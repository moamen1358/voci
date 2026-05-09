from __future__ import annotations

import logging
import os
import queue
import threading
from typing import Callable

import numpy as np

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveOptions,
    LiveTranscriptionEvents,
)

log = logging.getLogger(__name__)

OutputCallback = Callable[[str, str], None]


class StreamingTranscriber:
    """Deepgram Nova-3 streaming STT over WebSocket.

    Pulls float32 16 kHz audio chunks from `audio_queue`, encodes them to
    16-bit PCM, and pushes them to Deepgram's live transcription socket.

    Emits:
      - partial text via `on_partial(text, lang)` — interim transcripts that may change
      - final committed text via `on_text(text, lang)` — utterance-final transcripts
    """

    SAMPLE_RATE = 16000

    def __init__(
        self,
        audio_queue: queue.Queue[np.ndarray],
        on_text: OutputCallback,
        on_partial: OutputCallback | None = None,
        api_key: str | None = None,
        language: str = "en",
        model: str = "nova-2",  # smaller/faster than nova-3
        sample_rate: int = SAMPLE_RATE,
        endpointing_ms: int = 25,  # commit nearly instantly on any silence
        interim_results: bool = True,
        smart_format: bool = False,
        no_delay: bool = True,
        utterance_end_ms: int = 1000,
        # Compat shims
        process_interval: float | None = None,
        min_buffer_seconds: float | None = None,
    ) -> None:
        self.audio_queue = audio_queue
        self.on_text = on_text
        self.on_partial = on_partial
        self.sample_rate = sample_rate
        self.language = language
        self.model = model
        self.endpointing_ms = endpointing_ms
        self.interim_results = interim_results
        self.smart_format = smart_format
        self.no_delay = no_delay
        self.utterance_end_ms = utterance_end_ms

        key = api_key or os.environ.get("DEEPGRAM_API_KEY")
        if not key:
            raise RuntimeError(
                "DEEPGRAM_API_KEY not set. Get a key from https://console.deepgram.com/"
            )
        self._client = DeepgramClient(
            key, DeepgramClientOptions(options={"keepalive": "true"})
        )
        self._connection = None
        self._stop = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._connected = threading.Event()

    # ---------- event handlers wired to the websocket ----------

    def _on_open(self, *_args, **_kwargs):
        log.info("Deepgram socket open (model=%s, language=%s)", self.model, self.language)
        self._connected.set()

    def _on_message(self, *_args, result, **_kwargs):
        try:
            transcript = result.channel.alternatives[0].transcript
            if not transcript:
                return
            detected_lang = (
                getattr(result.channel.alternatives[0], "languages", None)
                or [self.language if self.language != "multi" else "en"]
            )[0]
            if result.is_final:
                self.on_text(transcript, detected_lang)
            else:
                if self.on_partial is not None:
                    self.on_partial(transcript, detected_lang)
        except Exception as e:
            log.exception("Deepgram message handler failed: %s", e)

    def _on_error(self, *_args, error=None, **_kwargs):
        log.error("Deepgram error: %s", error)

    def _on_close(self, *_args, **_kwargs):
        log.info("Deepgram socket closed")
        self._connected.clear()

    # ---------- main I/O loop ----------

    def _open_connection(self) -> None:
        conn = self._client.listen.websocket.v("1")
        conn.on(LiveTranscriptionEvents.Open, self._on_open)
        conn.on(LiveTranscriptionEvents.Transcript, self._on_message)
        conn.on(LiveTranscriptionEvents.Error, self._on_error)
        conn.on(LiveTranscriptionEvents.Close, self._on_close)

        opts = LiveOptions(
            model=self.model,
            language=self.language,
            encoding="linear16",
            sample_rate=self.sample_rate,
            channels=1,
            interim_results=self.interim_results,
            smart_format=self.smart_format,
            endpointing=self.endpointing_ms,
            utterance_end_ms=self.utterance_end_ms,
            vad_events=True,
            punctuate=True,
            no_delay=self.no_delay,
        )
        if not conn.start(opts):
            raise RuntimeError("Failed to start Deepgram connection")
        self._connection = conn
        # Wait briefly for the open event
        self._connected.wait(timeout=4.0)

    def _worker(self) -> None:
        try:
            self._open_connection()
        except Exception as e:
            log.exception("Could not open Deepgram connection: %s", e)
            return
        try:
            while not self._stop.is_set():
                try:
                    chunk = self.audio_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                if chunk is None or len(chunk) == 0:
                    continue
                # float32 [-1.0, 1.0] -> int16 little-endian PCM
                pcm = np.clip(chunk, -1.0, 1.0)
                pcm = (pcm * 32767.0).astype(np.int16).tobytes()
                try:
                    self._connection.send(pcm)
                except Exception as e:
                    log.error("send failed, reconnecting: %s", e)
                    try:
                        self._open_connection()
                    except Exception:
                        break
        finally:
            try:
                if self._connection is not None:
                    self._connection.finish()
            except Exception:
                pass

    def start(self) -> None:
        self._stop.clear()
        self._worker_thread = threading.Thread(target=self._worker, name="deepgram-stt", daemon=True)
        self._worker_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=3.0)
