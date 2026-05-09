#!/usr/bin/env python3
"""Benchmark Deepgram vs local-Whisper dictate backends on identical audio.

For each backend, we:
  1. start() it (model load / WebSocket open)
  2. begin_session()
  3. stream the audio file in 25 ms PCM frames at real-time pacing
  4. measure how long end_session() takes from key-up perspective

Usage:
    .venv/bin/python scripts/bench_dictate_backends.py [/tmp/english_probe.wav]
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import voci  # noqa: F401  (CUDA preload)
from voci.dictate_stt import DictateStreamingSTT
from voci.local_stt import LocalWhisperSTT


FRAME_MS = 25
SAMPLE_RATE = 16000
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2  # s16le


def load_pcm_s16le(path: Path) -> bytes:
    audio, sr = sf.read(str(path), dtype="float32")
    if audio.ndim > 1:
        audio = audio[:, 0]
    if sr != SAMPLE_RATE:
        # Resample if needed
        from scipy.signal import resample_poly  # type: ignore
        audio = resample_poly(audio, SAMPLE_RATE, sr)
    pcm = np.clip(audio, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    return pcm.tobytes()


def stream_audio(stt, pcm: bytes, realtime: bool) -> float:
    """Push audio in 25 ms frames; return wallclock seconds spent streaming."""
    t0 = time.monotonic()
    frame_dt = FRAME_MS / 1000.0
    n_frames = (len(pcm) + FRAME_BYTES - 1) // FRAME_BYTES
    next_send = time.monotonic()
    for i in range(n_frames):
        chunk = pcm[i * FRAME_BYTES : (i + 1) * FRAME_BYTES]
        stt.send_audio(chunk)
        if realtime:
            next_send += frame_dt
            now = time.monotonic()
            if next_send > now:
                time.sleep(next_send - now)
    return time.monotonic() - t0


def bench_backend(name: str, stt, pcm: bytes, runs: int) -> dict:
    print(f"\n=== {name} ===")
    print(f"  warm-up...")
    stt.start()
    # Warm-up run (not counted)
    stt.begin_session()
    stream_audio(stt, pcm[: SAMPLE_RATE * 2], realtime=False)
    _ = stt.end_session(max_wait_ms=1000) if isinstance(stt, DictateStreamingSTT) else stt.end_session()
    print(f"  warm-up done.")

    finalize_times = []
    transcripts = []
    for r in range(runs):
        stt.begin_session()
        stream_secs = stream_audio(stt, pcm, realtime=True)
        t_finalize_start = time.monotonic()
        if isinstance(stt, DictateStreamingSTT):
            text = stt.end_session(max_wait_ms=600)
        else:
            text = stt.end_session()
        finalize_ms = (time.monotonic() - t_finalize_start) * 1000
        finalize_times.append(finalize_ms)
        transcripts.append(text)
        print(f"  run {r+1}: streamed {stream_secs:.2f}s, finalize {finalize_ms:.0f}ms")
        print(f"          transcript: {text[:100]!r}")

    return {
        "name": name,
        "finalize_ms_avg": sum(finalize_times) / len(finalize_times),
        "finalize_ms_min": min(finalize_times),
        "finalize_ms_max": max(finalize_times),
        "transcripts": transcripts,
    }


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/english_probe.wav")
    if not path.exists():
        print(f"audio file not found: {path}", file=sys.stderr)
        return 1

    print(f"Loading audio: {path}")
    pcm = load_pcm_s16le(path)
    audio_secs = len(pcm) / 2 / SAMPLE_RATE
    print(f"  duration: {audio_secs:.2f}s, {len(pcm)} bytes s16le @ {SAMPLE_RATE} Hz")

    if not os.environ.get("DEEPGRAM_API_KEY"):
        print("WARN: DEEPGRAM_API_KEY not set — cloud bench will fail", file=sys.stderr)

    runs = 3

    # Deepgram first (faster startup)
    cloud = DictateStreamingSTT(language="en")
    cloud_result = bench_backend("Deepgram Nova-2 (cloud)", cloud, pcm, runs)
    cloud.stop()

    # Local Whisper
    local = LocalWhisperSTT(language="en")
    local_result = bench_backend("Whisper-large-v3-turbo (local CUDA)", local, pcm, runs)
    local.stop()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY (lower finalize_ms is better)")
    print("=" * 60)
    for r in (cloud_result, local_result):
        print(f"  {r['name']}")
        print(f"    finalize avg = {r['finalize_ms_avg']:.0f}ms (min {r['finalize_ms_min']:.0f}, max {r['finalize_ms_max']:.0f})")

    diff = cloud_result["finalize_ms_avg"] - local_result["finalize_ms_avg"]
    if diff > 20:
        print(f"\n  → LOCAL is faster by {diff:.0f}ms on average")
    elif diff < -20:
        print(f"\n  → CLOUD is faster by {-diff:.0f}ms on average")
    else:
        print(f"\n  → Effectively tied (within {abs(diff):.0f}ms)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
