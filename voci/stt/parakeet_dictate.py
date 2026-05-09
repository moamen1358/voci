from __future__ import annotations

import logging
import threading
import time

import numpy as np

from voci.stt._model import get_parakeet_model, get_target_sample_rate

log = logging.getLogger(__name__)


class DictateSTT:
    """Hold-to-talk STT via Parakeet.

    Drop-in replacement for the deleted ``voci.dictate_stt.DictateStreamingSTT``
    and ``voci.local_stt.LocalWhisperSTT``. Same lifecycle:

        ``start()`` once at app startup (warms the model)
        ``begin_session()`` on hotkey press
        ``send_audio(pcm)`` per mic frame while held
        ``end_session(max_wait_ms)`` on release → returns committed text
        ``stop()`` on app shutdown

    Strategy: buffer s16le PCM bytes during the session; on ``end_session``
    convert to float32 numpy and run a single batched ``model.transcribe``.
    Fast for short hold-to-talk clips and avoids any partial-stabilization
    machinery — we don't need partials here, just one clean final.

    Audio contract matches ``voci.mic_capture.MicStreamer`` — s16le 16 kHz mono.
    """

    SAMPLE_RATE = 16000

    def __init__(
        self,
        sample_rate: int = SAMPLE_RATE,
        language: str = "en",
        # ---- compat shims for ex-Deepgram callers (ignored) ----
        api_key: str | None = None,
        model: str | None = None,
        keywords: list[str] | None = None,
    ) -> None:
        if sample_rate != get_target_sample_rate():
            raise ValueError(
                f"Parakeet expects {get_target_sample_rate()} Hz audio, got {sample_rate}"
            )
        self.sample_rate = sample_rate
        self.language = language

        self._lock = threading.Lock()
        self._recording = False
        self._buffer = bytearray()
        self._model = None  # lazily loaded in start()

    def start(self) -> None:
        log.info("Warming up Parakeet model for dictation...")
        t0 = time.monotonic()
        self._model = get_parakeet_model()
        # Cheap warmup pass on a quarter-second of silence to JIT the kernels.
        try:
            warmup = np.zeros(self.SAMPLE_RATE // 4, dtype=np.float32)
            self._transcribe(warmup)
        except Exception:  # noqa: BLE001
            pass
        log.info("Parakeet dictate ready in %.1fs", time.monotonic() - t0)

    def stop(self) -> None:
        # Singleton model — don't unload. start() is idempotent.
        self._model = None

    # --------------------------------------------------------------------
    # Session API
    # --------------------------------------------------------------------

    def begin_session(self) -> None:
        with self._lock:
            self._recording = True
            self._buffer = bytearray()

    def send_audio(self, pcm: bytes) -> None:
        with self._lock:
            if self._recording:
                self._buffer.extend(pcm)

    def end_session(self, max_wait_ms: int = 0) -> str:
        # max_wait_ms is unused (no async finalize); accepted for interface
        # compat with the ex-Deepgram DictateStreamingSTT.
        with self._lock:
            self._recording = False
            audio_bytes = bytes(self._buffer)
            self._buffer = bytearray()
        if not audio_bytes or self._model is None:
            return ""
        samples = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if len(samples) < int(self.SAMPLE_RATE * 0.20):
            # Anything shorter than 200 ms is almost certainly an accidental tap.
            return ""
        t0 = time.monotonic()
        text = self._transcribe(samples)
        log.debug(
            "parakeet dictate transcribe %.0fms -> %r",
            (time.monotonic() - t0) * 1000,
            text[:60],
        )
        return text

    # --------------------------------------------------------------------
    # Internals
    # --------------------------------------------------------------------

    def _transcribe(self, audio: np.ndarray) -> str:
        import torch

        if self._model is None:
            return ""
        try:
            with torch.no_grad():
                hyps = self._model.transcribe(
                    [audio], batch_size=1, return_hypotheses=False, verbose=False
                )
        except Exception as e:  # noqa: BLE001
            log.warning("dictate transcribe failed: %s", e)
            return ""
        if not hyps:
            return ""
        first = hyps[0]
        if isinstance(first, str):
            return first.strip()
        text = getattr(first, "text", None) or getattr(first, "transcript", None) or ""
        return str(text).strip()
