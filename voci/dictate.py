from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

import numpy as np
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from pynput import keyboard

from voci.dictate_indicator import DictateIndicator
from voci.dictate_stt import DictateStreamingSTT
from voci.lingva_translate import LingvaTranslator
from voci.local_stt import LocalWhisperSTT
from voci.mic_capture import MicStreamer
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
    """Dual-backend hold-to-talk dictation.

    Two hotkeys map to two STT backends:
      - local_hotkey  (default F9)  → on-device Whisper-large-v3-turbo (CUDA)
      - cloud_hotkey  (default F10) → Deepgram Nova-2 (cloud streaming)

    Both backends are pre-warmed at startup. The mic is shared (pre-spawned).
    Per F-key press, the audio is routed to the chosen backend until release.
    """

    def __init__(
        self,
        indicator: DictateIndicator,
        local_hotkey: str = "f9",
        cloud_hotkey: str = "f8",
        target_lang: str | None = None,
        keywords: list[str] | None = None,
        backend: str = "both",  # "both" | "cloud" | "local"
    ) -> None:
        self.indicator = indicator
        self.backend_mode = backend
        # In single-backend modes the chosen backend takes the local_hotkey (F9)
        if backend == "cloud":
            cloud_hotkey = local_hotkey
        elif backend == "local":
            pass  # local already on F9
        self.local_hotkey_spec = local_hotkey
        self.cloud_hotkey_spec = cloud_hotkey
        self.local_hotkey = parse_hotkey(local_hotkey)
        self.cloud_hotkey = parse_hotkey(cloud_hotkey)
        self.target_lang = target_lang
        self.translator = (
            LingvaTranslator(src_lang="en", target_lang=target_lang)
            if target_lang and target_lang != "en"
            else None
        )
        self.typer = Typer()
        self.local_stt = LocalWhisperSTT(language="en", keywords=keywords) if backend in ("both", "local") else None
        self.cloud_stt = DictateStreamingSTT(language="en", keywords=keywords) if backend in ("both", "cloud") else None
        self.mic = MicStreamer(on_frame=self._on_mic_frame)

        self._pressed: set = set()
        self._recording = False
        self._active_backend: str | None = None  # "local" | "cloud"
        self._record_started_at = 0.0
        self._busy_lock = threading.Lock()
        self._listener: keyboard.Listener | None = None

    def _hotkey_for(self, backend: str) -> set:
        return self.local_hotkey if backend == "local" else self.cloud_hotkey

    def _on_press(self, key) -> None:
        canon = _canonical(key)
        self._pressed.add(canon)
        if self._recording:
            return
        if self.local_stt is not None and self.local_hotkey.issubset(self._pressed):
            self._begin_session("local")
        elif self.cloud_stt is not None and self.cloud_hotkey.issubset(self._pressed):
            self._begin_session("cloud")

    def _on_release(self, key) -> None:
        canon = _canonical(key)
        self._pressed.discard(canon)
        if not self._recording:
            return
        active_hk = self._hotkey_for(self._active_backend or "local")
        if not active_hk.issubset(self._pressed):
            self._end_session()

    def _begin_session(self, backend: str) -> None:
        self._active_backend = backend
        self._record_started_at = time.monotonic()
        log.info("🎙  recording [%s]...", backend)
        self.indicator.show_recording.emit()
        if backend == "local":
            self.local_stt.begin_session()
        else:
            self.cloud_stt.begin_session()
        self._recording = True

    def _end_session(self) -> None:
        backend = self._active_backend or "local"
        self._recording = False
        duration = time.monotonic() - self._record_started_at
        self.indicator.set_status_text.emit("✏  Transcribing…")
        threading.Thread(target=self._finish, args=(backend, duration), daemon=True).start()

    def _on_mic_frame(self, pcm: bytes) -> None:
        if not self._recording:
            return
        if self._active_backend == "local":
            self.local_stt.send_audio(pcm)
        else:
            self.cloud_stt.send_audio(pcm)
        # RMS → indicator wave amplitude
        try:
            samples = np.frombuffer(pcm, dtype=np.int16)
            if samples.size == 0:
                return
            rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) / 32768.0
            self.indicator.set_audio_level.emit(rms)
        except Exception:
            pass

    def _finish(self, backend: str, duration: float) -> None:
        with self._busy_lock:
            try:
                if duration < 0.2:
                    log.info("(too short, ignored)")
                    if backend == "local":
                        self.local_stt.end_session()
                    else:
                        self.cloud_stt.end_session(max_wait_ms=50)
                    return
                t0 = time.monotonic()
                if backend == "local":
                    text = self.local_stt.end_session()
                else:
                    text = self.cloud_stt.end_session(max_wait_ms=350)
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
                log.info("✏  [%s, finalize %.0fms, %.1fs audio] -> %s", backend, stt_ms, duration, text)
                self.typer.type_text(text)
            except Exception as e:
                log.exception("dictate finish failed: %s", e)
            finally:
                self.indicator.hide_recording.emit()

    def start(self) -> None:
        if self.cloud_stt is not None:
            self.cloud_stt.start()
        if self.local_stt is not None:
            self.local_stt.start()
        self.mic.start()
        self._listener = keyboard.Listener(on_press=self._on_press, on_release=self._on_release)
        self._listener.start()
        if self.backend_mode == "both":
            log.info("Dictate ready. %s = local Whisper. %s = Deepgram cloud.", self.local_hotkey_spec, self.cloud_hotkey_spec)
        elif self.backend_mode == "cloud":
            log.info("Dictate ready (cloud only). %s = Deepgram.", self.cloud_hotkey_spec)
        else:
            log.info("Dictate ready (local only). %s = Whisper.", self.local_hotkey_spec)

    def stop(self) -> None:
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self.mic.stop()
        if self.cloud_stt is not None:
            self.cloud_stt.stop()
        if self.local_stt is not None:
            self.local_stt.stop()


def main() -> int:
    p = argparse.ArgumentParser(
        prog="voci-dictate",
        description="Hold-to-talk dictation: F9 = local Whisper (private), F10 = Deepgram cloud.",
    )
    p.add_argument(
        "--backend",
        default="both",
        choices=["both", "cloud", "local"],
        help="Which STT backend(s) to load. 'both' (default): F9=local, F8=cloud. 'cloud': only Deepgram on F9. 'local': only Whisper on F9.",
    )
    p.add_argument("--local-hotkey", default="f9", help="Hold for LOCAL Whisper STT (default: f9)")
    p.add_argument("--cloud-hotkey", default="f8", help="Hold for DEEPGRAM cloud STT (default: f8)")
    p.add_argument("--target", default=None, help="Translate to this language code before typing (e.g. ar)")
    p.add_argument("--keyword", action="append", default=[], help="Boost a domain-specific term (repeatable)")
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

    # Pass only argv[0] so QApplication doesn't try to parse our argparse args
    qapp = QApplication.instance() or QApplication([sys.argv[0]])
    qapp.setQuitOnLastWindowClosed(False)

    indicator = DictateIndicator(style=args.style)
    dictate = DictateApp(
        indicator=indicator,
        local_hotkey=args.local_hotkey,
        cloud_hotkey=args.cloud_hotkey,
        target_lang=args.target,
        keywords=args.keyword,
        backend=args.backend,
    )
    QTimer.singleShot(0, dictate.start)

    rc = qapp.exec()
    dictate.stop()
    return rc


if __name__ == "__main__":
    sys.exit(main())
