"""Rendu peint des identités visuelles (panneaux, fonds, jauges).

Tout est dessiné avec ``QPainter`` (aucun moteur web, aucune dépendance) :

* ``paint_panel`` — cadre d'un widget selon le thème (anneaux JARVIS,
  chanfreins tactiques EDITH, verre dépoli FRIDAY) ;
* ``BackgroundRenderer`` — fond de fenêtre ; couche statique mise en cache
  (pixmap), couche animée légère dessinée seulement si le niveau d'animation
  le demande (aucun minuteur en mode « léger ») ;
* ``draw_sparkline`` / ``draw_ring`` — graphiques alimentés uniquement par des
  données réelles (l'appelant affiche un état vide explicite sinon).

Les objets géométriques sont toujours recréés plutôt que mutés (``QPointF``,
``QRectF``) : voir la note sur PySide6 6.12 dans ``requirements.txt``.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import (
    QBrush,
    QColor,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)

from .theme import ResolvedTheme


def _alpha(color: QColor, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def panel_path(rect: QRectF, theme: ResolvedTheme) -> QPainterPath:
    path = QPainterPath()
    shape = theme.spec.shape
    if shape == "chamfered":
        c = float(min(theme.spec.chamfer, rect.width() / 4, rect.height() / 4))
        polygon = QPolygonF([
            QPointF(rect.left() + c, rect.top()),
            QPointF(rect.right(), rect.top()),
            QPointF(rect.right(), rect.bottom() - c),
            QPointF(rect.right() - c, rect.bottom()),
            QPointF(rect.left(), rect.bottom()),
            QPointF(rect.left(), rect.top() + c),
        ])
        path.addPolygon(polygon)
        path.closeSubpath()
    else:
        radius = float(min(theme.spec.radius, rect.height() / 2, rect.width() / 2))
        path.addRoundedRect(rect, radius, radius)
    return path


def paint_panel(painter: QPainter, rect: QRectF, theme: ResolvedTheme, *, active: bool = False,
                highlight: float = 0.0, header_height: float = 0.0, edit_mode: bool = False) -> None:
    """Peint le cadre d'un panneau. ``highlight`` (0..1) : activité récente."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    spec = theme.spec
    inner = QRectF(rect.left() + 1.5, rect.top() + 1.5, rect.width() - 3.0, rect.height() - 3.0)
    path = panel_path(inner, theme)
    glow = theme.glow * (1.0 + 0.6 * highlight)

    if spec.shape == "soft":
        # Ombre douce (FRIDAY) : quelques couches translucides décalées.
        for i in range(4, 0, -1):
            shadow = QRectF(inner.left() - i, inner.top() + 2 + i, inner.width() + 2 * i, inner.height() + i)
            sp = QPainterPath()
            r = float(min(spec.radius + i, shadow.height() / 2))
            sp.addRoundedRect(shadow, r, r)
            painter.fillPath(sp, QColor(0, 0, 0, 14))

    # Remplissage
    gradient = QLinearGradient(inner.topLeft(), inner.bottomLeft())
    top = QColor(theme.panel)
    bottom = QColor(theme.panel_alt)
    if spec.shape == "soft":
        top = top.lighter(118)
        top.setAlpha(theme.panel.alpha())
    gradient.setColorAt(0.0, top)
    gradient.setColorAt(1.0, bottom)
    painter.fillPath(path, QBrush(gradient))

    accent = theme.accent
    if spec.shape == "rounded":
        # JARVIS : liseré lumineux + arcs.
        for width, alpha in ((7.0, 0.05), (4.0, 0.09), (2.2, 0.16)):
            painter.setPen(QPen(_alpha(theme.border, alpha * glow * 1.6), width))
            painter.drawPath(path)
        painter.setPen(QPen(_alpha(theme.border, 0.45 + 0.35 * highlight), 1.1))
        painter.drawPath(path)
        arc_r = min(20.0, inner.height() / 3)
        arc_rect = QRectF(inner.left() + 6, inner.top() + 6, arc_r, arc_r)
        painter.setPen(QPen(_alpha(accent, 0.85), 1.6))
        painter.drawArc(arc_rect, 90 * 16, 90 * 16)
        painter.setPen(QPen(_alpha(accent, 0.45), 1.0))
        painter.drawArc(QRectF(arc_rect.left() + 4, arc_rect.top() + 4, arc_r - 8, arc_r - 8), 100 * 16, 160 * 16)
        painter.setPen(QPen(_alpha(accent, 0.55), 1.2))
        for i in range(3):
            x = inner.right() - 12 - i * 6
            painter.drawLine(QPointF(x, inner.bottom() - 9), QPointF(x, inner.bottom() - 4))
    elif spec.shape == "chamfered":
        # EDITH : contour discret + équerres vives sur deux coins.
        painter.setPen(QPen(_alpha(theme.text_dim, 0.28), 1.0))
        painter.drawPath(path)
        if glow > 0.05:
            painter.setPen(QPen(_alpha(accent, 0.10 * glow), 4.0))
            painter.drawPath(path)
        painter.setPen(QPen(_alpha(accent, 0.95), 2.0))
        L = min(18.0, inner.width() / 6)
        painter.drawLine(QPointF(inner.right() - L, inner.top()), QPointF(inner.right(), inner.top()))
        painter.drawLine(QPointF(inner.right(), inner.top()), QPointF(inner.right(), inner.top() + L))
        painter.drawLine(QPointF(inner.left(), inner.bottom() - L), QPointF(inner.left(), inner.bottom()))
        painter.drawLine(QPointF(inner.left(), inner.bottom()), QPointF(inner.left() + L, inner.bottom()))
        painter.setPen(QPen(_alpha(theme.accent2, 0.75), 1.0))
        c = float(min(spec.chamfer, inner.width() / 4, inner.height() / 4))
        painter.drawLine(QPointF(inner.left() + c + 4, inner.top() + 3), QPointF(inner.left() + c + 26, inner.top() + 3))
        if header_height > 0:
            painter.setPen(QPen(_alpha(theme.text_dim, 0.18), 1.0, Qt.DashLine))
            y = inner.top() + header_height
            painter.drawLine(QPointF(inner.left() + 8, y), QPointF(inner.right() - 8, y))
    else:
        # FRIDAY : reflet supérieur, liseré très doux, halo coloré.
        painter.setPen(QPen(QColor(255, 255, 255, 26), 1.0))
        painter.drawPath(path)
        hl = QPainterPath()
        r = float(min(spec.radius, inner.height() / 2))
        hl.addRoundedRect(QRectF(inner.left() + 1, inner.top() + 1, inner.width() - 2, inner.height() - 2), r, r)
        painter.setPen(QPen(QColor(255, 255, 255, 16), 1.0))
        painter.drawPath(hl)
        blob = QRadialGradient(QPointF(inner.left() + inner.width() * 0.12, inner.bottom() - 6), inner.width() * 0.45)
        blob.setColorAt(0.0, _alpha(accent, 0.10 * glow + 0.04 * highlight))
        blob.setColorAt(1.0, _alpha(accent, 0.0))
        painter.save()
        painter.setClipPath(path)
        painter.fillRect(inner, QBrush(blob))
        painter.restore()
        if highlight > 0:
            painter.setPen(QPen(_alpha(accent, 0.35 * highlight), 1.4))
            painter.drawPath(path)

    if edit_mode:
        painter.setPen(QPen(_alpha(accent, 0.9), 1.4, Qt.DashLine))
        painter.drawPath(path)
    elif active:
        painter.setPen(QPen(_alpha(accent, 0.55), 1.2))
        painter.drawPath(path)
    painter.restore()


