"""Widgets du HUD Desktop (v1.7.0).

Chaque élément est un ``QWidget`` **enfant** de l'overlay : il est créé au plus
une fois par session puis montré/masqué. Aucune fenêtre n'est créée ou détruite
pendant l'utilisation, et rien n'est reconstruit à chaque changement d'état.

Tous les widgets partagent :

* une opacité animée (propriété Qt ``hud_opacity``) et un léger glissement
  vertical — l'apparition doit être *sentie*, jamais subie ;
* un rendu peint à la main (pas de feuille de style) : le fond translucide
  sombre reste lisible sur n'importe quel bureau et ne ressemble jamais à une
  fenêtre Windows ;
* la transparence aux clics, sauf la barre de contrôles qui est explicitement
  interactive.
"""

from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from .design import Metrics, Motion, Opacities, SURFACE, SURFACE_LIGHT, with_alpha


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class HudWidget(QWidget):
    """Base commune : opacité animée, thème, échelle, transparence aux clics."""

    #: Les sous-classes interactives passent à ``False``.
    CLICK_THROUGH = True

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._opacity = 0.0
        self._slide = Motion.WIDGET_SLIDE_PX
        self._accent = QColor(70, 180, 255)
        self._text_color = QColor(224, 240, 255)
        self._scale = 1.0
        self._text_scale = 1.0
        self._visible_target = False

        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        if self.CLICK_THROUGH:
            self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.hide()

        self._fade = QPropertyAnimation(self, b"hud_opacity", self)
        self._fade.setEasingCurve(QEasingCurve(Motion.WIDGET_CURVE))
        self._fade.finished.connect(self._on_fade_finished)

    # -- propriété animée ---------------------------------------------------

    def get_hud_opacity(self) -> float:
        return self._opacity

    def set_hud_opacity(self, value: float) -> None:
        value = _clamp(float(value), 0.0, 1.0)
        if abs(value - self._opacity) < 1e-3:
            return
        self._opacity = value
        self._slide = Motion.WIDGET_SLIDE_PX * (1.0 - value)
        self.update()

    hud_opacity = Property(float, get_hud_opacity, set_hud_opacity)

    # -- thème / échelle ----------------------------------------------------

    def apply_theme(self, accent: QColor, text: QColor, *, scale: float = 1.0, text_scale: float = 1.0) -> None:
        self._accent = QColor(accent)
        self._text_color = QColor(text)
        self._scale = _clamp(float(scale), 0.6, 2.0)
        self._text_scale = _clamp(float(text_scale), 0.8, 1.8)
        self.refresh_geometry()

    def refresh_geometry(self) -> None:
        size = self.sizeHint()
        if size.isValid():
            self.resize(size)
        self.update()

    def body_size(self) -> QSize:
        """Taille réellement peinte (hors marge réservée au glissement).

        Le widget réserve ``Motion.WIDGET_SLIDE_PX`` en bas pour l'animation
        d'apparition ; cette marge est vide et ne doit jamais compter dans le
        placement ni dans la détection de chevauchement.
        """
        size = self.size()
        return QSize(size.width(), max(1, size.height() - int(Motion.WIDGET_SLIDE_PX)))

    def _font(self, size: int, bold: bool = False) -> QFont:
        font = QFont(Metrics.FONT_FAMILY)
        font.setPointSizeF(max(7.0, size * self._scale * self._text_scale))
        font.setBold(bold)
        return font

    # -- visibilité ---------------------------------------------------------

    def show_hud(self) -> None:
        self._visible_target = True
        if not self.isVisible():
            self.show()
            self.raise_()
        self._fade.stop()
        self._fade.setDuration(Motion.WIDGET_IN_MS)
        self._fade.setStartValue(self._opacity)
        self._fade.setEndValue(1.0)
        self._fade.start()

    def hide_hud(self, *, immediate: bool = False) -> None:
        self._visible_target = False
        self._fade.stop()
        if immediate or not self.isVisible():
            self.set_hud_opacity(0.0)
            self.hide()
            return
        self._fade.setDuration(Motion.WIDGET_OUT_MS)
        self._fade.setStartValue(self._opacity)
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if not self._visible_target and self._opacity <= 0.01:
            self.hide()

    def is_showing(self) -> bool:
        """Vrai si le widget est visible ou en train d'apparaître."""
        return self._visible_target

    # -- rendu partagé ------------------------------------------------------

    def _paint_surface(self, painter: QPainter, rect: QRectF, radius: float) -> None:
        path = QPainterPath()
        path.addRoundedRect(rect, radius, radius)
        painter.setPen(Qt.NoPen)
        painter.setBrush(with_alpha(SURFACE, Opacities.CARD_BACKGROUND))
        painter.drawPath(path)
        # Liseré très discret, teinté par l'accent : le widget « appartient »
        # au halo sans devenir un cadre.
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(with_alpha(self._accent, Opacities.BORDER), 1.0))
        painter.drawPath(path)

    def _begin(self, painter: QPainter) -> bool:
        if self._opacity <= 0.01:
            return False
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setOpacity(self._opacity)
        painter.translate(0.0, self._slide)
        return True


