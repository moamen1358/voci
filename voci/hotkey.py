from __future__ import annotations

import logging
import threading

from pynput import keyboard
from PySide6.QtCore import QObject, Signal

log = logging.getLogger(__name__)


class HotkeyListener(QObject):
    toggle_visibility = Signal()
    swap_target_lang = Signal()
    toggle_drag_mode = Signal()
    clear_overlay = Signal()

    def __init__(
        self,
        hotkey_toggle: str = "<alt>+<ctrl>+v",
        hotkey_swap: str = "<alt>+<ctrl>+l",
        hotkey_drag: str = "<alt>+<ctrl>+d",
        hotkey_clear: str = "<alt>+<ctrl>+x",
    ) -> None:
        super().__init__()
        self._hotkeys = {
            hotkey_toggle: lambda: self.toggle_visibility.emit(),
            hotkey_swap: lambda: self.swap_target_lang.emit(),
            hotkey_drag: lambda: self.toggle_drag_mode.emit(),
            hotkey_clear: lambda: self.clear_overlay.emit(),
        }
        self._labels = {
            "toggle": hotkey_toggle,
            "swap": hotkey_swap,
            "drag": hotkey_drag,
            "clear": hotkey_clear,
        }
        self._listener: keyboard.GlobalHotKeys | None = None
        self._thread: threading.Thread | None = None

    def _build(self) -> keyboard.GlobalHotKeys:
        return keyboard.GlobalHotKeys(self._hotkeys)

    def start(self) -> None:
        def _run() -> None:
            try:
                self._listener = self._build()
                log.info(
                    "Hotkeys: %s=toggle | %s=drag-mode | %s=clear | %s=swap-lang",
                    self._labels["toggle"],
                    self._labels["drag"],
                    self._labels["clear"],
                    self._labels["swap"],
                )
                self._listener.run()
            except Exception as e:
                log.error("Hotkey listener failed: %s", e)

        self._thread = threading.Thread(target=_run, name="hotkey-listener", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
