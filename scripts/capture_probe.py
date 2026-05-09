#!/usr/bin/env python3
"""Capture N seconds of system audio from the configured monitor source and save to WAV.

Usage:
    .venv/bin/python scripts/capture_probe.py [seconds] [out_path]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voci.audio_capture import AudioCapture
from voci.config import AppConfig


def main() -> int:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
    out_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("/tmp/probe.wav")

    cfg = AppConfig.load()
    if not cfg.monitor_source:
        print("ERROR: no monitor source. Run scripts/list_audio_sources.py first.", file=sys.stderr)
        return 2

    print(f"Capturing {seconds:.1f}s from {cfg.monitor_source} ...")
    print("(make sure something is playing audio)")
    cap = AudioCapture(monitor_source=cfg.monitor_source, sample_rate=cfg.sample_rate, block_seconds=0.25)
    cap.start()
    time.sleep(0.6)

    deadline = time.monotonic() + seconds
    chunks = []
    while time.monotonic() < deadline:
        try:
            chunk = cap.audio_queue.get(timeout=0.5)
            chunks.append(chunk)
        except Exception:
            pass
    cap.stop()

    if not chunks:
        print("ERROR: no audio captured. Is parec running, monitor source live, audio playing?", file=sys.stderr)
        return 3

    audio = np.concatenate(chunks)
    sf.write(out_path, audio, cfg.sample_rate, subtype="FLOAT")
    rms = float(np.sqrt(np.mean(audio**2)))
    peak = float(np.max(np.abs(audio)))
    print(f"Wrote {out_path} ({len(audio)/cfg.sample_rate:.2f}s, rms={rms:.4f}, peak={peak:.4f})")
    if rms < 1e-4:
        print("WARN: audio looks silent. Was anything playing?")
    return 0


if __name__ == "__main__":
    sys.exit(main())
