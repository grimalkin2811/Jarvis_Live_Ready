"""Rendu du halo périphérique du Desktop Mode (v1.7.0).

Objectif de performance
-----------------------
L'implémentation 1.6.0 repeignait **tout l'écran** à 60 Hz avec deux
``QPainterPath`` soustraits et huit dégradés radiaux. À 1440p/4K, c'est le
chemin le plus coûteux qui soit sur le moteur raster.

Ici :

* la lumière de chaque bord est pré-rendue **une fois** dans un ``QPixmap``
  (mis en cache par taille + couleur + bord) ; chaque image ne fait que la
  blitter avec une opacité — l'opération la plus rapide de Qt ;
* le repaint est **limité aux quatre bandes** (``region()``), jamais à
  l'intérieur de l'écran qui reste intégralement vide ;
* les accents animés (balayage, segments, compte à rebours) utilisent un
  unique sprite radial mis en cache ;
* à l'état masqué, le renderer ne dessine rien et le contrôleur arrête son
  minuteur : coût CPU nul.

Le changement d'état est un **fondu enchaîné** entre deux profils (deux jeux
de bandes vivants au maximum), ce qui lit comme un morphing de couleur sans
recalculer un dégradé par image.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QLinearGradient,
    QPainter,
    QPixmap,
    QRadialGradient,
    QRegion,
)

from .design import HaloProfile, Metrics, Opacities, profile_for, state_color, with_alpha
from .state import DesktopState

#: Index des bords.
TOP, RIGHT, BOTTOM, LEFT = 0, 1, 2, 3
EDGES = (TOP, RIGHT, BOTTOM, LEFT)

#: Taille du sprite radial réutilisé par tous les accents.
_SPRITE_SIZE = 192

#: Nombre maximal de jeux de bandes gardés en cache (courant + précédent, plus
#: une marge pour les changements de thème).
_CACHE_LIMIT = 12


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class HaloRenderer:
    """Dessine la lumière périphérique et ses accents."""

    def __init__(self) -> None:
        self._size = QSize(0, 0)
        self._band = 0.0
        self._theme = QColor(70, 180, 255)
        self._profile: HaloProfile = profile_for(DesktopState.HIDDEN)
        self._previous: HaloProfile = profile_for(DesktopState.HIDDEN)
        self._blend = 1.0
        self._phase = 0.0
        self._level = 0.0
        self._progress = 1.0
        self._intensity = 1.0
        self._thickness = 1.0
        self._motion = 1.0
        self._audio_reactive = True
        self._bands: dict[tuple, QPixmap] = {}
        self._sprite_cache: dict[int, QPixmap] = {}

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def configure(
        self,
        *,
        intensity: float = 1.0,
        thickness: float = 1.0,
        motion: float = 1.0,
        audio_reactive: bool = True,
    ) -> None:
        thickness = _clamp(float(thickness), 0.5, 1.8)
        if abs(thickness - self._thickness) > 1e-3:
            self._thickness = thickness
            self._bands.clear()
            self._recompute_band()
        self._intensity = _clamp(float(intensity), 0.2, 1.6)
        self._motion = _clamp(float(motion), 0.0, 1.0)
        self._audio_reactive = bool(audio_reactive)

    def set_theme(self, glow: QColor) -> None:
        color = QColor(glow) if glow is not None else QColor(70, 180, 255)
        if not color.isValid():
            color = QColor(70, 180, 255)
        if color.rgb() == self._theme.rgb():
            return
        self._theme = color
        self._bands.clear()

    def resize(self, size: QSize) -> None:
        if size == self._size:
            return
        self._size = QSize(size)
        self._bands.clear()
        self._recompute_band()

    def _recompute_band(self) -> None:
        side = float(min(self._size.width(), self._size.height()))
        if side <= 0:
            self._band = 0.0
            return
        raw = side * Metrics.HALO_BAND_RATIO * self._thickness
        self._band = _clamp(raw, Metrics.HALO_BAND_MIN, Metrics.HALO_BAND_MAX)

    # ------------------------------------------------------------------
    # État
    # ------------------------------------------------------------------

    def set_state(self, state: str, *, immediate: bool = False) -> None:
        profile = profile_for(state)
        if profile.key == self._profile.key:
            return
        self._previous = self._profile
        self._profile = profile
        self._blend = 1.0 if immediate else 0.0

    @property
    def state(self) -> str:
        return self._profile.key

    def set_level(self, level: float) -> None:
        try:
            value = float(level)
        except (TypeError, ValueError):
            return
        self._level = _clamp(value, 0.0, 1.0)

    def set_progress(self, fraction: float) -> None:
        """Fraction restante d'une fenêtre temporelle (compte à rebours)."""
        self._progress = _clamp(float(fraction), 0.0, 1.0)

    def advance(self, dt: float, blend_step: float) -> None:
        """Avance le temps d'animation. ``dt`` en secondes."""
        self._phase = (self._phase + dt) % 3600.0
        if self._blend < 1.0:
            self._blend = min(1.0, self._blend + max(0.0, blend_step))

    def is_idle(self) -> bool:
        """Vrai quand plus rien ne bouge (le contrôleur peut couper le timer)."""
        return (
            self._profile.key == DesktopState.HIDDEN
            and self._previous.key == DesktopState.HIDDEN
            and self._blend >= 1.0
        )

    # ------------------------------------------------------------------
    # Géométrie
    # ------------------------------------------------------------------

    def band_thickness(self) -> float:
        return self._band

    def edge_rects(self) -> tuple[QRectF, QRectF, QRectF, QRectF]:
        width = float(self._size.width())
        height = float(self._size.height())
        band = self._band
        return (
            QRectF(0.0, 0.0, width, band),
            QRectF(width - band, 0.0, band, height),
            QRectF(0.0, height - band, width, band),
            QRectF(0.0, 0.0, band, height),
        )

    def region(self) -> QRegion:
        """Zone à repeindre : uniquement les bandes, jamais le centre."""
        region = QRegion()
        for rect in self.edge_rects():
            region = region.united(QRegion(rect.toAlignedRect()))
        return region

    # ------------------------------------------------------------------
    # Ressources mises en cache
    # ------------------------------------------------------------------

    def _band_pixmap(self, edge: int, color: QColor) -> QPixmap | None:
        rect = self.edge_rects()[edge]
        width = max(1, int(round(rect.width())))
        height = max(1, int(round(rect.height())))
        key = (edge, width, height, color.rgb())
        cached = self._bands.get(key)
        if cached is not None:
            return cached
        if width <= 1 or height <= 1:
            return None
        if len(self._bands) >= _CACHE_LIMIT:
            self._bands.clear()

        pixmap = QPixmap(width, height)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.Antialiasing, False)
            if edge == TOP:
                gradient = QLinearGradient(0.0, 0.0, 0.0, float(height))
            elif edge == BOTTOM:
                gradient = QLinearGradient(0.0, float(height), 0.0, 0.0)
            elif edge == LEFT:
                gradient = QLinearGradient(0.0, 0.0, float(width), 0.0)
            else:
                gradient = QLinearGradient(float(width), 0.0, 0.0, 0.0)
            # Profil de chute non linéaire : un noyau dense collé au bord puis
            # une longue traîne — c'est ce qui donne « de la lumière » et non
            # « un cadre ».
            gradient.setColorAt(0.00, with_alpha(color, 1.00))
            gradient.setColorAt(0.16, with_alpha(color, 0.62))
            gradient.setColorAt(0.38, with_alpha(color, 0.28))
            gradient.setColorAt(0.66, with_alpha(color, 0.09))
            gradient.setColorAt(1.00, with_alpha(color, 0.0))
            painter.fillRect(0, 0, width, height, gradient)
        finally:
            painter.end()
        self._bands[key] = pixmap
        return pixmap

    def _sprite(self, color: QColor) -> QPixmap:
        key = color.rgb()
        cached = self._sprite_cache.get(key)
        if cached is not None:
            return cached
        if len(self._sprite_cache) >= 8:
            self._sprite_cache.clear()
        pixmap = QPixmap(_SPRITE_SIZE, _SPRITE_SIZE)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            center = _SPRITE_SIZE / 2.0
            gradient = QRadialGradient(center, center, center)
            gradient.setColorAt(0.0, with_alpha(color, 0.95))
            gradient.setColorAt(0.35, with_alpha(color, 0.45))
            gradient.setColorAt(0.72, with_alpha(color, 0.12))
            gradient.setColorAt(1.0, with_alpha(color, 0.0))
            painter.setPen(Qt.NoPen)
            painter.setBrush(gradient)
            painter.drawEllipse(0, 0, _SPRITE_SIZE, _SPRITE_SIZE)
        finally:
            painter.end()
        self._sprite_cache[key] = pixmap
        return pixmap

    # ------------------------------------------------------------------
    # Rendu
    # ------------------------------------------------------------------

    def paint(self, painter: QPainter, opacity: float = 1.0) -> None:
        """Dessine le halo. ``opacity`` porte le fondu d'apparition global."""
        if self._band <= 0.0 or opacity <= 0.002:
            return
        blend = _clamp(self._blend, 0.0, 1.0)
        layers: list[tuple[HaloProfile, float]] = []
        if blend < 1.0 and self._previous.key != self._profile.key:
            layers.append((self._previous, 1.0 - blend))
        layers.append((self._profile, blend))

        for profile, weight in layers:
            if weight <= 0.002 or profile.alpha <= 0.0:
                continue
            self._paint_profile(painter, profile, opacity * weight)

    def _paint_profile(self, painter: QPainter, profile: HaloProfile, weight: float) -> None:
        color = state_color(self._theme, profile)
        breathe = 1.0
        if profile.breathe_hz > 0.0 and profile.breathe > 0.0:
            wave = math.sin(self._phase * profile.breathe_hz * math.tau)
            breathe = 1.0 + profile.breathe * self._motion * wave
        audio = 0.0
        if self._audio_reactive and profile.audio > 0.0:
            audio = profile.audio * self._level * (0.35 + 0.65 * self._motion)

        base = profile.alpha * self._intensity * breathe * (1.0 + audio * 0.55)
        base = _clamp(base, 0.0, Opacities.HALO_PEAK)

        rects = self.edge_rects()
        for edge in EDGES:
            edge_weight = profile.edges[edge]
            if edge_weight <= 0.01:
                continue
            pixmap = self._band_pixmap(edge, color)
            if pixmap is None:
                continue
            alpha = _clamp(base * edge_weight * weight, 0.0, 1.0)
            if alpha <= 0.003:
                continue
            painter.setOpacity(alpha)
            painter.drawPixmap(rects[edge].topLeft(), pixmap)

        painter.setOpacity(1.0)
        if profile.sweep > 0.0:
            self._paint_sweep(painter, profile, color, weight)
        if profile.ticks > 0:
            self._paint_ticks(painter, profile, color, weight)
        if profile.countdown:
            self._paint_countdown(painter, color, weight)
        painter.setOpacity(1.0)

    # -- accents ------------------------------------------------------------

    def _perimeter_point(self, t: float) -> QPointF:
        """Point du périmètre pour ``t`` dans [0, 1) (sens horaire, haut-gauche)."""
        width = float(self._size.width())
        height = float(self._size.height())
        total = 2.0 * (width + height)
        if total <= 0.0:
            return QPointF(0.0, 0.0)
        distance = (t % 1.0) * total
        if distance < width:
            return QPointF(distance, 0.0)
        distance -= width
        if distance < height:
            return QPointF(width, distance)
        distance -= height
        if distance < width:
            return QPointF(width - distance, height)
        distance -= width
        return QPointF(0.0, height - distance)

    def _paint_sweep(self, painter: QPainter, profile: HaloProfile, color: QColor, weight: float) -> None:
        sprite = self._sprite(color)
        speed = profile.sweep_hz * (0.4 + 0.6 * self._motion)
        radius = self._band * 1.15
        # Deux accents opposés : la circulation se lit sans avoir à suivre un
        # point unique du regard.
        for offset in (0.0, 0.5):
            t = (self._phase * speed + offset) % 1.0
            point = self._perimeter_point(t)
            alpha = _clamp(profile.sweep * weight * self._intensity, 0.0, 1.0)
            if alpha <= 0.004:
                continue
            painter.setOpacity(alpha)
            painter.drawPixmap(
                QRectF(point.x() - radius, point.y() - radius, radius * 2.0, radius * 2.0),
                sprite,
                QRectF(0.0, 0.0, float(_SPRITE_SIZE), float(_SPRITE_SIZE)),
            )

    def _paint_ticks(self, painter: QPainter, profile: HaloProfile, color: QColor, weight: float) -> None:
        count = max(1, int(profile.ticks))
        width = float(self._size.width())
        if width <= 0.0:
            return
        slot = width / float(count)
        tick_w = slot * 0.46
        tick_h = max(3.0, self._band * 0.055)
        painter.setPen(Qt.NoPen)
        for index in range(count):
            phase = (self._phase * 1.35 * (0.4 + 0.6 * self._motion) - index * 0.12) % 1.0
            pulse = max(0.0, 1.0 - abs(phase - 0.25) * 3.2)
            alpha = _clamp((0.18 + 0.72 * pulse) * weight * self._intensity, 0.0, 1.0)
            if alpha <= 0.01:
                continue
            painter.setOpacity(alpha)
            painter.setBrush(with_alpha(color, 1.0))
            x = slot * index + (slot - tick_w) / 2.0
            painter.drawRoundedRect(QRectF(x, 0.0, tick_w, tick_h), tick_h / 2.0, tick_h / 2.0)

    def _paint_countdown(self, painter: QPainter, color: QColor, weight: float) -> None:
        width = float(self._size.width())
        height = float(self._size.height())
        if width <= 0.0 or height <= 0.0:
            return
        fraction = _clamp(self._progress, 0.0, 1.0)
        if fraction <= 0.001:
            return
        line_h = max(2.0, self._band * 0.032)
        line_w = width * 0.34 * fraction
        if line_w <= 1.0:
            return
        painter.setPen(Qt.NoPen)
        painter.setOpacity(_clamp(0.55 * weight * self._intensity, 0.0, 1.0))
        painter.setBrush(with_alpha(color, 1.0))
        painter.drawRoundedRect(
            QRectF((width - line_w) / 2.0, height - line_h - 2.0, line_w, line_h),
            line_h / 2.0,
            line_h / 2.0,
        )

    # ------------------------------------------------------------------
    # Diagnostic
    # ------------------------------------------------------------------

    def cache_size(self) -> int:
        """Nombre de pixmaps en cache (utilisé par les tests de performance)."""
        return len(self._bands) + len(self._sprite_cache)

    def clear_cache(self) -> None:
        self._bands.clear()
        self._sprite_cache.clear()

    def debug_rect(self) -> QRect:
        return QRect(0, 0, self._size.width(), self._size.height())
