from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    Property,
    QPropertyAnimation,
    QTimer,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QWidget

from .appearance_actions import AppearanceState


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def build_presence_hook(overlay: "ScreenHaloOverlay") -> Callable[[str], None]:
    def _hook(state: str) -> None:
        action_map = {
            "listening": overlay.show_listening,
            "thinking": overlay.show_thinking,
            "speaking": overlay.show_speaking,
            "hide_overlay": overlay.hide_overlay,
            "hidden": overlay.hide_overlay,
        }
        action = action_map.get(state, overlay.hide_overlay)
        action()

    return _hook


class ScreenHaloOverlay(QWidget):
    overlayIntensityChanged = Signal(float)

    def __init__(self, appearance_state: AppearanceState | None = None) -> None:
        super().__init__()

        self.appearance_state = appearance_state or AppearanceState()
        self._overlay_intensity = 0.0
        self._presence_state = "hidden"
        self._animation_target = 0.0
        self._phase = 0.0

        window_flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        if hasattr(Qt, "WindowTransparentForInput"):
            window_flags |= Qt.WindowTransparentForInput
        if hasattr(Qt, "WindowDoesNotAcceptFocus"):
            window_flags |= Qt.WindowDoesNotAcceptFocus

        self.setWindowFlags(window_flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(False)

        self._fade_animation = QPropertyAnimation(self, b"overlay_intensity", self)
        self._fade_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._fade_animation.setDuration(260)
        self._fade_animation.finished.connect(self._on_fade_finished)

        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._tick)
        self._pulse_timer.start(16)

    def _tick(self) -> None:
        self._phase = (self._phase + 0.045) % (math.tau * 8.0)
        if self.isVisible() or self._fade_animation.state() == QAbstractAnimation.State.Running:
            self.update()

    def _on_fade_finished(self) -> None:
        if self._animation_target <= 0.0 and self._overlay_intensity <= 0.001:
            super().hide()

    def _animate_to(self, target: float, duration: int = 260, presence_state: str | None = None) -> None:
        target = _clamp(target, 0.0, 1.0)
        self._animation_target = target
        if presence_state is not None:
            self._presence_state = presence_state

        if target > 0.0 and not self.isVisible():
            self.show_overlay()

        self._fade_animation.stop()
        self._fade_animation.setDuration(duration)
        self._fade_animation.setStartValue(self._overlay_intensity)
        self._fade_animation.setEndValue(target)
        self._fade_animation.start()

    def show_overlay(self) -> None:
        super().showFullScreen()
        self.raise_()

    def show_listening(self) -> None:
        self._animate_to(0.62, duration=220, presence_state="listening")

    def show_thinking(self) -> None:
        self._animate_to(0.78, duration=240, presence_state="thinking")

    def show_speaking(self) -> None:
        self._animate_to(1.0, duration=220, presence_state="speaking")

    def hide_overlay(self) -> None:
        self._animate_to(0.0, duration=280, presence_state="hidden")

    def get_overlay_intensity(self) -> float:
        return self._overlay_intensity

    def set_overlay_intensity(self, value: float) -> None:
        value = _clamp(float(value), 0.0, 1.0)
        if abs(self._overlay_intensity - value) < 1e-4:
            return
        self._overlay_intensity = value
        self.overlayIntensityChanged.emit(value)
        self.update()

    overlay_intensity = Property(
        float,
        get_overlay_intensity,
        set_overlay_intensity,
        notify=overlayIntensityChanged,
    )

    def _base_glow_color(self) -> QColor:
        return QColor(self.appearance_state.glow_color)

    def _accent_color(self) -> QColor:
        accent = QColor(self.appearance_state.text_color)
        accent.setAlpha(255)
        return accent

    def _rounded_ring_path(self, rect: QRectF, inset: float, radius: float) -> QPainterPath:
        outer_path = QPainterPath()
        inner_path = QPainterPath()
        outer_path.addRoundedRect(rect, radius, radius)
        inner_rect = rect.adjusted(inset, inset, -inset, -inset)
        inner_radius = max(8.0, radius - inset * 0.75)
        inner_path.addRoundedRect(inner_rect, inner_radius, inner_radius)
        return outer_path.subtracted(inner_path)

    def _draw_glow_blob(
        self,
        painter: QPainter,
        center_x: float,
        center_y: float,
        radius: float,
        color: QColor,
        stretch_x: float = 1.0,
        stretch_y: float = 1.0,
    ) -> None:
        blob = QRadialGradient(center_x, center_y, radius)
        soft = QColor(color)
        soft.setAlpha(0)
        mid = QColor(color)
        mid.setAlpha(color.alpha())
        blob.setColorAt(0.0, mid)
        blob.setColorAt(0.36, QColor(color.red(), color.green(), color.blue(), int(color.alpha() * 0.42)))
        blob.setColorAt(1.0, soft)
        painter.save()
        painter.translate(center_x, center_y)
        painter.scale(stretch_x, stretch_y)
        painter.translate(-center_x, -center_y)
        painter.setPen(Qt.NoPen)
        painter.setBrush(blob)
        painter.drawEllipse(
            center_x - radius,
            center_y - radius,
            radius * 2.0,
            radius * 2.0,
        )
        painter.restore()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            painter.fillRect(self.rect(), Qt.transparent)

            intensity = _clamp(self._overlay_intensity, 0.0, 1.0)
            if intensity <= 0.001:
                return

            width = float(self.width())
            height = float(self.height())
            min_side = min(width, height)
            ring_inset = 14.0 + 26.0 * intensity
            outer_radius = min_side * 0.055 + 34.0 + 16.0 * intensity
            inner_radius = max(10.0, outer_radius - ring_inset * 0.85)
            ring_width = 18.0 + 34.0 * intensity
            pulse = 0.84 + 0.16 * math.sin(self._phase)
            drift_x = math.sin(self._phase * 0.67) * 10.0 * intensity
            drift_y = math.cos(self._phase * 0.53) * 8.0 * intensity
            base_color = self._base_glow_color()
            accent_color = self._accent_color()

            outer_alpha = int(32 + 90 * intensity * pulse)
            mid_alpha = int(78 + 120 * intensity * pulse)
            inner_alpha = int(18 + 36 * intensity)
            highlight_alpha = int(44 + 60 * intensity * pulse)
            edge_alpha = int(18 + 40 * intensity * pulse)

            outer = QColor(base_color)
            outer.setAlpha(outer_alpha)
            mid = QColor(base_color)
            mid.setAlpha(mid_alpha)
            inner = QColor(base_color)
            inner.setAlpha(inner_alpha)
            highlight = QColor(accent_color)
            highlight.setAlpha(highlight_alpha)
            edge_glow = QColor(base_color)
            edge_glow.setAlpha(edge_alpha)

            transparent = QColor(0, 0, 0, 0)

            outer_rect = QRectF(2.0, 2.0, width - 4.0, height - 4.0)
            ring_path = self._rounded_ring_path(outer_rect, ring_inset, outer_radius)
            inner_path = self._rounded_ring_path(
                outer_rect.adjusted(8.0, 8.0, -8.0, -8.0),
                max(12.0, ring_inset * 0.62),
                inner_radius,
            )

            sweep = QLinearGradient(0.0, 0.0, width, height)
            sweep.setColorAt(0.0, highlight)
            sweep.setColorAt(0.26, outer)
            sweep.setColorAt(0.52, edge_glow)
            sweep.setColorAt(0.75, mid)
            sweep.setColorAt(1.0, highlight)

            painter.setPen(Qt.NoPen)
            painter.setBrush(sweep)
            painter.drawPath(ring_path)

            shimmer = QLinearGradient(width, 0.0, 0.0, height)
            shimmer.setColorAt(0.0, QColor(highlight.red(), highlight.green(), highlight.blue(), int(highlight.alpha() * 0.36)))
            shimmer.setColorAt(0.5, transparent)
            shimmer.setColorAt(1.0, QColor(base_color.red(), base_color.green(), base_color.blue(), int(base_color.alpha() * 0.18)))
            painter.setBrush(shimmer)
            painter.drawPath(ring_path)

            contour_color = QColor(accent_color)
            contour_color.setAlpha(int(14 + 34 * intensity * pulse))
            painter.setPen(QPen(contour_color, 1.15))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(inner_path)

            corner_radius = min_side * 0.09 + 54.0 * intensity
            corner_spread_x = 1.0 + 0.10 * intensity
            corner_spread_y = 1.0 + 0.08 * intensity
            corner_color = QColor(base_color)
            corner_color.setAlpha(int(90 + 80 * intensity * pulse))
            for center_x, center_y, stretch_x, stretch_y in (
                (0.0, 0.0, 1.0 + 0.06 * intensity, 1.0 + 0.04 * intensity),
                (width, 0.0, 1.0 + 0.10 * intensity, 1.0 + 0.04 * intensity),
                (0.0, height, 1.0 + 0.04 * intensity, 1.0 + 0.10 * intensity),
                (width, height, 1.0 + 0.08 * intensity, 1.0 + 0.08 * intensity),
            ):
                self._draw_glow_blob(
                    painter,
                    center_x,
                    center_y,
                    corner_radius,
                    corner_color,
                    stretch_x=stretch_x,
                    stretch_y=stretch_y,
                )

            edge_radius = min_side * 0.12 + 36.0 * intensity
            edge_color = QColor(accent_color)
            edge_color.setAlpha(int(68 + 52 * intensity * pulse))
            for center_x, center_y, stretch_x, stretch_y in (
                (width * 0.5 + drift_x, 0.0, 2.2, 0.72),
                (width * 0.5 - drift_x, height, 2.2, 0.72),
                (0.0, height * 0.5 + drift_y, 0.72, 2.1),
                (width, height * 0.5 - drift_y, 0.72, 2.1),
            ):
                self._draw_glow_blob(
                    painter,
                    center_x,
                    center_y,
                    edge_radius,
                    edge_color,
                    stretch_x=stretch_x,
                    stretch_y=stretch_y,
                )

            border_color = QColor(base_color)
            border_color.setAlpha(int(24 + 44 * intensity))
            painter.setPen(QPen(border_color, 1.25))
            painter.drawPath(ring_path)
        finally:
            painter.end()