# ---------------------------------------------------------------------------
# Pastille d'état
# ---------------------------------------------------------------------------


class StatusChip(HudWidget):
    """Pastille « À l'écoute / Réflexion / Action / Réponse »."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._pulse = 0.0

    def set_text(self, text: str) -> None:
        value = str(text or "")
        if value == self._text:
            return
        self._text = value
        self.refresh_geometry()

    def text(self) -> str:
        return self._text

    def set_pulse(self, value: float) -> None:
        self._pulse = _clamp(float(value), 0.0, 1.0)
        self.update()

    def sizeHint(self) -> QSize:  # type: ignore[override]
        metrics = QFontMetricsF(self._font(Metrics.FONT_SIZE_CHIP, bold=True))
        pad_x = Metrics.CHIP_PADDING_X * self._scale
        pad_y = Metrics.CHIP_PADDING_Y * self._scale
        dot = Metrics.CHIP_DOT * self._scale
        width = metrics.horizontalAdvance(self._text) + pad_x * 2.0 + dot * 2.2
        height = metrics.height() + pad_y * 2.0
        return QSize(int(round(width)), int(round(height + Motion.WIDGET_SLIDE_PX)))

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        try:
            if not self._begin(painter):
                return
            body = QRectF(0.0, 0.0, self.width(), self.height() - Motion.WIDGET_SLIDE_PX)
            radius = min(body.height() / 2.0, Metrics.CHIP_RADIUS * self._scale * 1.6)
            self._paint_surface(painter, body.adjusted(0.5, 0.5, -0.5, -0.5), radius)

            dot = Metrics.CHIP_DOT * self._scale
            cx = Metrics.CHIP_PADDING_X * self._scale + dot / 2.0
            cy = body.center().y()
            glow = 0.55 + 0.45 * self._pulse
            painter.setPen(Qt.NoPen)
            painter.setBrush(with_alpha(self._accent, 0.22 * glow))
            painter.drawEllipse(QRectF(cx - dot, cy - dot, dot * 2.0, dot * 2.0))
            painter.setBrush(with_alpha(self._accent, glow))
            painter.drawEllipse(QRectF(cx - dot / 2.0, cy - dot / 2.0, dot, dot))

            painter.setFont(self._font(Metrics.FONT_SIZE_CHIP, bold=True))
            painter.setPen(with_alpha(self._text_color, Opacities.TEXT))
            text_rect = QRectF(
                cx + dot,
                body.top(),
                body.width() - cx - dot - Metrics.CHIP_PADDING_X * self._scale,
                body.height(),
            )
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, self._text)
        finally:
            painter.end()


# ---------------------------------------------------------------------------
# Pastille d'outil
# ---------------------------------------------------------------------------


class ToolChip(StatusChip):
    """Variante de la pastille d'état dédiée à l'exécution d'un outil."""

    def set_tool(self, name: str) -> None:
        label = str(name or "").strip()
        self.set_text(f"⚙ {label}" if label else "⚙ Action")


# ---------------------------------------------------------------------------
# Cartes de texte
# ---------------------------------------------------------------------------


