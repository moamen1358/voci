from __future__ import annotations

import logging
import os
import queue
import threading
from collections.abc import Callable

import numpy as np

log = logging.getLogger(__name__)

OutputCallback = Callable[[str, str], None]


class AssemblyAIStreamingTranscriber:
    """AssemblyAI Universal-Streaming v3 over WebSocket.

    Drop-in replacement for the Parakeet/Deepgram transcribers — same
    constructor + callback shape, so it slots into ``--stt-backend
    assemblyai`` without any caller awareness.

    Universal-Streaming's defining feature is **immutable finals**: once
    ``end_of_turn`` arrives for a transcript, the model never revises that
    text. Combined with the existing monotonic provisional rendering in
    voci/main.py, this means once a word is on screen it stays put — no
    flicker.

    Audio contract: float32 16 kHz mono frames pulled from ``audio_queue``,
    converted to s16le PCM (Universal-Streaming's required encoding) and
    streamed in via the SDK's iterator-of-bytes protocol.
    """

    SAMPLE_RATE = 16000

    def __init__(
        self,
        audio_queue: queue.Queue,
        on_text: OutputCallback,
        on_partial: OutputCallback | None = None,
        api_key: str | None = None,
        language: str = "en",
        sample_rate: int = SAMPLE_RATE,
        # Aggressive endpointing — AssemblyAI defaults wait ~400-500 ms of
        # silence before declaring end_of_turn, which compounds with the
        # translation round-trip to make committed Arabic appear ~800 ms
        # after you stop talking. These trim that to ~250 ms total. Tune
        # higher if commits fire mid-sentence on natural breath pauses.
        min_end_of_turn_silence_when_confident: int = 160,
        max_turn_silence: int = 700,
        end_of_turn_confidence_threshold: float = 0.4,
        # Compat shims for ex-Deepgram callers (ignored)
        model: str | None = None,
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
        self.min_end_of_turn_silence_when_confident = min_end_of_turn_silence_when_confident
        self.max_turn_silence = max_turn_silence
        self.end_of_turn_confidence_threshold = end_of_turn_confidence_threshold

        self.api_key = api_key or os.environ.get("ASSEMBLYAI_API_KEY")
        if not self.api_key:
            raise RuntimeError(
                "ASSEMBLYAI_API_KEY not set. Get a free key at https://www.assemblyai.com/"
            )

        self._stop = threading.Event()
        self._client = None  # set in start()
        self._stream_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        from assemblyai.streaming.v3 import (
            StreamingClient,
            StreamingClientOptions,
            StreamingEvents,
            StreamingParameters,
        )
        from assemblyai.streaming.v3.models import SpeechModel

        self._client = StreamingClient(
            StreamingClientOptions(
                api_key=self.api_key,
                api_host="streaming.assemblyai.com",
            )
        )
        self._client.on(StreamingEvents.Turn, self._on_turn)
        self._client.on(StreamingEvents.Error, self._on_error)
        self._client.on(StreamingEvents.Termination, self._on_terminated)

        # universal_streaming_english is the dedicated English-only model;
        # use universal_streaming_multilingual if non-English is ever needed.
        speech_model = (
            SpeechModel.universal_streaming_english
            if self.language == "en"
            else SpeechModel.universal_streaming_multilingual
        )

        self._client.connect(
            StreamingParameters(
                sample_rate=self.sample_rate,
                speech_model=speech_model,
                format_turns=True,
                include_partial_turns=True,
                min_end_of_turn_silence_when_confident=self.min_end_of_turn_silence_when_confident,
                max_turn_silence=self.max_turn_silence,
                end_of_turn_confidence_threshold=self.end_of_turn_confidence_threshold,
            )
        )
        log.info(
            "AssemblyAI streaming client connected "
            "(sample_rate=%d, min_silence=%dms, max_silence=%dms, conf_thresh=%.2f)",
            self.sample_rate,
            self.min_end_of_turn_silence_when_confident,
            self.max_turn_silence,
            self.end_of_turn_confidence_threshold,
        )

        # client.stream() blocks pulling chunks from the iterator we hand it,
        # so run it in a worker thread.
        self._stop.clear()
        self._stream_thread = threading.Thread(
            target=self._stream_loop, name="assemblyai-stream", daemon=True
        )
        self._stream_thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._client is not None:
            try:
                self._client.disconnect(terminate=True)
            except Exception:  # noqa: BLE001
                pass
        if self._stream_thread is not None:
            self._stream_thread.join(timeout=2.0)
        self._client = None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    # AssemblyAI rejects chunks shorter than 50 ms or longer than 1000 ms.
    # voci.audio_capture commonly emits 25 ms blocks (cfg.block_seconds=0.025),
    # so we coalesce into ~100 ms chunks before yielding — comfortably above
    # the floor and short enough to keep latency low.
    _OUTPUT_CHUNK_MS = 100
    _OUTPUT_BYTES = SAMPLE_RATE * 2 * _OUTPUT_CHUNK_MS // 1000  # s16le bytes/chunk

    def _audio_iterator(self):
        """Generator the AssemblyAI SDK consumes via ``client.stream(iter)``.
        Yields ~100 ms s16le PCM chunks coalesced from the float32 queue."""
        buf = bytearray()
        while not self._stop.is_set():
            try:
                chunk = self.audio_queue.get(timeout=0.1)
            except queue.Empty:
                # Flush whatever we have so the stream stays alive even
                # during brief silences (still gates on the 50 ms minimum).
                if len(buf) >= self._OUTPUT_BYTES // 2:
                    yield bytes(buf)
                    buf = bytearray()
                continue
            if chunk is None or len(chunk) == 0:
                continue
            try:
                pcm = np.clip(chunk.astype(np.float32, copy=False), -1.0, 1.0)
                buf.extend((pcm * 32767.0).astype(np.int16).tobytes())
            except Exception as e:  # noqa: BLE001
                log.warning("audio convert failed: %s", e)
                continue
            while len(buf) >= self._OUTPUT_BYTES:
                yield bytes(buf[: self._OUTPUT_BYTES])
                del buf[: self._OUTPUT_BYTES]

    def _stream_loop(self) -> None:
        try:
            assert self._client is not None
            self._client.stream(self._audio_iterator())
        except Exception as e:  # noqa: BLE001
            if not self._stop.is_set():
                log.warning("AssemblyAI stream ended: %s", e)

    # The SDK's .on(Event, handler) passes the StreamingClient as the first
    # positional arg, then the typed event. We use _client_ as the var name
    # so it's clear it isn't 'self'.

    def _on_turn(self, _client, event) -> None:
        text = (event.transcript or "").strip()
        if not text:
            return
        if event.end_of_turn:
            # Immutable final — Universal-Streaming guarantees this won't be
            # revised. Emit as on_text so the consumer can commit it.
            self.on_text(text, self.language)
        else:
            # Partial — may grow but won't shrink before end_of_turn.
            if self.on_partial is not None:
                self.on_partial(text, self.language)

    def _on_error(self, _client, error) -> None:
        log.error("AssemblyAI error: %s", error)

    def _on_terminated(self, _client, event) -> None:
        log.info(
            "AssemblyAI session terminated (audio_duration=%ss)",
            getattr(event, "audio_duration_seconds", "?"),
        )
