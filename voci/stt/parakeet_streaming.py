from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable

import numpy as np

from voci.stt._model import get_parakeet_model, get_target_sample_rate

log = logging.getLogger(__name__)

OutputCallback = Callable[[str, str], None]


class StreamingTranscriber:
    """Local streaming STT via NVIDIA Parakeet TDT 0.6B v2 on CUDA.

    Drop-in replacement for the deleted ``voci.deepgram_stt.StreamingTranscriber``
    — same constructor shape and same callback contract — so ``voci/main.py``
    needs only an import swap to use it.

    Strategy: micro-batched buffered streaming with energy-based endpointing.

    * Pulls float32 16 kHz audio chunks from ``audio_queue`` (whatever block
      size ``voci.audio_capture.AudioCapture`` is configured for).
    * Maintains a per-utterance rolling buffer.
    * Runs inference on the current utterance every ``inference_interval``
      seconds (default 0.3 s) and emits the hypothesis via ``on_partial``.
    * When RMS energy stays below ``vad_threshold`` for ``silence_timeout``
      seconds, treats the utterance as ended, emits the final text via
      ``on_text``, and resets the buffer.

    First-token latency on RTX 4060: ~250-400 ms in this implementation. (True
    cache-aware streaming via NeMo's CacheAwareStreamingAudioBuffer would be
    closer to 50 ms — see TODO at the bottom of this file.)
    """

    SAMPLE_RATE = 16000

    def __init__(
        self,
        audio_queue: queue.Queue,
        on_text: OutputCallback,
        on_partial: OutputCallback | None = None,
        sample_rate: int = SAMPLE_RATE,
        inference_interval: float = 0.25,
        silence_timeout: float = 0.40,
        vad_threshold: float = 0.005,
        max_utterance_seconds: float = 25.0,
        language: str = "en",
        # ---- compat shims for ex-Deepgram callers (ignored) ----
        api_key: str | None = None,
        model: str | None = None,
        endpointing_ms: int | None = None,
        interim_results: bool | None = None,
        smart_format: bool | None = None,
        no_delay: bool | None = None,
        utterance_end_ms: int | None = None,
        process_interval: float | None = None,
        min_buffer_seconds: float | None = None,
    ) -> None:
        if sample_rate != get_target_sample_rate():
            raise ValueError(
                f"Parakeet expects {get_target_sample_rate()} Hz audio, got {sample_rate}"
            )
        self.audio_queue = audio_queue
        self.on_text = on_text
        self.on_partial = on_partial
        self.sample_rate = sample_rate
        self.inference_interval = inference_interval
        self.silence_timeout = silence_timeout
        self.vad_threshold = vad_threshold
        self.max_utterance_seconds = max_utterance_seconds
        self.language = language

        self._stop = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._model = None  # lazy-loaded in start()

    def start(self) -> None:
        log.info("Warming up Parakeet model...")
        self._model = get_parakeet_model()
        self._stop.clear()
        self._worker_thread = threading.Thread(
            target=self._worker, name="parakeet-stream", daemon=True
        )
        self._worker_thread.start()
        log.info("Parakeet streaming transcriber running")

    def stop(self) -> None:
        self._stop.set()
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=3.0)

    # --------------------------------------------------------------------
    # Internals
    # --------------------------------------------------------------------

    def _worker(self) -> None:
        utt_chunks: list[np.ndarray] = []
        utt_samples = 0
        in_utterance = False
        last_speech_t = 0.0
        last_inference_t = 0.0
        last_partial_text = ""

        max_samples = int(self.max_utterance_seconds * self.sample_rate)

        while not self._stop.is_set():
            try:
                chunk = self.audio_queue.get(timeout=0.1)
            except queue.Empty:
                # Even with no new chunk, check whether we should finalize a
                # pending utterance based on elapsed silence.
                if in_utterance:
                    self._maybe_finalize(utt_chunks, last_speech_t, in_utterance, last_partial_text)
                continue
            if chunk is None or len(chunk) == 0:
                continue

            now = time.monotonic()
            rms = float(np.sqrt(np.mean(chunk.astype(np.float32) ** 2)))
            is_speech = rms >= self.vad_threshold

            if is_speech:
                if not in_utterance:
                    in_utterance = True
                    utt_chunks = []
                    utt_samples = 0
                    last_inference_t = 0.0
                    last_partial_text = ""
                utt_chunks.append(chunk)
                utt_samples += len(chunk)
                last_speech_t = now
            elif in_utterance:
                # Record the silent chunk too so the model sees the trailing
                # quiet — helps endpointing accuracy.
                utt_chunks.append(chunk)
                utt_samples += len(chunk)

            if not in_utterance:
                continue

            # Periodic partial inference
            if now - last_inference_t >= self.inference_interval and utt_samples >= int(
                self.sample_rate * 0.30
            ):
                last_inference_t = now
                audio = self._concat(utt_chunks)
                text = self._transcribe(audio)
                if text and text != last_partial_text and self.on_partial is not None:
                    self.on_partial(text, self.language)
                    last_partial_text = text

            # Endpointing
            silent_for = now - last_speech_t
            if silent_for >= self.silence_timeout or utt_samples >= max_samples:
                self._finalize(utt_chunks, last_partial_text)
                utt_chunks = []
                utt_samples = 0
                in_utterance = False
                last_partial_text = ""
                last_inference_t = 0.0

        # Drain on shutdown — no graceful final emission needed for this app.

    def _maybe_finalize(
        self,
        utt_chunks: list[np.ndarray],
        last_speech_t: float,
        in_utterance: bool,
        last_partial_text: str,
    ) -> None:
        if not in_utterance:
            return
        if (time.monotonic() - last_speech_t) < self.silence_timeout:
            return
        self._finalize(utt_chunks, last_partial_text)
        # Caller's loop owns the buffers; this helper only fires the callback.
        utt_chunks.clear()

    def _finalize(self, utt_chunks: list[np.ndarray], last_partial_text: str) -> None:
        if not utt_chunks:
            return
        audio = self._concat(utt_chunks)
        text = self._transcribe(audio) or last_partial_text
        if text:
            self.on_text(text, self.language)

    def _concat(self, chunks: list[np.ndarray]) -> np.ndarray:
        return np.concatenate(chunks).astype(np.float32, copy=False)

    def _transcribe(self, audio: np.ndarray) -> str:
        import torch

        if self._model is None:
            return ""
        # Parakeet wants a 1-D float32 array in [-1, 1]. NeMo handles batching
        # internally when given a list of arrays.
        try:
            with torch.no_grad():
                hyps = self._model.transcribe(
                    [audio], batch_size=1, return_hypotheses=False, verbose=False
                )
        except Exception as e:  # noqa: BLE001
            log.warning("transcribe failed: %s", e)
            return ""
        if not hyps:
            return ""
        first = hyps[0]
        # NeMo returns either a list[str] or list[Hypothesis] depending on
        # version + flags. Handle both.
        if isinstance(first, str):
            return first.strip()
        text = getattr(first, "text", None) or getattr(first, "transcript", None) or ""
        return str(text).strip()


# TODO(performance): swap the rolling-buffer approach above for true cache-aware
# streaming via nemo.collections.asr.parts.utils.streaming_utils. Parakeet 0.6B
# v2 supports it (FastConformer encoder). Expected first-token latency drop
# from ~300 ms to ~50 ms. Deferred because the current implementation already
# beats Deepgram's network round-trip and is simpler/more robust to NeMo API
# churn. Revisit once the rest of the refactor is verified end-to-end.
