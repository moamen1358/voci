from __future__ import annotations

import logging
import os
import queue
import threading
from collections.abc import Callable

import numpy as np

log = logging.getLogger(__name__)

OutputCallback = Callable[[str, str], None]


class SonioxStreamingTranscriber:
    """Soniox real-time STT over WebSocket. Sub-200 ms first-token latency.

    Same constructor + callback shape as the Parakeet/Deepgram transcribers
    so it slots into ``--stt-backend soniox`` without main.py knowing the
    difference.

    Soniox emits **token-level events** rather than full transcripts. We
    accumulate finalized tokens into the current utterance, treat the
    trailing tentative tokens as the live partial, and use Soniox's
    endpoint-detection ``<end>`` marker to flush a complete utterance.
    """

    SAMPLE_RATE = 16000

    def __init__(
        self,
        audio_queue: queue.Queue,
        on_text: OutputCallback,
        on_partial: OutputCallback | None = None,
        api_key: str | None = None,
        language: str = "en",
        model: str = "stt-rt-v4",
        sample_rate: int = SAMPLE_RATE,
        endpoint_max_delay_ms: int = 400,
        # Compat shims for ex-Deepgram callers (ignored)
        endpointing_ms: int | None = None,
        interim_results: bool | None = None,
        smart_format: bool | None = None,
        no_delay: bool | None = None,
        utterance_end_ms: int | None = None,
        process_interval: float | None = None,
        min_buffer_seconds: float | None = None,
    ) -> None:
        self.audio_queue = audio_queue
        self.on_text = on_text
        self.on_partial = on_partial
        self.sample_rate = sample_rate
        self.language = language
        self.model = model
        self.endpoint_max_delay_ms = endpoint_max_delay_ms

        self.api_key = api_key or os.environ.get("SONIOX_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "SONIOX_API_KEY not set. Get a free key at https://console.soniox.com/"
            )

        self._stop = threading.Event()
        self._sender_thread: threading.Thread | None = None
        self._receiver_thread: threading.Thread | None = None
        self._session = None
        self._session_cm = None  # the context manager wrapping the session

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        from soniox import SonioxClient
        from soniox.types import RealtimeSTTConfig

        client = SonioxClient(api_key=self.api_key)
        config = RealtimeSTTConfig(
            api_key=self.api_key,
            model=self.model,
            audio_format="s16le",
            num_channels=1,
            sample_rate=self.sample_rate,
            language_hints=[self.language],
            enable_endpoint_detection=True,
            max_endpoint_delay_ms=self.endpoint_max_delay_ms,
        )
        # The SDK exposes connect() as a context manager; enter it manually
        # so we control the lifetime from start()/stop() instead.
        self._session_cm = client.realtime.stt.connect(config=config)
        self._session = self._session_cm.__enter__()
        log.info(
            "Soniox session open (model=%s, language=%s, endpoint_max=%dms)",
            self.model,
            self.language,
            self.endpoint_max_delay_ms,
        )

        self._stop.clear()
        self._sender_thread = threading.Thread(
            target=self._sender_loop, name="soniox-sender", daemon=True
        )
        self._receiver_thread = threading.Thread(
            target=self._receiver_loop, name="soniox-receiver", daemon=True
        )
        self._sender_thread.start()
        self._receiver_thread.start()

    def stop(self) -> None:
        self._stop.set()
        # Send empty frame to gracefully close per Soniox docs
        try:
            if self._session is not None:
                self._session.send_bytes(b"")
                self._session.finish()
        except Exception:  # noqa: BLE001
            pass
        if self._sender_thread is not None:
            self._sender_thread.join(timeout=2.0)
        if self._receiver_thread is not None:
            self._receiver_thread.join(timeout=2.0)
        if self._session_cm is not None:
            try:
                self._session_cm.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        self._session = None
        self._session_cm = None

    # ------------------------------------------------------------------
    # Threads
    # ------------------------------------------------------------------

    def _sender_loop(self) -> None:
        """Pull float32 chunks from the queue, convert to s16le, push to WS."""
        while not self._stop.is_set():
            try:
                chunk = self.audio_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if chunk is None or len(chunk) == 0:
                continue
            try:
                pcm = np.clip(chunk.astype(np.float32, copy=False), -1.0, 1.0)
                pcm_bytes = (pcm * 32767.0).astype(np.int16).tobytes()
                if self._session is not None:
                    self._session.send_bytes(pcm_bytes)
            except Exception as e:  # noqa: BLE001
                log.warning("soniox send failed: %s", e)
                # The receiver will detect the close; let it reconnect or exit.
                break

    def _receiver_loop(self) -> None:
        """Consume token events; emit on_partial / on_text per Soniox semantics.

        Strategy:
          * ``committed`` accumulates finalized tokens for the current utterance.
          * Each event also gives us the trailing tentative tokens; the live
            partial is committed + tentative.
          * The special ``<end>`` token (emitted when endpoint detection
            triggers) closes the utterance: emit committed as a final and
            reset for the next one.
        """
        if self._session is None:
            return
        committed: list[str] = []
        tentative: list[str] = []
        try:
            for event in self._session.receive_events():
                if self._stop.is_set():
                    break
                if event.error_code:
                    log.error("soniox error %s: %s", event.error_code, event.error_message)
                    break
                if not event.tokens:
                    if event.finished:
                        break
                    continue

                tentative = []
                for tok in event.tokens:
                    text = tok.text or ""
                    if text == "<end>":
                        # Endpoint reached — flush committed as final.
                        full = "".join(committed).strip()
                        if full:
                            self.on_text(full, self.language)
                        committed = []
                        tentative = []
                        continue
                    if tok.is_final:
                        committed.append(text)
                    else:
                        tentative.append(text)

                if self.on_partial is not None:
                    live = ("".join(committed) + "".join(tentative)).strip()
                    if live:
                        self.on_partial(live, self.language)

                if event.finished:
                    break
        except Exception as e:  # noqa: BLE001
            if not self._stop.is_set():
                log.warning("soniox receiver ended: %s", e)
