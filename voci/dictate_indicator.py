from __future__ import annotations

import math
import shutil
import subprocess
import time

from PySide6.QtCore import QPoint, QPointF, QRect, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QGuiApplication,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget


def _cursor_pos() -> tuple[int, int] | None:
    if shutil.which("xdotool") is None:
        return None
    try:
        out = subprocess.check_output(["xdotool", "getmouselocation"], timeout=0.5).decode()
        parts: dict[str, str] = {}
        for tok in out.strip().split():
            if ":" in tok:
                k, v = tok.split(":", 1)
                parts[k] = v
        return int(parts["x"]), int(parts["y"])
    except Exception:
        return None


def _focused_window_geometry() -> tuple[int, int, int, int] | None:
    if shutil.which("xdotool") is None:
        return None
    try:
        out = subprocess.check_output(
            ["xdotool", "getactivewindow", "getwindowgeometry", "--shell"],
            timeout=0.5,
        ).decode()
        kvs: dict[str, str] = {}
        for line in out.strip().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                kvs[k.strip()] = v.strip()
        return (int(kvs["X"]), int(kvs["Y"]), int(kvs["WIDTH"]), int(kvs["HEIGHT"]))
    except Exception:
        return None


class DictateIndicator(QWidget):
    """Custom-painted equalizer pill: 5 vertical bars in a red→yellow gradient
    that bounce in sync with sine-driven phases, plus an elapsed-seconds counter
    on the right. While transcribing, the bars are replaced by a centered
    status string. Frameless, transparent, always-on-top, click-through.
    """

    show_recording = Signal()
    hide_recording = Signal()
    set_status_text = Signal(str)
    set_audio_level = Signal(float)  # 0..1+ normalized RMS, drives wave amplitude

    NUM_BARS = 5
    BAR_W = 6
    BAR_GAP = 5
    BAR_MIN_H = 6
    BAR_MAX_H = 38
    PILL_PAD_H = 18
    PILL_PAD_V = 14
    TEXT_GAP = 14
    TEXT_WIDTH = 56
    PILL_RADIUS = 22

    STYLES = ("bars", "pulse", "dots", "wave", "ripple", "blob")

    def __init__(self, style: str = "bars") -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)

        self._mode = "recording"
        self._status_text = ""
        self._t0 = 0.0
        self._anim_t = 0.0
        self._audio_level = 0.0  # raw, last value reported
        self._audio_level_smoothed = 0.0  # EMA-smoothed for visuals
        self._font = QFont("Noto Sans", 13, QFont.DemiBold)
        self.style = style if style in self.STYLES else "bars"

        if self.style == "pulse":
            self.setFixedSize(64, 64)
        elif self.style == "dots":
            self.setFixedSize(96, 40)
        elif self.style == "wave":
            self.setFixedSize(180, 56)
        elif self.style == "ripple":
            self.setFixedSize(72, 72)
        elif self.style == "blob":
            self.setFixedSize(76, 76)
        else:
            bars_w = self.NUM_BARS * self.BAR_W + (self.NUM_BARS - 1) * self.BAR_GAP
            w = self.PILL_PAD_H * 2 + bars_w + self.TEXT_GAP + self.TEXT_WIDTH
            h = self.BAR_MAX_H + self.PILL_PAD_V * 2
            self.setFixedSize(w, h)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)

        self.show_recording.connect(self._on_show, Qt.QueuedConnection)
        self.hide_recording.connect(self._on_hide, Qt.QueuedConnection)
        self.set_status_text.connect(self._on_status, Qt.QueuedConnection)
        self.set_audio_level.connect(self._on_audio_level, Qt.QueuedConnection)

        self.hide()

    def sizeHint(self) -> QSize:
        return self.size()

    @Slot()
    def _on_show(self) -> None:
        self._mode = "recording"
        self._t0 = time.monotonic()
        self._anim_t = 0.0
        self._audio_level = 0.0
        self._audio_level_smoothed = 0.0
        self._reposition()
        self.show()
        self.raise_()
        self.timer.start(16)  # ~60 fps for buttery-smooth animation

    @Slot(float)
    def _on_audio_level(self, level: float) -> None:
        # Asymmetric EMA: rise fast (responsive), fall slowly (smooth decay)
        self._audio_level = max(0.0, level)
        target = self._audio_level
        if target > self._audio_level_smoothed:
            self._audio_level_smoothed = 0.55 * self._audio_level_smoothed + 0.45 * target
        else:
            self._audio_level_smoothed = 0.85 * self._audio_level_smoothed + 0.15 * target

    @Slot()
    def _on_hide(self) -> None:
        self.timer.stop()
        self.hide()

    @Slot(str)
    def _on_status(self, text: str) -> None:
        self._mode = "status"
        self._status_text = text
        self._reposition()
        if not self.isVisible():
            self.show()
            self.raise_()
        if not self.timer.isActive():
            self.timer.start(16)
        self.update()

    def _tick(self) -> None:
        self._anim_t = time.monotonic() - self._t0
        self.update()

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)

        # Pill background — skipped for "wave" so the line floats bare
        if self.style != "wave":
            p.setBrush(QColor(10, 10, 10, 245))
            p.setPen(Qt.NoPen)
            radius = min(self.height() // 2, self.PILL_RADIUS)
            p.drawRoundedRect(self.rect(), radius, radius)

        if self._mode == "status":
            self._paint_status(p)
            return

        if self.style == "pulse":
            self._paint_pulse(p)
        elif self.style == "dots":
            self._paint_dots(p)
        elif self.style == "wave":
            self._paint_wave(p)
        elif self.style == "ripple":
            self._paint_ripple(p)
        elif self.style == "blob":
            self._paint_blob(p)
        else:
            # Original red/orange equalizer bars + glow
            p.setBrush(Qt.NoBrush)
            glow_pulse = 0.6 + 0.4 * math.sin(self._anim_t * 5.0)
            p.setPen(QColor(255, 60, 60, int(80 + 70 * glow_pulse)))
            p.drawRoundedRect(
                self.rect().adjusted(1, 1, -1, -1), self.PILL_RADIUS - 1, self.PILL_RADIUS - 1
            )
            self._paint_bars(p)

    def _paint_pulse(self, p: QPainter) -> None:
        """Single breathing white dot with a faint expanding ring."""
        cx = self.width() // 2
        cy = self.height() // 2

        # Faint outer ring that expands and fades over a 1.6 s loop
        ring_t = (self._anim_t * 0.625) % 1.0  # 0..1
        ring_radius = int(10 + 18 * ring_t)
        ring_alpha = int(120 * (1.0 - ring_t))
        if ring_alpha > 0:
            p.setBrush(Qt.NoBrush)
            pen = p.pen()
            pen.setColor(QColor(255, 255, 255, ring_alpha))
            pen.setWidth(2)
            p.setPen(pen)
            p.drawEllipse(QPoint(cx, cy), ring_radius, ring_radius)

        # Inner dot — breathes between r=6 and r=10
        breath = 0.5 + 0.5 * math.sin(self._anim_t * 4.0)
        r = int(6 + 4 * breath)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 230))
        p.drawEllipse(QPoint(cx, cy), r, r)

    def _paint_dots(self, p: QPainter) -> None:
        """Three white dots that pulse in sequence — typing-indicator style."""
        n = 3
        dot_r = 5
        gap = 16
        total_w = (n - 1) * gap
        cy = self.height() // 2
        start_x = (self.width() - total_w) // 2

        for i in range(n):
            phase = self._anim_t * 4.0 - i * 0.6
            level = 0.5 + 0.5 * math.sin(phase)
            alpha = int(80 + 175 * level)
            radius = int(dot_r + 1.5 * level)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(255, 255, 255, alpha))
            p.drawEllipse(QPoint(start_x + i * gap, cy), radius, radius)

    def _build_wave_path(self, time_offset: float, amp_mult: float) -> QPainterPath:
        x_start = 8
        x_end = self.width() - 8
        cy = self.height() / 2
        n = 56
        t_anim = self._anim_t + time_offset

        # Scale amplitude by smoothed audio level — DRAMATIC mapping so silence
        # is nearly flat and speech punches up clearly.
        #   silence (RMS ≈ 0.001)  → scale ≈ 0.08 (barely moving)
        #   quiet talk (RMS ≈ 0.03) → scale ≈ 0.5
        #   normal talk (RMS ≈ 0.08) → scale ≈ 1.1
        #   loud (RMS ≈ 0.20+)     → scale ≈ 1.9 (clamped)
        level_scale = 0.08 + min(1.85, self._audio_level_smoothed * 13.0)

        pts: list[tuple[float, float]] = []
        for i in range(n):
            progress = i / (n - 1)
            x = x_start + (x_end - x_start) * progress
            # Smooth bell envelope (raised cosine — softer than pure sine)
            envelope = 0.5 - 0.5 * math.cos(progress * 2 * math.pi)
            t = t_anim * 5.5 + progress * 7.0
            wave = (
                0.55 * math.sin(t)
                + 0.30 * math.sin(t * 2.1 + 1.4)
                + 0.18 * math.sin(t * 0.6 + 3.0)
                + 0.10 * math.sin(t * 3.7 + 2.0)
            )
            y = cy + 14.0 * amp_mult * envelope * wave * level_scale
            pts.append((x, y))

        # Cubic-ish smoothing via quadratic curves through segment midpoints
        path = QPainterPath()
        path.moveTo(*pts[0])
        for i in range(1, len(pts) - 1):
            x0, y0 = pts[i]
            x1, y1 = pts[i + 1]
            mid = ((x0 + x1) / 2, (y0 + y1) / 2)
            path.quadTo(x0, y0, *mid)
        path.lineTo(*pts[-1])
        return path

    def _paint_wave(self, p: QPainter) -> None:
        """Layered audio-waveform:
        - 3-layer red glow halo (outer→inner) for soft diffuse light
        - echo wave behind the main one (smaller amp, time-offset) → parallax depth
        - main crisp gradient line on top (red → coral along x)
        """
        path = self._build_wave_path(time_offset=0.0, amp_mult=1.0)
        echo = self._build_wave_path(time_offset=-0.18, amp_mult=0.55)

        p.setBrush(Qt.NoBrush)

        # Outer wide soft glow
        g_outer = QPen(QColor(255, 40, 60, 35))
        g_outer.setWidthF(11.0)
        g_outer.setCapStyle(Qt.RoundCap)
        g_outer.setJoinStyle(Qt.RoundJoin)
        p.setPen(g_outer)
        p.drawPath(path)

        # Mid glow
        g_mid = QPen(QColor(255, 50, 60, 70))
        g_mid.setWidthF(7.0)
        g_mid.setCapStyle(Qt.RoundCap)
        g_mid.setJoinStyle(Qt.RoundJoin)
        p.setPen(g_mid)
        p.drawPath(path)

        # Echo wave (faded, behind)
        echo_pen = QPen(QColor(255, 90, 100, 90))
        echo_pen.setWidthF(1.6)
        echo_pen.setCapStyle(Qt.RoundCap)
        echo_pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(echo_pen)
        p.drawPath(echo)

        # Main line — gradient along x (deep red → coral)
        grad = QLinearGradient(8, 0, self.width() - 8, 0)
        grad.setColorAt(0.0, QColor(220, 30, 50, 245))
        grad.setColorAt(0.5, QColor(255, 80, 90, 250))
        grad.setColorAt(1.0, QColor(220, 30, 50, 245))
        line = QPen(QBrush(grad), 2.6)
        line.setCapStyle(Qt.RoundCap)
        line.setJoinStyle(Qt.RoundJoin)
        p.setPen(line)
        p.drawPath(path)

    def _paint_ripple(self, p: QPainter) -> None:
        """Sonar-style concentric rings expanding outward and fading."""
        cx = self.width() // 2
        cy = self.height() // 2
        n_ripples = 3
        cycle = 1.6  # seconds for a ring's full life
        for i in range(n_ripples):
            phase = ((self._anim_t / cycle) + i / n_ripples) % 1.0
            r = int(6 + 28 * phase)
            alpha = int(180 * (1.0 - phase))
            if alpha <= 0:
                continue
            pen = QPen(QColor(255, 255, 255, alpha))
            pen.setWidth(2)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPoint(cx, cy), r, r)

        # Steady inner dot
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawEllipse(QPoint(cx, cy), 4, 4)

    def _paint_blob(self, p: QPainter) -> None:
        """Liquid morphing blob — radial deformation using harmonic sines.
        Filled with a soft white radial gradient. Modern / organic feel."""
        cx = self.width() / 2
        cy = self.height() / 2
        base_r = 22.0
        n = 64

        path = QPainterPath()
        for i in range(n + 1):
            angle = 2 * math.pi * i / n
            deform = (
                3.0 * math.sin(self._anim_t * 2.2 + angle * 3.0)
                + 2.0 * math.sin(self._anim_t * 1.4 + angle * 5.0 + 1.1)
                + 1.2 * math.sin(self._anim_t * 3.1 + angle * 7.0 + 2.4)
            )
            r = base_r + deform
            x = cx + r * math.cos(angle)
            y = cy + r * math.sin(angle)
            if i == 0:
                path.moveTo(x, y)
            else:
                path.lineTo(x, y)
        path.closeSubpath()

        grad = QRadialGradient(QPointF(cx, cy), base_r + 6.0)
        grad.setColorAt(0.0, QColor(255, 255, 255, 240))
        grad.setColorAt(0.6, QColor(255, 255, 255, 180))
        grad.setColorAt(1.0, QColor(255, 255, 255, 50))
        p.setBrush(grad)
        p.setPen(Qt.NoPen)
        p.drawPath(path)

    def _paint_bars(self, p: QPainter) -> None:
        bars_w = self.NUM_BARS * self.BAR_W + (self.NUM_BARS - 1) * self.BAR_GAP
        bars_x = self.PILL_PAD_H
        center_y = self.height() // 2

        for i in range(self.NUM_BARS):
            phase = self._anim_t * 9.0 + i * 0.85
            base = 0.5 + 0.5 * math.sin(phase)
            envelope = 0.7 + 0.3 * math.sin(self._anim_t * 4.0 + i * 0.4)
            level = max(0.18, base * envelope)
            h = int(self.BAR_MIN_H + (self.BAR_MAX_H - self.BAR_MIN_H) * level)
            x = bars_x + i * (self.BAR_W + self.BAR_GAP)
            y = center_y - h // 2

            grad = QLinearGradient(0, y, 0, y + h)
            grad.setColorAt(0.0, QColor(255, 220, 90))
            grad.setColorAt(0.5, QColor(255, 130, 50))
            grad.setColorAt(1.0, QColor(230, 40, 60))
            p.setBrush(grad)
            p.setPen(Qt.NoPen)
            p.drawRoundedRect(QRect(x, y, self.BAR_W, h), 3, 3)

        p.setFont(self._font)
        p.setPen(QColor(255, 255, 255, 220))
        text_x = bars_x + bars_w + self.TEXT_GAP
        elapsed = self._anim_t
        p.drawText(
            QRect(text_x, 0, self.width() - text_x - self.PILL_PAD_H, self.height()),
            int(Qt.AlignVCenter | Qt.AlignLeft),
            f"{elapsed:0.1f}s",
        )

    def _paint_status(self, p: QPainter) -> None:
        p.setFont(self._font)
        p.setPen(QColor(255, 255, 255, 230))
        p.drawText(self.rect(), int(Qt.AlignCenter), self._status_text or "")

    def _reposition(self) -> None:
        win = _focused_window_geometry()
        if win is not None:
            wx, wy, ww, wh = win
            x = wx + (ww - self.width()) // 2
            y = wy + wh - self.height() - 24
            screen_obj = (
                QGuiApplication.screenAt(QPoint(wx + ww // 2, wy + wh // 2))
                or QGuiApplication.primaryScreen()
            )
        else:
            pos = _cursor_pos()
            if pos is None:
                screen = QGuiApplication.primaryScreen().availableGeometry()
                x = screen.x() + (screen.width() - self.width()) // 2
                y = screen.y() + screen.height() - self.height() - 120
                self.move(x, y)
                return
            cx, cy = pos
            screen_obj = QGuiApplication.screenAt(QPoint(cx, cy)) or QGuiApplication.primaryScreen()
            x = cx - self.width() // 2
            y = cy + 28
        screen = screen_obj.availableGeometry()
        x = max(screen.x() + 8, min(x, screen.x() + screen.width() - self.width() - 8))
        y = max(screen.y() + 8, min(y, screen.y() + screen.height() - self.height() - 8))
        self.move(x, y)
