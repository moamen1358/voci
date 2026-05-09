from __future__ import annotations

from PySide6.QtCore import QPoint, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QGuiApplication, QMouseEvent
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLabel, QVBoxLayout, QWidget


class LineOverlay(QWidget):
    """Single subtitle line in its own frameless transparent always-on-top window.
    Click-through by default; drag-to-move when click-through is disabled.
    """

    text_changed = Signal(str)

    def __init__(
        self,
        font_family: str = "Noto Sans",
        font_size: int = 16,
        color_alpha: int = 255,
        background_alpha: int = 90,
        outline_blur: int = 12,
        outline_alpha: int = 240,
        bold: bool = False,
        rtl: bool = False,
    ) -> None:
        super().__init__()
        self._drag_origin: QPoint | None = None

        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self.label = QLabel("", self)
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(False)
        if rtl:
            self.label.setLayoutDirection(Qt.RightToLeft)
        weight = QFont.DemiBold if bold else QFont.Normal
        self.label.setFont(QFont(font_family, font_size, weight))
        self.label.setStyleSheet(
            f"color: rgba(255,255,255,{color_alpha});"
            f" background-color: rgba(0, 0, 0, {background_alpha});"
            f" padding: 8px 18px;"
            f" border-radius: 8px;"
        )
        shadow = QGraphicsDropShadowEffect(self.label)
        shadow.setBlurRadius(outline_blur)
        shadow.setColor(QColor(0, 0, 0, outline_alpha))
        shadow.setOffset(0, 0)
        self.label.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.label)
        self.setLayout(layout)

        self.text_changed.connect(self._on_text_changed)

    @Slot(str)
    def _on_text_changed(self, text: str) -> None:
        self.label.setText(text)
        self.adjustSize()

    def set_text(self, text: str) -> None:
        self.text_changed.emit(text)

    def set_clickthrough(self, on: bool) -> None:
        self.setAttribute(Qt.WA_TransparentForMouseEvents, on)

    # Drag-to-move (active only when click-through is off)
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_origin = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_origin is not None and event.buttons() & Qt.LeftButton:
            new_pos = event.globalPosition().toPoint() - self._drag_origin
            self.move(new_pos)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.LeftButton:
            self._drag_origin = None
            event.accept()


class SubtitleOverlay(QWidget):
    """Coordinator wrapping two independent LineOverlay windows.

    Top window: source-language partial. Bottom window: target translation.
    Each is its own widget — moveable, hideable, sized independently.
    """

    def __init__(
        self,
        top_font: str = "Noto Sans",
        top_font_size: int = 16,
        top_color_alpha: int = 180,
        bottom_font: str = "Noto Sans",
        bottom_font_size: int = 16,
        bottom_color_alpha: int = 255,
        bottom_bold: bool = False,
        background_alpha: int = 90,
        outline_blur: int = 12,
        outline_alpha: int = 240,
        max_width_frac: float = 0.72,  # accepted for API compat, unused (no wrap)
        bottom_margin_px: int = 80,
    ) -> None:
        super().__init__()
        self.bottom_margin_px = bottom_margin_px
        self.setVisible(False)  # the coordinator itself is invisible

        self.top = LineOverlay(
            font_family=top_font,
            font_size=top_font_size,
            color_alpha=top_color_alpha,
            background_alpha=background_alpha,
            outline_blur=outline_blur,
            outline_alpha=outline_alpha,
            bold=False,
            rtl=False,
        )
        self.bottom = LineOverlay(
            font_family=bottom_font,
            font_size=bottom_font_size,
            color_alpha=bottom_color_alpha,
            background_alpha=background_alpha,  # same as top per request
            outline_blur=outline_blur,
            outline_alpha=outline_alpha,
            bold=bottom_bold,
            rtl=True,
        )
        self._position_defaults()

    def _position_defaults(self) -> None:
        screen = QGuiApplication.primaryScreen()
        geom = screen.availableGeometry()
        # Force a layout pass so adjustSize uses real metrics
        self.bottom.label.setText(" ")
        self.top.label.setText(" ")
        self.bottom.adjustSize()
        self.top.adjustSize()

        # Position bottom (translation) at the very bottom-center
        bot_x = geom.x() + (geom.width() - self.bottom.width()) // 2
        bot_y = geom.y() + geom.height() - self.bottom.height() - self.bottom_margin_px
        self.bottom.move(bot_x, bot_y)

        # Position top (source) just above the bottom window with 8 px gap
        gap = 8
        top_x = geom.x() + (geom.width() - self.top.width()) // 2
        top_y = bot_y - self.top.height() - gap
        self.top.move(top_x, top_y)

        self.top.label.setText("")
        self.bottom.label.setText("")

    # ---------- public API used by main.py ----------

    def set_top_text(self, text: str) -> None:
        self.top.set_text(text)

    def set_bottom_text(self, text: str) -> None:
        self.bottom.set_text(text)

    def set_text(self, text: str) -> None:  # back-compat
        self.set_bottom_text(text)

    def clear_text(self) -> None:
        self.top.set_text("")
        self.bottom.set_text("")

    def show(self) -> None:  # type: ignore[override]
        self.top.show()
        self.bottom.show()

    def hide(self) -> None:  # type: ignore[override]
        self.top.hide()
        self.bottom.hide()

    def isVisible(self) -> bool:  # type: ignore[override]
        return self.top.isVisible() or self.bottom.isVisible()

    def toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()

    def set_clickthrough(self, on: bool) -> None:
        self.top.set_clickthrough(on)
        self.bottom.set_clickthrough(on)

    def testAttribute(self, attr) -> bool:  # type: ignore[override]
        # Click-through state is read from the top window (both kept in sync)
        return self.top.testAttribute(attr)
