#!/usr/bin/env python3
"""Benchmark local Parakeet STT first-token + finalize latency.

For each run we:
  1. Push a fresh `begin_session()` on the dictate facade
  2. Stream the audio file in 25 ms PCM frames at real-time pacing
  3. Measure how long `end_session()` takes (finalize latency from key-up POV)

The streaming facade is also exercised against the same audio with a callback
that timestamps the first emitted partial — that's our "first-token latency".

Usage:
    uv run python scripts/bench_parakeet.py [audio.wav]

Output: stdout summary + ~/.cache/voci/bench/parakeet-<ts>.csv (p50/p95/p99).
"""

from __future__ import annotations

import csv
import queue
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import voci  # noqa: F401  (CUDA preload)
from voci.stt import DictateSTT, StreamingTranscriber

FRAME_MS = 25
SAMPLE_RATE = 16000
FRAME_BYTES = SAMPLE_RATE * FRAME_MS // 1000 * 2  # s16le bytes/frame
FLOAT_BLOCK_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # float32 samples/frame


def load_audio(path: Path) -> tuple[bytes, np.ndarray]:
    """Return both s16le bytes (for dictate) and float32 array (for streaming)."""
    audio, sr = sf.read(str(path), dtype="float32")
    if audio.ndim > 1:
        audio = audio[:, 0]
    if sr != SAMPLE_RATE:
        from scipy.signal import resample_poly  # type: ignore

        audio = resample_poly(audio, SAMPLE_RATE, sr)
    audio = np.clip(audio, -1.0, 1.0).astype(np.float32)
    pcm = (audio * 32767.0).astype(np.int16).tobytes()
    return pcm, audio


def bench_dictate(pcm: bytes, runs: int) -> list[float]:
    """Return finalize_ms for each run (key-up to text-returned)."""
    print("\n=== DictateSTT (Parakeet, hold-to-talk) ===")
    print("  warm-up (loads model, runs throwaway transcribe)...")
    stt = DictateSTT(language="en")
    stt.start()
    # Warm-up
    stt.begin_session()
    stt.send_audio(pcm[: SAMPLE_RATE * 2 * 2])  # 2s
    _ = stt.end_session()
    print("  warm-up done.")

    times: list[float] = []
    for r in range(runs):
        stt.begin_session()
        # Push the entire clip "instantly" — measures pure inference time, not
        # streaming. (For real-time streaming feel, use bench_streaming below.)
        stt.send_audio(pcm)
        t0 = time.monotonic()
        text = stt.end_session()
        dt_ms = (time.monotonic() - t0) * 1000
        times.append(dt_ms)
        print(f"  run {r + 1}: finalize {dt_ms:6.0f}ms — {text[:80]!r}")
    stt.stop()
    return times


def _bench_one_streaming_run(audio: np.ndarray, warmup: bool) -> tuple[float, float]:
    """Single run; isolated scope so closures bind cleanly."""
    q: queue.Queue = queue.Queue(maxsize=512)
    speech_start = [0.0]
    first_partial = [0.0]
    first_final = [0.0]

    def on_partial(_text: str, _lang: str) -> None:
        if first_partial[0] == 0.0:
            first_partial[0] = (time.monotonic() - speech_start[0]) * 1000

    def on_text(_text: str, _lang: str) -> None:
        if first_final[0] == 0.0:
            first_final[0] = (time.monotonic() - speech_start[0]) * 1000

    st = StreamingTranscriber(
        audio_queue=q,
        on_text=on_text,
        on_partial=on_partial,
        sample_rate=SAMPLE_RATE,
    )
    st.start()
    if warmup:
        time.sleep(2.0)

    n_blocks = (len(audio) + FLOAT_BLOCK_SAMPLES - 1) // FLOAT_BLOCK_SAMPLES
    next_send = time.monotonic()
    speech_start[0] = next_send
    for i in range(n_blocks):
        block = audio[i * FLOAT_BLOCK_SAMPLES : (i + 1) * FLOAT_BLOCK_SAMPLES]
        q.put(block.copy())
        next_send += FRAME_MS / 1000.0
        now = time.monotonic()
        if next_send > now:
            time.sleep(next_send - now)

    deadline = time.monotonic() + 4.0
    while first_final[0] == 0.0 and time.monotonic() < deadline:
        time.sleep(0.05)
    st.stop()

    partial_ms = first_partial[0] or float("nan")
    final_ms = first_final[0] or float("nan")
    return partial_ms, final_ms


def bench_streaming(audio: np.ndarray, runs: int) -> list[tuple[float, float]]:
    """Stream audio in real time; return (first_partial_ms, first_final_ms) per run."""
    print("\n=== StreamingTranscriber (Parakeet, overlay mode) ===")
    results: list[tuple[float, float]] = []
    for r in range(runs):
        partial_ms, final_ms = _bench_one_streaming_run(audio, warmup=(r == 0))
        results.append((partial_ms, final_ms))
        print(f"  run {r + 1}: first partial {partial_ms:6.0f}ms, first final {final_ms:6.0f}ms")
    return results


def percentiles(xs: list[float]) -> dict[str, float]:
    xs = [x for x in xs if not (x != x)]  # drop NaN
    if not xs:
        return {"p50": float("nan"), "p95": float("nan"), "p99": float("nan")}
    xs_sorted = sorted(xs)
    return {
        "p50": statistics.median(xs_sorted),
        "p95": xs_sorted[max(0, int(len(xs_sorted) * 0.95) - 1)],
        "p99": xs_sorted[max(0, int(len(xs_sorted) * 0.99) - 1)],
    }


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/english_probe.wav")
    if not path.exists():
        print(f"audio file not found: {path}", file=sys.stderr)
        print("Generate one with: scripts/capture_probe.py", file=sys.stderr)
        return 1

    print(f"Loading audio: {path}")
    pcm, audio = load_audio(path)
    audio_secs = len(audio) / SAMPLE_RATE
    print(f"  duration: {audio_secs:.2f}s @ {SAMPLE_RATE} Hz")

    runs = 5
    dictate_times = bench_dictate(pcm, runs)
    streaming = bench_streaming(audio, runs)
    streaming_partial = [s[0] for s in streaming]
    streaming_final = [s[1] for s in streaming]

    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    d = percentiles(dictate_times)
    sp = percentiles(streaming_partial)
    sf_ = percentiles(streaming_final)
    print(
        f"  Dictate finalize  : p50 {d['p50']:6.0f}ms  p95 {d['p95']:6.0f}ms  p99 {d['p99']:6.0f}ms"
    )
    print(
        f"  Stream 1st partial: p50 {sp['p50']:6.0f}ms  p95 {sp['p95']:6.0f}ms  p99 {sp['p99']:6.0f}ms"
    )
    print(
        f"  Stream 1st final  : p50 {sf_['p50']:6.0f}ms  p95 {sf_['p95']:6.0f}ms  p99 {sf_['p99']:6.0f}ms"
    )

    # Persist to CSV for trend tracking
    out_dir = Path.home() / ".cache" / "voci" / "bench"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"parakeet-{datetime.now().strftime('%Y%m%dT%H%M%S')}.csv"
    with out_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "p50_ms", "p95_ms", "p99_ms", "n_runs", "audio_seconds"])
        w.writerow(["dictate_finalize", d["p50"], d["p95"], d["p99"], runs, audio_secs])
        w.writerow(["stream_first_partial", sp["p50"], sp["p95"], sp["p99"], runs, audio_secs])
        w.writerow(["stream_first_final", sf_["p50"], sf_["p95"], sf_["p99"], runs, audio_secs])
    print(f"\n  CSV: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
