from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

import numpy as np
from pynput import keyboard
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from voci.dictate_indicator import DictateIndicator
from voci.mic_capture import MicStreamer
from voci.stt import DictateSTT
from voci.translate import NllbTranslator
from voci.typer import Typer

log = logging.getLogger("voci.dictate")


def parse_hotkey(spec: str) -> set:
    result: set = set()
    for part in spec.lower().split("+"):
        p = part.strip()
        if not p:
            continue
        if p.startswith("<") and p.endswith(">"):
            name = p[1:-1]
            mapping = {
                "ctrl": keyboard.Key.ctrl,
                "alt": keyboard.Key.alt,
                "shift": keyboard.Key.shift,
                "cmd": keyboard.Key.cmd,
                "space": keyboard.Key.space,
                "tab": keyboard.Key.tab,
                "enter": keyboard.Key.enter,
                "esc": keyboard.Key.esc,
            }
            if name in mapping:
                result.add(mapping[name])
            elif name.startswith("f") and name[1:].isdigit():
                result.add(getattr(keyboard.Key, name))
            else:
                raise ValueError(f"Unknown special key: <{name}>")
        elif len(p) == 1:
            result.add(keyboard.KeyCode.from_char(p))
        elif p.startswith("f") and p[1:].isdigit():
            result.add(getattr(keyboard.Key, p))
        else:
            raise ValueError(f"Cannot parse hotkey part: {p!r}")
    if not result:
        raise ValueError(f"Empty hotkey: {spec!r}")
    return result


def _canonical(key) -> object:
    aliases = {
        keyboard.Key.ctrl_l: keyboard.Key.ctrl,
        keyboard.Key.ctrl_r: keyboard.Key.ctrl,
        keyboard.Key.alt_l: keyboard.Key.alt,
        keyboard.Key.alt_r: keyboard.Key.alt,
        keyboard.Key.alt_gr: keyboard.Key.alt,
        keyboard.Key.shift_l: keyboard.Key.shift,
        keyboard.Key.shift_r: keyboard.Key.shift,
        keyboard.Key.cmd_l: keyboard.Key.cmd,
        keyboard.Key.cmd_r: keyboard.Key.cmd,
    }
    return aliases.get(key, key)


class DictateApp:
    """Hold-to-talk dictation backed by local Parakeet TDT.

    A single hotkey (default F9): press to start recording, release to stop and
    type the transcribed text into the focused window.
    """

    def __init__(
        self,
        indicator: DictateIndicator,
        hotkey: str = "f9",
        target_lang: str | None = None,
    ) -> None:
        self.indicator = indicator
        self.hotkey_spec = hotkey
        self.hotkey = parse_hotkey(hotkey)
        self.target_lang = target_lang
        self.translator = (
            NllbTranslator(src_lang="en", target_lang=target_lang)
            if target_lang and target_lang != "en"
            else None
        )
        self.typer = Typer()
        self.stt = DictateSTT(language="en")
        self.mic = MicStreamer(on_frame=self._on_mic_frame)

        self._pressed: set = set()
        self._recording = False
        self._record_started_at = 0.0
        self._busy_lock = threading.Lock()
        self._listener: keyboard.Listener | None = None

    def _on_press(self, key) -> None:
        canon = _canonical(key)
        self._pressed.add(canon)
        if self._recording:
            return
        if self.hotkey.issubset(self._pressed):
            self._begin_session()

    def _on_release(self, key) -> None:
        canon = _canonical(key)
        self._pressed.discard(canon)
        if not self._recording:
            return
        if not self.hotkey.issubset(self._pressed):
            self._end_session()

    def _begin_session(self) -> None:
        self._record_started_at = time.monotonic()
        log.info("🎙  recording...")
        self.indicator.show_recording.emit()
        self.stt.begin_session()
        self._recording = True

    def _end_session(self) -> None:
        self._recording = False
        duration = time.monotonic() - self._record_started_at
        self.indicator.set_status_text.emit("✏  Transcribing…")
        threading.Thread(target=self._finish, args=(duration,), daemon=True).start()

    def _on_mic_frame(self, pcm: bytes) -> None:
        if not self._recording:
            return
        self.stt.send_audio(pcm)
        # RMS → indicator wave amplitude
        try:
            samples = np.frombuffer(pcm, dtype=np.int16)
            if samples.size == 0:
                return
            rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) / 32768.0
            self.indicator.set_audio_level.emit(rms)
        except Exception:
            pass

    def _finish(self, duration: float) -> None:
        with self._busy_lock:
            try:
                if duration < 0.2:
                    log.info("(too short, ignored)")
                    self.stt.end_session()
                    return
                t0 = time.monotonic()
                text = self.stt.end_session()
                stt_ms = (time.monotonic() - t0) * 1000
                if not text:
                    log.info("(no speech detected)")
                    return
                if self.translator is not None:
                    self.indicator.set_status_text.emit("🌐  Translating…")
                    t1 = time.monotonic()
                    text = self.translator.translate(text) or text
                    log.debug("translate %.0fms", (time.monotonic() - t1) * 1000)
                self.indicator.set_status_text.emit("⌨  Typing…")
                log.info("✏  [%.0fms STT, %.1fs audio] -> %s", stt_ms, duration, text)
                self.typer.paste_text(text)
            except Exception as e:
                log.exception("dictate finish failed: %s", e)
            finally:
                self.indicator.hide_recording.emit()

    def start(self) -> None:
        self.stt.start()
        self.mic.start()
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        log.info("Dictate ready. Hold %s to record.", self.hotkey_spec)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self.mic.stop()
        self.stt.stop()


def main() -> int:
    p = argparse.ArgumentParser(
        prog="voci-dictate",
        description="Hold-to-talk dictation using local NVIDIA Parakeet (no cloud).",
    )
    p.add_argument("--hotkey", default="f9", help="Hold this key to record (default: f9)")
    p.add_argument(
        "--target",
        default=None,
        help="Translate to this language code before typing (e.g. ar)",
    )
    p.add_argument(
        "--style",
        default="wave",
        choices=["bars", "pulse", "dots", "wave", "ripple", "blob"],
        help="Indicator animation style (default: wave)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    qapp = QApplication.instance() or QApplication([sys.argv[0]])
    qapp.setQuitOnLastWindowClosed(False)

    indicator = DictateIndicator(style=args.style)
    dictate = DictateApp(
        indicator=indicator,
        hotkey=args.hotkey,
        target_lang=args.target,
    )
    QTimer.singleShot(0, dictate.start)

    rc = qapp.exec()
    dictate.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