class TextCard(HudWidget):
    """Carte translucide affichant un texte court sur une ou deux lignes."""

    MAX_LINES = 2
    PREFIX = ""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = ""
        self._max_width = 520.0

    def set_max_width(self, width: float) -> None:
        value = max(Metrics.CARD_MIN_WIDTH, float(width))
        if abs(value - self._max_width) < 1.0:
            return
        self._max_width = value
        self.refresh_geometry()

    def set_text(self, text: str) -> None:
        value = " ".join(str(text or "").split())
        if value == self._text:
            return
        self._text = value
        self.refresh_geometry()

    def text(self) -> str:
        return self._text

    def _lines(self) -> list[str]:
        if not self._text:
            return []
        metrics = QFontMetricsF(self._font(Metrics.FONT_SIZE_CARD))
        available = self._max_width - Metrics.CARD_PADDING_X * 2.0 * self._scale
        words = self._text.split(" ")
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if metrics.horizontalAdvance(candidate) <= available or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
                if len(lines) >= self.MAX_LINES:
                    break
        if current and len(lines) < self.MAX_LINES:
            lines.append(current)
        if len(lines) > self.MAX_LINES:
            lines = lines[: self.MAX_LINES]
        # Dernière ligne élidée si le texte déborde encore.
        if lines:
            joined = " ".join(lines)
            if len(joined) < len(self._text):
                lines[-1] = metrics.elidedText(lines[-1] + " …", Qt.ElideRight, available)
        return lines

    def sizeHint(self) -> QSize:  # type: ignore[override]
        lines = self._lines()
        metrics = QFontMetricsF(self._font(Metrics.FONT_SIZE_CARD))
        pad_x = Metrics.CARD_PADDING_X * self._scale
        pad_y = Metrics.CARD_PADDING_Y * self._scale
        if not lines:
            return QSize(int(Metrics.CARD_MIN_WIDTH), int(metrics.height() + pad_y * 2.0))
        width = max(metrics.horizontalAdvance(line) for line in lines) + pad_x * 2.0
        width = max(Metrics.CARD_MIN_WIDTH, min(self._max_width, width))
        height = metrics.height() * len(lines) + pad_y * 2.0
        return QSize(int(round(width)), int(round(height + Motion.WIDGET_SLIDE_PX)))

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        try:
            if not self._begin(painter) or not self._text:
                return
            body = QRectF(0.0, 0.0, self.width(), self.height() - Motion.WIDGET_SLIDE_PX)
            self._paint_surface(
                painter,
                body.adjusted(0.5, 0.5, -0.5, -0.5),
                Metrics.CARD_RADIUS * self._scale,
            )
            painter.setFont(self._font(Metrics.FONT_SIZE_CARD))
            metrics = QFontMetricsF(painter.font())
            pad_x = Metrics.CARD_PADDING_X * self._scale
            pad_y = Metrics.CARD_PADDING_Y * self._scale
            y = body.top() + pad_y
            painter.setPen(with_alpha(self._text_color, Opacities.TEXT))
            for line in self._lines():
                painter.drawText(
                    QRectF(pad_x, y, body.width() - pad_x * 2.0, metrics.height()),
                    Qt.AlignVCenter | Qt.AlignLeft,
                    line,
                )
                y += metrics.height()
        finally:
            painter.end()


class TranscriptCard(TextCard):
    """Ce que Jarvis a compris. Alimentée par les transcriptions existantes."""


class ResponseCard(TextCard):
    """Résumé court de la réponse de Jarvis (désactivé par défaut)."""

    MAX_LINES = 2


# ---------------------------------------------------------------------------
# Visualiseur audio
# ---------------------------------------------------------------------------


class AudioBars(HudWidget):
    """Sept barres réagissant au niveau audio réel (micro ou voix de Jarvis)."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._level = 0.0
        self._phase = 0.0
        self._values = [0.15] * Metrics.BARS

    def set_level(self, level: float) -> None:
        self._level = _clamp(float(level), 0.0, 1.0)

    def advance(self, dt: float, motion: float = 1.0) -> None:
        """Met à jour les hauteurs. Appelé par l'overlay, jamais par un timer propre."""
        self._phase = (self._phase + dt) % 3600.0
        import math

        for index in range(Metrics.BARS):
            centre = abs(index - (Metrics.BARS - 1) / 2.0) / max(1.0, (Metrics.BARS - 1) / 2.0)
            shape = 1.0 - 0.55 * centre
            wobble = 0.5 + 0.5 * math.sin(self._phase * (2.3 + index * 0.37) + index)
            target = 0.12 + self._level * shape * (0.55 + 0.45 * wobble) * (0.4 + 0.6 * motion)
            self._values[index] += (target - self._values[index]) * 0.35
        self.update()

    def sizeHint(self) -> QSize:  # type: ignore[override]
        width = (Metrics.BAR_WIDTH + Metrics.BAR_GAP) * Metrics.BARS * self._scale
        height = Metrics.BAR_MAX_HEIGHT * self._scale
        return QSize(int(round(width)), int(round(height + Motion.WIDGET_SLIDE_PX)))

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        try:
            if not self._begin(painter):
                return
            painter.setPen(Qt.NoPen)
            bar_w = Metrics.BAR_WIDTH * self._scale
            gap = Metrics.BAR_GAP * self._scale
            max_h = Metrics.BAR_MAX_HEIGHT * self._scale
            base_y = max_h
            for index, value in enumerate(self._values):
                height = max(2.0, max_h * _clamp(value, 0.0, 1.0))
                x = index * (bar_w + gap)
                alpha = 0.35 + 0.55 * _clamp(value, 0.0, 1.0)
                painter.setBrush(with_alpha(self._accent, alpha))
                painter.drawRoundedRect(
                    QRectF(x, base_y - height, bar_w, height),
                    Metrics.BAR_RADIUS,
                    Metrics.BAR_RADIUS,
                )
        finally:
            painter.end()


