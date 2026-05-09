from __future__ import annotations

import logging
import os
import threading
import time

from deepgram import (
    DeepgramClient,
    DeepgramClientOptions,
    LiveOptions,
    LiveTranscriptionEvents,
)

log = logging.getLogger(__name__)


class DictateStreamingSTT:
    """Persistent Deepgram WebSocket for hold-to-talk dictation.

    The connection is opened once at app start and stays alive (with keepalive
    pings) across many F9 presses. Each "session" is bounded by
    `begin_session()` / `end_session()`:

      begin_session() — clears finals, marks recording=True
      send_audio(pcm) — forwards PCM frames to Deepgram while recording
      end_session()   — sets recording=False, sends Finalize control message,
                        waits briefly for any trailing final transcript, returns
                        the concatenated text.

    Saves ~200-400 ms vs the REST one-shot path because there's no TCP/TLS
    handshake on every press, and audio is already most-of-the-way transcribed
    by the time the user releases the key.
    """

    def __init__(
        self,
        api_key: str | None = None,
        sample_rate: int = 16000,
        model: str = "nova-2",
        language: str = "en",
        keywords: list[str] | None = None,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPGRAM_API_KEY")
        if not self.api_key:
            raise RuntimeError("DEEPGRAM_API_KEY not set")
        self.sample_rate = sample_rate
        self.model = model
        self.language = language
        self.keywords = keywords or []

        self._client = DeepgramClient(
            self.api_key, DeepgramClientOptions(options={"keepalive": "true"})
        )
        self._conn = None
        self._connected = threading.Event()

        self._lock = threading.Lock()
        self._recording = False
        self._session_finals: list[str] = []
        self._final_arrived = threading.Event()

    def start(self) -> None:
        conn = self._client.listen.websocket.v("1")
        conn.on(LiveTranscriptionEvents.Open, self._on_open)
        conn.on(LiveTranscriptionEvents.Transcript, self._on_message)
        conn.on(LiveTranscriptionEvents.Close, self._on_close)
        conn.on(LiveTranscriptionEvents.Error, self._on_error)

        kw = {}
        if self.keywords:
            kw["keywords"] = self.keywords
        opts = LiveOptions(
            model=self.model,
            language=self.language,
            encoding="linear16",
            sample_rate=self.sample_rate,
            channels=1,
            interim_results=False,
            smart_format=True,
            punctuate=True,
            endpointing=25,
            no_delay=True,
            **kw,
        )
        if not conn.start(opts):
            raise RuntimeError("Failed to start Deepgram WebSocket")
        self._conn = conn
        self._connected.wait(timeout=4.0)
        log.info("Deepgram dictate socket open")

    def stop(self) -> None:
        if self._conn is not None:
            try:
                self._conn.finish()
            except Exception:
                pass
            self._conn = None

    def begin_session(self) -> None:
        with self._lock:
            self._recording = True
            self._session_finals = []
            self._final_arrived.clear()

    def send_audio(self, pcm: bytes) -> None:
        if not self._connected.is_set():
            return
        with self._lock:
            if not self._recording:
                return
        try:
            self._conn.send(pcm)
        except Exception as e:
            log.debug("send failed: %s", e)

    def end_session(self, max_wait_ms: int = 350) -> str:
        with self._lock:
            self._recording = False
        try:
            if self._conn is not None and hasattr(self._conn, "finalize"):
                self._conn.finalize()
        except Exception as e:
            log.debug("finalize failed: %s", e)
        self._final_arrived.wait(timeout=max_wait_ms / 1000.0)
        time.sleep(0.03)
        with self._lock:
            return " ".join(self._session_finals).strip()

    def _on_open(self, *_args, **_kwargs) -> None:
        self._connected.set()

    def _on_close(self, *_args, **_kwargs) -> None:
        self._connected.clear()
        log.info("Deepgram socket closed")

    def _on_error(self, *_args, error=None, **_kwargs) -> None:
        log.error("Deepgram error: %s", error)

    def _on_message(self, *_args, result=None, **_kwargs) -> None:
        if result is None:
            return
        try:
            transcript = result.channel.alternatives[0].transcript
        except (AttributeError, IndexError):
            return
        if not transcript:
            return
        if not getattr(result, "is_final", False):
            return
        with self._lock:
            if self._recording:
                self._session_finals.append(transcript.strip())
            else:
                self._session_finals.append(transcript.strip())
                self._final_arrived.set()