class BackgroundRenderer:
    """Fond de fenêtre. Couche statique en cache + couche animée optionnelle."""

    def __init__(self) -> None:
        self._cache: QPixmap | None = None
        self._cache_key: tuple | None = None

    def invalidate(self) -> None:
        self._cache = None
        self._cache_key = None

    def _static_layer(self, size: QSize, theme: ResolvedTheme, dpr: float) -> QPixmap:
        key = (size.width(), size.height(), theme.spec.id, theme.accent.name(), round(theme.glow, 2), dpr)
        if self._cache is not None and self._cache_key == key:
            return self._cache
        pixmap = QPixmap(max(1, int(size.width() * dpr)), max(1, int(size.height() * dpr)))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(theme.bg_bottom)
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(size.width()), float(size.height())
        grad = QLinearGradient(QPointF(0, 0), QPointF(0, h))
        grad.setColorAt(0.0, theme.bg_top)
        grad.setColorAt(1.0, theme.bg_bottom)
        p.fillRect(QRectF(0, 0, w, h), QBrush(grad))
        bg = theme.spec.background
        if bg == "rings":
            center = QPointF(w * 0.78, h * 0.18)
            halo = QRadialGradient(center, max(w, h) * 0.55)
            halo.setColorAt(0.0, _alpha(theme.accent, 0.16 * theme.glow + 0.04))
            halo.setColorAt(1.0, _alpha(theme.accent, 0.0))
            p.fillRect(QRectF(0, 0, w, h), QBrush(halo))
            for i, radius in enumerate((110, 190, 280, 390, 520, 680)):
                pen = QPen(_alpha(theme.accent, 0.10 if i % 2 else 0.16), 1.0 if i % 2 else 1.4)
                if i % 2:
                    pen.setStyle(Qt.DashLine)
                p.setPen(pen)
                p.drawEllipse(center, float(radius), float(radius))
        elif bg == "grid":
            minor, major = 32, 128
            p.setPen(QPen(QColor(255, 255, 255, 9), 1.0))
            for x in range(0, int(w) + 1, minor):
                p.drawLine(QPointF(x, 0), QPointF(x, h))
            for y in range(0, int(h) + 1, minor):
                p.drawLine(QPointF(0, y), QPointF(w, y))
            p.setPen(QPen(_alpha(theme.accent, 0.10), 1.0))
            for x in range(0, int(w) + 1, major):
                p.drawLine(QPointF(x, 0), QPointF(x, h))
            for y in range(0, int(h) + 1, major):
                p.drawLine(QPointF(0, y), QPointF(w, y))
            p.setPen(QPen(_alpha(theme.accent, 0.45), 1.2))
            for cx, cy in ((w * 0.12, h * 0.85), (w * 0.9, h * 0.12), (w * 0.62, h * 0.7)):
                p.drawLine(QPointF(cx - 10, cy), QPointF(cx + 10, cy))
                p.drawLine(QPointF(cx, cy - 10), QPointF(cx, cy + 10))
                p.drawEllipse(QPointF(cx, cy), 16.0, 16.0)
            vignette = QRadialGradient(QPointF(w / 2, h / 2), max(w, h) * 0.75)
            vignette.setColorAt(0.6, QColor(0, 0, 0, 0))
            vignette.setColorAt(1.0, QColor(0, 0, 0, 110))
            p.fillRect(QRectF(0, 0, w, h), QBrush(vignette))
        else:  # aurora (statique)
            for (fx, fy, color, alpha) in ((0.2, 0.25, theme.accent, 0.22), (0.8, 0.3, theme.accent2, 0.18),
                                           (0.55, 0.85, theme.border, 0.16)):
                g = QRadialGradient(QPointF(w * fx, h * fy), max(w, h) * 0.45)
                g.setColorAt(0.0, _alpha(color, alpha * (0.6 + 0.6 * theme.glow)))
                g.setColorAt(1.0, _alpha(color, 0.0))
                p.fillRect(QRectF(0, 0, w, h), QBrush(g))
        p.end()
        self._cache = pixmap
        self._cache_key = key
        return pixmap

    def paint(self, painter: QPainter, size: QSize, theme: ResolvedTheme, phase: float, *,
              energy: float = 0.0, dpr: float = 1.0) -> None:
        painter.drawPixmap(0, 0, self._static_layer(size, theme, dpr))
        if theme.background_fps <= 0:
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(size.width()), float(size.height())
        rich = theme.animation_level == "pousse"
        bg = theme.spec.background
        if bg == "rings":
            center = QPointF(w * 0.78, h * 0.18)
            for i, radius in enumerate((150, 280, 450) if rich else (280,)):
                span = 50 + 20 * i
                start = (phase * (18 + 9 * i) * (1 if i % 2 == 0 else -1)) % 360
                pen = QPen(_alpha(theme.accent, 0.35 + 0.3 * energy), 2.0 if i == 0 else 1.4)
                pen.setCapStyle(Qt.RoundCap)
                painter.setPen(pen)
                rect = QRectF(center.x() - radius, center.y() - radius, 2.0 * radius, 2.0 * radius)
                painter.drawArc(rect, int(start * 16), int(span * 16))
                painter.drawArc(rect, int((start + 180) * 16), int(span * 0.5 * 16))
            if rich and energy > 0.02:
                painter.setPen(QPen(_alpha(theme.accent, 0.25 * energy), 3.0))
                painter.drawEllipse(center, 110.0 + 30.0 * energy, 110.0 + 30.0 * energy)
        elif bg == "grid":
            period = 9.0 if rich else 14.0
            y = (phase % period) / period * (h + 160) - 80
            band = QLinearGradient(QPointF(0, y - 60), QPointF(0, y + 6))
            band.setColorAt(0.0, _alpha(theme.accent, 0.0))
            band.setColorAt(0.9, _alpha(theme.accent, 0.06 if not rich else 0.09))
            band.setColorAt(1.0, _alpha(theme.accent, 0.35))
            painter.fillRect(QRectF(0, y - 60, w, 66), QBrush(band))
            if rich:
                cx, cy = w * 0.9, h * 0.12
                pr = (phase % 3.0) / 3.0
                painter.setPen(QPen(_alpha(theme.accent, 0.5 * (1 - pr)), 1.2))
                painter.drawEllipse(QPointF(cx, cy), 16 + 60 * pr, 16 + 60 * pr)
        else:  # aurora animée : basse résolution puis mise à l'échelle
            small = QPixmap(max(1, int(w / 6)), max(1, int(h / 6)))
            small.fill(Qt.transparent)
            sp = QPainter(small)
            sw, sh = float(small.width()), float(small.height())
            blobs = ((theme.accent, 0.20), (theme.accent2, 0.16), (theme.border, 0.14))
            for i, (color, alpha) in enumerate(blobs[: 3 if rich else 2]):
                t = phase * (0.05 + 0.03 * i)
                fx = 0.5 + 0.35 * math.sin(t * 2.0 + i * 2.1)
                fy = 0.5 + 0.30 * math.cos(t * 1.6 + i * 1.3)
                g = QRadialGradient(QPointF(sw * fx, sh * fy), max(sw, sh) * 0.5)
                g.setColorAt(0.0, _alpha(color, alpha * (1.0 + energy)))
                g.setColorAt(1.0, _alpha(color, 0.0))
                sp.fillRect(QRectF(0, 0, sw, sh), QBrush(g))
            sp.end()
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.drawPixmap(QRectF(0, 0, w, h), small, QRectF(0, 0, sw, sh))
        painter.restore()


