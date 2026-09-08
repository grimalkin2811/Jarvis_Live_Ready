"""Overlay cinématique des Protocoles : le « Jarvis, wake up » visuel.

Ce widget plein écran s'affiche par-dessus tout, sans bordure et sans voler
le focus, puis dessine en temps réel la séquence jouée par
``src.protocols`` :

* un **réacteur** central (anneaux concentriques contrarotatifs) qui se
  charge au rythme des beats ;
* un **balayage horizontal** et une grille de fond façon salle de contrôle ;
* le **journal télémétrique** qui s'écrit ligne à ligne, avec effet machine
  à écrire et curseur clignotant ;
* une **barre de progression** et le compte des systèmes en ligne.

Il est piloté uniquement par des évènements (dictionnaires) : le backend ne
connaît pas Qt, et l'overlay ne connaît pas le backend. Les évènements
arrivant d'un thread audio sont réacheminés vers le thread graphique par un
signal Qt, ce qui rend l'ensemble thread-safe.
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QWidget

try:
    from src import protocols
except Exception:  # pragma: no cover - import direct hors package
    import protocols  # type: ignore


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


class _Line:
    """Une ligne du journal, dévoilée caractère par caractère."""

    __slots__ = ("label", "detail", "status", "revealed", "age")

    def __init__(self, label: str, detail: str, status: str) -> None:
        self.label = label
        self.detail = detail
        self.status = status
        self.revealed = 0.0
        self.age = 0.0

    @property
    def text(self) -> str:
        return f"{self.label} — {self.detail}" if self.detail else self.label


class BootSequenceOverlay(QWidget):
    """Fenêtre plein écran affichant un protocole en cours."""

    #: Reçoit les évènements de ``src.protocols`` depuis n'importe quel thread.
    event_received = Signal(dict)

    MAX_LINES = 14

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.setWindowTitle("Jarvis — Protocole")
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        if hasattr(Qt, "WindowDoesNotAcceptFocus"):
            flags |= Qt.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)

        self.time = 0.0
        self.title = "JARVIS"
        self.subtitle = "SÉQUENCE D'ALLUMAGE"
        self.accent = QColor(90, 205, 255)
        self.lines: list[_Line] = []
        self.total_beats = 1
        self.done_beats = 0
        self.progress = 0.0
        self.shown_progress = 0.0
        self.closing = ""
        self.state = "idle"          # idle | running | closing
        self.intensity = 0.0         # fondu global 0..1
        self._fade_target = 0.0
        self._closing_time = 0.0
        self._flash = 0.0            # éclat au démarrage / fin
        self._ring_seed = [random.random() for _ in range(12)]

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

        self.event_received.connect(self.handle_event)

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    def _fit_to_screen(self) -> None:
        screen = QApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())

    def _tick(self) -> None:
        self.time += 0.016
        self.intensity = _lerp(self.intensity, self._fade_target, 0.18)
        self.shown_progress = _lerp(self.shown_progress, self.progress, 0.12)
        self._flash = max(0.0, self._flash - 0.02)

        for line in self.lines:
            line.age += 0.016
            target = len(line.text)
            if line.revealed < target:
                line.revealed = min(target, line.revealed + 2.4)

        if self.state == "closing" and (self.time - self._closing_time) > 2.6:
            self._fade_target = 0.0
            if self.intensity < 0.02:
                self.state = "idle"
                self._timer.stop()
                self.hide()
                return

        self.update()

    # ------------------------------------------------------------------
    # Évènements de protocole
    # ------------------------------------------------------------------

    def handle_event(self, event: dict) -> None:
        """Applique un évènement — **doit** tourner dans le thread Qt."""
        kind = event.get("type")

        if kind == protocols.EVENT_START:
            self.title = str(event.get("title") or "JARVIS")
            self.subtitle = str(event.get("subtitle") or "")
            accent = event.get("accent") or (90, 205, 255)
            try:
                self.accent = QColor(int(accent[0]), int(accent[1]), int(accent[2]))
            except Exception:
                self.accent = QColor(90, 205, 255)
            self.lines = []
            self.total_beats = max(1, len(event.get("beats") or []))
            self.done_beats = 0
            self.progress = 0.0
            self.shown_progress = 0.0
            self.closing = ""
            self.state = "running"
            self._flash = 1.0
            self._fade_target = 1.0
            self._fit_to_screen()
            if not self._timer.isActive():
                self._timer.start(16)
            self.show()
            self.raise_()

        elif kind == protocols.EVENT_BEAT:
            self.lines.append(
                _Line(
                    str(event.get("label") or ""),
                    str(event.get("detail") or ""),
                    str(event.get("status") or "ok"),
                )
            )
            del self.lines[: max(0, len(self.lines) - self.MAX_LINES)]
            self.done_beats = int(event.get("index", 0)) + 1
            self.progress = _clamp(float(event.get("progress") or 0.0), 0.0, 1.0)

        elif kind == protocols.EVENT_LINE:
            self.lines.append(_Line(str(event.get("text") or ""), "", "ok"))
            del self.lines[: max(0, len(self.lines) - self.MAX_LINES)]

        elif kind == protocols.EVENT_DONE:
            self.closing = str(event.get("closing") or "")
            self.progress = 1.0
            self.state = "closing"
            self._closing_time = self.time
            self._flash = 1.0 if not event.get("cancelled") else 0.4

    # ------------------------------------------------------------------
    # Rendu
    # ------------------------------------------------------------------

    def paintEvent(self, _event) -> None:  # noqa: C901 - rendu dense mais linéaire
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.TextAntialiasing, True)

        w = float(self.width())
        h = float(self.height())
        alpha = _clamp(self.intensity, 0.0, 1.0)
        if alpha <= 0.01:
            return

        self._paint_backdrop(painter, w, h, alpha)
        self._paint_grid(painter, w, h, alpha)
        self._paint_scanline(painter, w, h, alpha)

        # Le réacteur est calé un peu au-dessus du centre : la place libre
        # sous lui accueille le titre et le sous-titre sans jamais sortir de
        # l'écran, y compris sur un format très large ou très carré.
        center = QPointF(w * 0.30, h * 0.40)
        radius = min(w * 0.30, h * 0.30) * 0.42
        self._paint_reactor(painter, center, radius, alpha)

        self._paint_titles(painter, center, radius, w, h, alpha)
        self._paint_log(painter, w, h, alpha)
        self._paint_progress(painter, w, h, alpha)

        painter.end()

    # -- couches -------------------------------------------------------

    def _paint_backdrop(self, painter: QPainter, w: float, h: float, alpha: float) -> None:
        veil = QRadialGradient(QPointF(w * 0.32, h * 0.5), max(w, h) * 0.8)
        base = QColor(4, 10, 18)
        base.setAlphaF(0.90 * alpha)
        edge = QColor(0, 3, 8)
        edge.setAlphaF(0.97 * alpha)
        veil.setColorAt(0.0, base)
        veil.setColorAt(1.0, edge)
        painter.fillRect(self.rect(), veil)

        if self._flash > 0.01:
            flash = QColor(self.accent)
            flash.setAlphaF(0.20 * self._flash * alpha)
            painter.fillRect(self.rect(), flash)

    def _paint_grid(self, painter: QPainter, w: float, h: float, alpha: float) -> None:
        pen = QColor(self.accent)
        pen.setAlphaF(0.06 * alpha)
        painter.setPen(QPen(pen, 1.0))
        step = 64.0
        offset = (self.time * 12.0) % step
        y = -offset
        while y < h:
            painter.drawLine(QPointF(0.0, y), QPointF(w, y))
            y += step
        x = -offset
        while x < w:
            painter.drawLine(QPointF(x, 0.0), QPointF(x, h))
            x += step

    def _paint_scanline(self, painter: QPainter, w: float, h: float, alpha: float) -> None:
        y = (self.time * 0.22) % 1.0 * h
        gradient = QLinearGradient(0.0, y - 90.0, 0.0, y + 90.0)
        clear = QColor(self.accent)
        clear.setAlphaF(0.0)
        glow = QColor(self.accent)
        glow.setAlphaF(0.13 * alpha)
        gradient.setColorAt(0.0, clear)
        gradient.setColorAt(0.5, glow)
        gradient.setColorAt(1.0, clear)
        painter.fillRect(QRectF(0.0, y - 90.0, w, 180.0), gradient)

    def _paint_reactor(self, painter: QPainter, center: QPointF, radius: float, alpha: float) -> None:
        charge = _clamp(self.shown_progress, 0.0, 1.0)
        pulse = 0.5 + 0.5 * math.sin(self.time * 2.4)

        # Halo
        halo = QRadialGradient(center, radius * 2.6)
        inner = QColor(self.accent)
        inner.setAlphaF((0.16 + 0.20 * charge) * alpha)
        outer = QColor(self.accent)
        outer.setAlphaF(0.0)
        halo.setColorAt(0.0, inner)
        halo.setColorAt(1.0, outer)
        painter.setPen(Qt.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, radius * 2.6, radius * 2.6)

        # Anneaux contrarotatifs
        painter.setBrush(Qt.NoBrush)
        for index in range(5):
            ring_radius = radius * (0.55 + 0.24 * index)
            direction = 1.0 if index % 2 == 0 else -1.0
            span = 90 + int(160 * self._ring_seed[index])
            start = int((self.time * (26.0 + 13.0 * index) * direction) % 360)
            color = QColor(self.accent)
            color.setAlphaF((0.30 + 0.42 * charge) * alpha)
            painter.setPen(QPen(color, 2.0 if index % 2 else 1.2))
            rect = QRectF(
                center.x() - ring_radius,
                center.y() - ring_radius,
                ring_radius * 2.0,
                ring_radius * 2.0,
            )
            painter.drawArc(rect, start * 16, span * 16)

        # Arc de charge : la progression réelle du protocole
        arc_radius = radius * 1.62
        rect = QRectF(
            center.x() - arc_radius,
            center.y() - arc_radius,
            arc_radius * 2.0,
            arc_radius * 2.0,
        )
        track = QColor(self.accent)
        track.setAlphaF(0.14 * alpha)
        painter.setPen(QPen(track, 5.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 90 * 16, -360 * 16)
        live = QColor(self.accent)
        live.setAlphaF(0.95 * alpha)
        painter.setPen(QPen(live, 5.0, Qt.SolidLine, Qt.RoundCap))
        painter.drawArc(rect, 90 * 16, -int(360 * charge) * 16)

        # Cœur
        core = QRadialGradient(center, radius * 0.62)
        hot = QColor(235, 250, 255)
        hot.setAlphaF((0.55 + 0.35 * charge) * alpha)
        mid = QColor(self.accent)
        mid.setAlphaF((0.30 + 0.30 * pulse * charge) * alpha)
        cold = QColor(self.accent)
        cold.setAlphaF(0.0)
        core.setColorAt(0.0, hot)
        core.setColorAt(0.45, mid)
        core.setColorAt(1.0, cold)
        painter.setPen(Qt.NoPen)
        painter.setBrush(core)
        painter.drawEllipse(center, radius * 0.62, radius * 0.62)

        # Segments périphériques (façon jauge d'énergie)
        segments = 48
        for index in range(segments):
            angle = (index / segments) * math.tau - math.pi / 2.0
            lit = (index / segments) <= charge
            length = radius * (0.16 if lit else 0.09)
            r0 = radius * 1.90
            color = QColor(self.accent)
            color.setAlphaF((0.85 if lit else 0.16) * alpha)
            painter.setPen(QPen(color, 2.0 if lit else 1.0))
            painter.drawLine(
                QPointF(center.x() + math.cos(angle) * r0,
                        center.y() + math.sin(angle) * r0),
                QPointF(center.x() + math.cos(angle) * (r0 + length),
                        center.y() + math.sin(angle) * (r0 + length)),
            )

    def _paint_titles(self, painter: QPainter, center: QPointF, radius: float,
                      w: float, h: float, alpha: float) -> None:
        title_color = QColor(235, 248, 255)
        title_color.setAlphaF(0.95 * alpha)
        font = QFont("Consolas")
        font.setPointSizeF(max(18.0, radius * 0.30))
        font.setBold(True)
        font.setLetterSpacing(QFont.AbsoluteSpacing, radius * 0.06)
        painter.setFont(font)
        painter.setPen(title_color)
        rect = QRectF(0.0, h * 0.70, w * 0.60, h * 0.08)
        painter.drawText(rect, Qt.AlignHCenter | Qt.AlignVCenter, self.title)

        sub_color = QColor(self.accent)
        sub_color.setAlphaF(0.85 * alpha)
        sub = QFont("Consolas")
        sub.setPointSizeF(max(9.0, radius * 0.13))
        sub.setLetterSpacing(QFont.AbsoluteSpacing, radius * 0.05)
        painter.setFont(sub)
        painter.setPen(sub_color)
        rect = QRectF(0.0, h * 0.785, w * 0.60, h * 0.05)
        painter.drawText(rect, Qt.AlignHCenter | Qt.AlignVCenter, self.subtitle)

        # Compteur de systèmes en ligne
        counter = QColor(200, 230, 255)
        counter.setAlphaF(0.70 * alpha)
        mono = QFont("Consolas")
        mono.setPointSizeF(max(8.0, radius * 0.11))
        painter.setFont(mono)
        painter.setPen(counter)
        rect = QRectF(0.0, h * 0.05, w * 0.60, h * 0.05)
        painter.drawText(
            rect,
            Qt.AlignHCenter | Qt.AlignVCenter,
            f"SYSTÈMES EN LIGNE  {self.done_beats:02d} / {self.total_beats:02d}",
        )

    def _paint_log(self, painter: QPainter, w: float, h: float, alpha: float) -> None:
        left = w * 0.52
        top = h * 0.22
        line_height = max(20.0, h * 0.035)

        mono = QFont("Consolas")
        mono.setPointSizeF(max(9.0, h * 0.0135))
        painter.setFont(mono)

        # Cadre du journal
        frame = QColor(self.accent)
        frame.setAlphaF(0.22 * alpha)
        painter.setPen(QPen(frame, 1.2))
        painter.setBrush(Qt.NoBrush)
        panel = QRectF(left - 24.0, top - 44.0, w * 0.40, line_height * (self.MAX_LINES + 2))
        path = QPainterPath()
        path.addRoundedRect(panel, 10.0, 10.0)
        painter.drawPath(path)

        header = QColor(self.accent)
        header.setAlphaF(0.80 * alpha)
        painter.setPen(header)
        painter.drawText(QPointF(left, top - 18.0), "// JOURNAL TÉLÉMÉTRIQUE")

        # Largeur utile du journal : une ligne longue (chemin, message
        # d'erreur) est tronquée proprement plutôt que de déborder du cadre.
        text_width = panel.width() - 56.0

        for index, line in enumerate(self.lines):
            y = top + 10.0 + index * line_height
            fade = _clamp(0.35 + line.age * 2.0, 0.0, 1.0)
            text = line.text[: int(line.revealed)]
            text = painter.fontMetrics().elidedText(
                text, Qt.ElideRight, int(text_width)
            )

            bullet = QColor(120, 255, 190) if line.status == "ok" else QColor(255, 190, 90)
            bullet.setAlphaF(0.9 * fade * alpha)
            painter.setPen(bullet)
            painter.drawText(QPointF(left, y), "▸")

            body = QColor(205, 232, 255)
            body.setAlphaF((0.55 + 0.40 * fade) * alpha)
            painter.setPen(body)
            painter.drawText(QPointF(left + 20.0, y), text)

            if line.revealed < len(line.text) and int(self.time * 6.0) % 2 == 0:
                caret = QColor(self.accent)
                caret.setAlphaF(0.9 * alpha)
                painter.setPen(caret)
                painter.drawText(
                    QPointF(left + 20.0 + painter.fontMetrics().horizontalAdvance(text), y),
                    "█",
                )

        if self.closing:
            closing = QColor(235, 250, 255)
            closing.setAlphaF(0.95 * alpha)
            font = QFont("Consolas")
            font.setPointSizeF(max(11.0, h * 0.019))
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(closing)
            painter.drawText(
                QPointF(left, top + 26.0 + self.MAX_LINES * line_height),
                self.closing,
            )

    def _paint_progress(self, painter: QPainter, w: float, h: float, alpha: float) -> None:
        bar = QRectF(w * 0.12, h * 0.925, w * 0.76, 4.0)
        track = QColor(self.accent)
        track.setAlphaF(0.15 * alpha)
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(bar, 2.0, 2.0)

        filled = QRectF(bar)
        filled.setWidth(bar.width() * _clamp(self.shown_progress, 0.0, 1.0))
        glow = QColor(self.accent)
        glow.setAlphaF(0.90 * alpha)
        painter.setBrush(glow)
        painter.drawRoundedRect(filled, 2.0, 2.0)

        label = QColor(190, 220, 255)
        label.setAlphaF(0.65 * alpha)
        mono = QFont("Consolas")
        mono.setPointSizeF(max(8.0, h * 0.012))
        painter.setFont(mono)
        painter.setPen(label)
        painter.drawText(
            QPointF(bar.left(), bar.top() - 12.0),
            f"{int(self.shown_progress * 100):3d} %",
        )
        painter.drawText(
            QRectF(bar.left(), bar.top() - 24.0, bar.width(), 16.0),
            Qt.AlignRight,
            "ÉCHAP POUR INTERROMPRE",
        )


class ProtocolPresenter(QObject):
    """Relie ``src.protocols`` à l'overlay, quel que soit le thread émetteur."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.overlay = BootSequenceOverlay()
        protocols.add_listener(self._on_event)

    def _on_event(self, event: dict) -> None:
        # Appelé depuis le thread du protocole : on repasse par un signal.
        self.overlay.event_received.emit(event)

    def play(self, name: str = "wake_up") -> bool:
        """Lance un protocole ; renvoie False si le nom est inconnu."""
        return protocols.start_protocol(name) is not None

    def cancel(self) -> None:
        protocols.cancel_active()

    def close(self) -> None:
        protocols.remove_listener(self._on_event)
        try:
            self.overlay.hide()
        except Exception:
            pass
