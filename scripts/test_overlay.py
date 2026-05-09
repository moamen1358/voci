#!/usr/bin/env python3
"""Standalone smoke test for the subtitle overlay.

Cycles through several lines (English + Arabic) so we can eyeball font, RTL,
positioning, click-through, and always-on-top behavior.

Usage:
    .venv/bin/python scripts/test_overlay.py
Then put another window over where the overlay should be, click around, and
see if clicks pass through to the window underneath.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from voci.overlay import SubtitleOverlay


SAMPLES = [
    "Hello, this is the voci subtitle overlay.",
    "مرحباً، هذا هو شريط الترجمة.",
    "The quick brown fox jumps over the lazy dog.",
    "اللغة العربية جميلة جداً.",
    "Try clicking on a window underneath this overlay — clicks should pass through.",
    "إذا ظهر النص بشكل صحيح، فهذا يعني أن العرض يعمل.",
]


def main() -> int:
    app = QApplication(sys.argv)
    overlay = SubtitleOverlay(font_size=22)
    overlay.show()

    idx = {"i": 0}

    def tick() -> None:
        overlay.set_text(SAMPLES[idx["i"] % len(SAMPLES)])
        idx["i"] += 1

    tick()
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(2000)

    print("Overlay running. Ctrl-C to exit.")
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
