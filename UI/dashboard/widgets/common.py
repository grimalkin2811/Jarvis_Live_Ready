"""Briques visuelles partagées par les widgets (pastilles, tuiles, jauges)."""

from __future__ import annotations

import html
import time

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QPainter, QPalette, QPen, QRadialGradient
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from ..painting import draw_ring, draw_sparkline


class ElidedLabel(QLabel):
    """Libellé qui se réduit avec des points de suspension (jamais tronqué net)."""

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(24, super().minimumSizeHint().height())

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setFont(self.font())
        painter.setPen(self.palette().color(QPalette.WindowText))
        text = self.fontMetrics().elidedText(self.text(), Qt.ElideRight, max(0, self.width()))
        painter.drawText(self.rect(), int(Qt.AlignLeft | Qt.AlignVCenter), text)
        painter.end()


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""))


def clock(ts: float | None) -> str:
    if not ts:
        return "—"
    try:
        return time.strftime("%H:%M:%S", time.localtime(float(ts)))
    except (TypeError, ValueError):
        return "—"


def iso_short(value) -> str:
    text = str(value or "")
    if "T" in text:
        date, _, rest = text.partition("T")
        return f"{date} {rest[:5]}"
    return text or "—"


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    if wrap:
        widget.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
    return widget


def set_role(widget: QWidget, role: str) -> None:
    widget.setProperty("role", role)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def button(text: str, role: str | None = None, tooltip: str = "") -> QPushButton:
    widget = QPushButton(text)
    if role:
        widget.setProperty("role", role)
    if tooltip:
        widget.setToolTip(tooltip)
    return widget


class StatusDot(QWidget):
    """Pastille d'état colorée (avec halo selon le thème)."""

    def __init__(self, theme_manager, status: str = "unknown", size: int = 12, parent=None) -> None:
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.status = status
        self._size = size
        self.setFixedSize(size + 6, size + 6)

    def set_status(self, status: str) -> None:
        if status != self.status:
            self.status = status
            self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._size + 6, self._size + 6)

    def paintEvent(self, event) -> None:  # noqa: N802
        theme = self.theme_manager.theme
        color = theme.status_color(self.status)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        c = QRectF(self.rect()).center()
        if theme.glow > 0.05:
            g = QRadialGradient(c, self._size)
            halo = QColor(color)
            halo.setAlphaF(0.45 * theme.glow)
            g.setColorAt(0.0, halo)
            end = QColor(color)
            end.setAlpha(0)
            g.setColorAt(1.0, end)
            p.setPen(Qt.NoPen)
            p.setBrush(g)
            p.drawEllipse(c, self._size * 0.9, self._size * 0.9)
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(c, self._size / 2.0, self._size / 2.0)
        p.end()


class Ring(QWidget):
    """Jauge circulaire (valeur réelle uniquement ; ``None`` = vide explicite)."""

    def __init__(self, theme_manager, size: int = 84, parent=None) -> None:
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.fraction: float | None = None
        self.status = "unknown"
        self.text = ""
        self.setFixedSize(size, size)

    def set_value(self, fraction: float | None, status: str = "unknown", text: str = "") -> None:
        self.fraction, self.status, self.text = fraction, status, text
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        theme = self.theme_manager.theme
        p = QPainter(self)
        color = theme.status_color(self.status) if self.status != "accent" else theme.accent
        track = QColor(theme.text_dim)
        track.setAlpha(50)
        draw_ring(p, QRectF(self.rect()), self.fraction or 0.0, color, track, width=7.0)
        p.setPen(QPen(theme.text))
        font = p.font()
        font.setPointSizeF(max(8.0, self.height() / 7.0))
        font.setBold(True)
        p.setFont(font)
        p.drawText(QRectF(self.rect()), Qt.AlignCenter, self.text or ("—" if self.fraction is None else ""))
        p.end()


class Sparkline(QWidget):
    def __init__(self, theme_manager, parent=None) -> None:
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.values: list[float] = []
        self.max_value = 100.0
        self.setMinimumHeight(28)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_values(self, values: list[float], max_value: float = 100.0) -> None:
        self.values = list(values)
        self.max_value = max_value
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        theme = self.theme_manager.theme
        r = QRectF(self.rect()).adjusted(1, 3, -1, -2)
        if len(self.values) >= 2:
            draw_sparkline(p, r, self.values, theme.accent, self.max_value)
        else:
            # Pas de courbe sans au moins deux mesures RÉELLES (jamais de
            # tracé simulé) : simple ligne de base discrète.
            p.setPen(QPen(theme.text_dim, 1.0, Qt.DotLine))
            p.drawLine(int(r.left()), int(r.bottom()), int(r.right()), int(r.bottom()))
        p.end()


class MetricTile(QFrame):
    """Tuile : libellé, grande valeur, légende, mini-graphique optionnel."""

    def __init__(self, theme_manager, title: str, with_graph: bool = False, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(1)
        self.title = label(title, "dim")
        self.value = label("—", "metric")
        self.caption = label("", "dim", wrap=True)
        layout.addWidget(self.title)
        layout.addWidget(self.value)
        layout.addWidget(self.caption)
        self.graph = Sparkline(theme_manager) if with_graph else None
        if self.graph is not None:
            layout.addWidget(self.graph, 1)

    def set(self, value: str, caption: str = "") -> None:
        self.value.setText(value)
        self.caption.setText(caption)
        self.caption.setVisible(bool(caption))


class EmptyState(QWidget):
    """Message d'état vide explicite (jamais de fausse donnée)."""

    def __init__(self, text: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        self.label = label(text, "dim", wrap=True)
        self.label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.label)
        layout.addStretch(1)

    def set_text(self, text: str) -> None:
        self.label.setText(text)


def hrow(*widgets, stretch_last: bool = False, spacing: int = 6) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(spacing)
    for index, widget in enumerate(widgets):
        if widget is None:
            row.addStretch(1)
        elif isinstance(widget, int):
            row.addSpacing(widget)
        else:
            row.addWidget(widget, 1 if (stretch_last and index == len(widgets) - 1) else 0)
    return row