def draw_sparkline(painter: QPainter, rect: QRectF, values: list[float], color: QColor,
                   max_value: float = 100.0, fill: bool = True) -> None:
    if len(values) < 2 or rect.width() < 4:
        return
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    top = max(max_value, max(values) or 1.0)
    step = rect.width() / (len(values) - 1)
    points = [QPointF(rect.left() + i * step, rect.bottom() - (max(0.0, v) / top) * rect.height())
              for i, v in enumerate(values)]
    path = QPainterPath(points[0])
    for point in points[1:]:
        path.lineTo(point)
    if fill:
        area = QPainterPath(path)
        area.lineTo(QPointF(points[-1].x(), rect.bottom()))
        area.lineTo(QPointF(points[0].x(), rect.bottom()))
        area.closeSubpath()
        g = QLinearGradient(rect.topLeft(), rect.bottomLeft())
        g.setColorAt(0.0, _alpha(color, 0.35))
        g.setColorAt(1.0, _alpha(color, 0.0))
        painter.fillPath(area, QBrush(g))
    painter.setPen(QPen(color, 1.6))
    painter.drawPath(path)
    painter.restore()


def draw_ring(painter: QPainter, rect: QRectF, fraction: float, color: QColor, track: QColor,
              width: float = 6.0, start_angle: float = 90.0) -> None:
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    inset = width / 2 + 1
    r = QRectF(rect.left() + inset, rect.top() + inset, rect.width() - 2 * inset, rect.height() - 2 * inset)
    pen = QPen(track, width)
    pen.setCapStyle(Qt.FlatCap)
    painter.setPen(pen)
    painter.drawEllipse(r)
    frac = max(0.0, min(1.0, fraction))
    if frac > 0:
        pen = QPen(color, width)
        pen.setCapStyle(Qt.RoundCap)
        painter.setPen(pen)
        painter.drawArc(r, int(start_angle * 16), int(-frac * 360 * 16))
    painter.restore()
