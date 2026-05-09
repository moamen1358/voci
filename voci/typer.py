from __future__ import annotations

import logging
import shutil
import subprocess

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot

log = logging.getLogger(__name__)


class Typer(QObject):
    """Insert text at the current cursor position.

    Two strategies:
      * `paste_text()` — preferred. Puts text on the clipboard and synthesizes
        Ctrl+V at the focused window. Constant time regardless of text length.
        Saves+restores the user's prior clipboard contents.
      * `type_text()` — fallback. Synthesizes individual keystrokes via xdotool.
        Slower for long text (~5 ms per character).

    Threading: this class is a QObject. `paste_text()` is safe to call from any
    thread — it emits a signal that's processed on the GUI thread (where the
    QApplication lives), so all Qt clipboard ops happen on the right thread.
    """

    _paste_request = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._tool: str | None = None
        for cmd in ("xdotool", "wtype", "ydotool"):
            if shutil.which(cmd) is not None:
                self._tool = cmd
                break
        if self._tool is None:
            log.warning(
                "No type-injection tool found (xdotool/wtype/ydotool). Will print to stdout instead."
            )
        # GUI-thread slot runs whatever thread emits paste_text
        self._paste_request.connect(self._do_paste_gui_thread, Qt.QueuedConnection)

    def _send_paste_keystroke(self) -> None:
        if self._tool == "xdotool":
            subprocess.run(["xdotool", "key", "--clearmodifiers", "ctrl+v"], check=False)
        elif self._tool == "wtype":
            subprocess.run(["wtype", "-M", "ctrl", "v", "-m", "ctrl"], check=False)
        elif self._tool == "ydotool":
            subprocess.run(["ydotool", "key", "29:1", "47:1", "47:0", "29:0"], check=False)

    def paste_text(self, text: str) -> None:
        """Thread-safe. Queues clipboard work onto the GUI thread."""
        text = (text or "").strip()
        if not text:
            return
        if self._tool is None:
            print(text, flush=True)
            return
        self._paste_request.emit(text)

    @Slot(str)
    def _do_paste_gui_thread(self, text: str) -> None:
        # Always runs on the GUI thread thanks to QueuedConnection
        try:
            from PySide6.QtWidgets import QApplication

            cb = QApplication.clipboard()
            saved = cb.text()
            cb.setText(text)

            # Tiny delay so X11 registers the clipboard ownership change before paste
            def fire_paste() -> None:
                self._send_paste_keystroke()
                # Restore prior clipboard once the focused app has consumed our paste
                QTimer.singleShot(300, lambda: cb.setText(saved) if saved else None)

            QTimer.singleShot(15, fire_paste)
        except Exception as e:
            log.error("clipboard paste failed (%s); falling back to type", e)
            self.type_text(text)

    def type_text(self, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        if self._tool == "xdotool":
            subprocess.run(["xdotool", "type", "--delay", "0", "--", text], check=False)
        elif self._tool == "wtype":
            subprocess.run(["wtype", "--", text], check=False)
        elif self._tool == "ydotool":
            subprocess.run(["ydotool", "type", text], check=False)
        else:
            print(text, flush=True)