# ---------------------------------------------------------------------------
# Barre de contrôles (widget interactif)
# ---------------------------------------------------------------------------


class ControlsBar(HudWidget):
    """Boutons Stop / Micro / Masquer. Seul widget qui reçoit des clics."""

    CLICK_THROUGH = False

    stopRequested = Signal()
    muteToggled = Signal()
    hideRequested = Signal()

    BUTTONS = ("stop", "mic", "hide")
    GLYPHS = {"stop": "■", "mic": "🎤", "hide": "✕"}
    TOOLTIPS = {
        "stop": "Couper la réponse en cours",
        "mic": "Couper / rétablir le micro",
        "hide": "Masquer l'overlay",
    }

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hover = -1
        self._muted = False
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Contrôles Jarvis")

    def set_muted(self, muted: bool) -> None:
        value = bool(muted)
        if value == self._muted:
            return
        self._muted = value
        self.update()

    def sizeHint(self) -> QSize:  # type: ignore[override]
        size = Metrics.BUTTON_SIZE * self._scale
        gap = Metrics.BUTTON_GAP * self._scale
        width = size * len(self.BUTTONS) + gap * (len(self.BUTTONS) + 1)
        height = size + gap * 2.0
        return QSize(int(round(width)), int(round(height + Motion.WIDGET_SLIDE_PX)))

    def _button_rect(self, index: int) -> QRectF:
        size = Metrics.BUTTON_SIZE * self._scale
        gap = Metrics.BUTTON_GAP * self._scale
        return QRectF(gap + index * (size + gap), gap, size, size)

    def _hit(self, pos) -> int:
        point = pos.toPointF() if hasattr(pos, "toPointF") else pos
        for index in range(len(self.BUTTONS)):
            if self._button_rect(index).contains(point):
                return index
        return -1

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        hover = self._hit(event.position() if hasattr(event, "position") else event.pos())
        if hover != self._hover:
            self._hover = hover
            self.update()

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hover = -1
        self.update()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        index = self._hit(event.position() if hasattr(event, "position") else event.pos())
        if index < 0:
            event.ignore()
            return
        self.activate(index)
        event.accept()

    def activate(self, index: int) -> None:
        """Déclenche un bouton par son index (utilisé aussi par les tests)."""
        if not (0 <= index < len(self.BUTTONS)):
            return
        name = self.BUTTONS[index]
        if name == "stop":
            self.stopRequested.emit()
        elif name == "mic":
            self.muteToggled.emit()
        else:
            self.hideRequested.emit()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        try:
            if not self._begin(painter):
                return
            body = QRectF(0.0, 0.0, self.width(), self.height() - Motion.WIDGET_SLIDE_PX)
            self._paint_surface(
                painter,
                body.adjusted(0.5, 0.5, -0.5, -0.5),
                Metrics.BUTTON_RADIUS * self._scale * 1.4,
            )
            painter.setFont(self._font(Metrics.FONT_SIZE_CARD, bold=True))
            for index, name in enumerate(self.BUTTONS):
                rect = self._button_rect(index)
                hovered = index == self._hover
                painter.setPen(Qt.NoPen)
                painter.setBrush(
                    with_alpha(SURFACE_LIGHT, 0.95 if hovered else 0.55)
                )
                painter.drawRoundedRect(
                    rect, Metrics.BUTTON_RADIUS * self._scale, Metrics.BUTTON_RADIUS * self._scale
                )
                if hovered:
                    painter.setBrush(Qt.NoBrush)
                    painter.setPen(QPen(with_alpha(self._accent, 0.75), 1.2))
                    painter.drawRoundedRect(
                        rect,
                        Metrics.BUTTON_RADIUS * self._scale,
                        Metrics.BUTTON_RADIUS * self._scale,
                    )
                glyph = self.GLYPHS[name]
                if name == "mic" and self._muted:
                    painter.setPen(with_alpha(QColor(255, 140, 140), Opacities.TEXT))
                else:
                    painter.setPen(with_alpha(self._text_color, Opacities.TEXT))
                painter.drawText(rect, Qt.AlignCenter, glyph)
        finally:
            painter.end()
