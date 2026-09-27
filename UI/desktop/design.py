"""Design system du Desktop Mode (v1.7.0).

Une **seule** source pour tous les nombres de l'interface Desktop : durées,
courbes, rayons, opacités, épaisseurs, marges, profils de halo. Raffiner
l'esthétique plus tard ne doit pas obliger à ouvrir trente fichiers.

Le module dépend de ``QColor`` / ``QEasingCurve`` (données Qt bon marché) mais
ne crée aucun widget : il reste importable dans un test sans ``QApplication``.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEasingCurve
from PySide6.QtGui import QColor

from .state import DesktopState

# ---------------------------------------------------------------------------
# Temps & courbes
# ---------------------------------------------------------------------------


class Motion:
    """Durées (ms) et courbes. Une transition doit se *sentir*, pas s'attendre."""

    #: Apparition du halo — volontairement très courte : l'utilisateur doit
    #: voir Jarvis réagir « avant d'avoir fini son mot ».
    APPEAR_MS = 170
    #: Disparition — un peu plus longue, pour ne pas « claquer ».
    DISAPPEAR_MS = 260
    #: Fondu enchaîné entre deux états visuels.
    CROSSFADE_MS = 220
    #: Apparition / disparition des widgets du HUD.
    WIDGET_IN_MS = 180
    WIDGET_OUT_MS = 150
    #: Glissement vertical des cartes (px) pendant leur apparition.
    WIDGET_SLIDE_PX = 14.0

    APPEAR_CURVE = QEasingCurve.Type.OutCubic
    DISAPPEAR_CURVE = QEasingCurve.Type.InOutCubic
    WIDGET_CURVE = QEasingCurve.Type.OutCubic

    #: Cadence de l'animation. 60 FPS quand ça bouge ; jamais quand le halo
    #: est masqué (le minuteur est arrêté, coût CPU nul).
    FRAME_MS = 16
    #: Cadence en mode « animations réduites » (accessibilité).
    FRAME_MS_REDUCED = 33
    #: Lissage exponentiel du niveau audio (0 = figé, 1 = brut).
    LEVEL_SMOOTHING = 0.22


# ---------------------------------------------------------------------------
# Géométrie & typographie
# ---------------------------------------------------------------------------


class Metrics:
    """Dimensions de base, exprimées avant application de l'échelle utilisateur."""

    #: Épaisseur de la lumière périphérique, en fraction du petit côté.
    HALO_BAND_RATIO = 0.135
    HALO_BAND_MIN = 64.0
    HALO_BAND_MAX = 260.0
    #: Rayon de l'adoucissement des coins du halo.
    HALO_CORNER_RATIO = 0.22

    #: Cartes (transcription, réponse).
    CARD_RADIUS = 14.0
    CARD_PADDING_X = 18.0
    CARD_PADDING_Y = 12.0
    CARD_MAX_WIDTH_RATIO = 0.46
    CARD_MIN_WIDTH = 220.0

    #: Pastilles (état, outil).
    CHIP_RADIUS = 13.0
    CHIP_PADDING_X = 14.0
    CHIP_PADDING_Y = 7.0
    CHIP_DOT = 7.0

    #: Visualiseur audio.
    BARS = 7
    BAR_WIDTH = 4.0
    BAR_GAP = 4.0
    BAR_MAX_HEIGHT = 26.0
    BAR_RADIUS = 2.0

    #: Barre de contrôles.
    BUTTON_SIZE = 34.0
    BUTTON_GAP = 8.0
    BUTTON_RADIUS = 10.0

    #: Marge minimale entre un widget et le bord de l'écran.
    SCREEN_MARGIN = 24.0

    FONT_FAMILY = "Segoe UI"
    FONT_SIZE_CHIP = 11
    FONT_SIZE_CARD = 13
    FONT_SIZE_SMALL = 10


class Opacities:
    """Opacités de référence (0..1)."""

    HALO_BASE = 0.55
    HALO_PEAK = 0.92
    CARD_BACKGROUND = 0.82
    CHIP_BACKGROUND = 0.78
    BORDER = 0.30
    TEXT = 0.96
    TEXT_DIM = 0.62


#: Fond sombre commun aux cartes et pastilles : neutre, lisible sur n'importe
#: quel bureau, jamais une « fenêtre Windows ».
SURFACE = QColor(8, 13, 20)
SURFACE_LIGHT = QColor(18, 27, 38)


# ---------------------------------------------------------------------------
# Profils de halo par état
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HaloProfile:
    """Signature visuelle d'un état.

    Chaque état a une **forme** différente, pas seulement une intensité :
    c'est ce qui rend l'état identifiable d'un coup d'œil.
    """

    key: str
    #: Décalage de teinte appliqué à la couleur du thème (-0.5 .. 0.5).
    hue_shift: float
    #: Correction de luminosité appliquée à la couleur du thème.
    lightness: float
    #: Couleur imposée (états où l'ambiguïté est interdite : erreur).
    forced: QColor | None
    #: Poids de chaque bord : haut, droite, bas, gauche.
    edges: tuple[float, float, float, float]
    #: Opacité de base du halo.
    alpha: float
    #: Amplitude et fréquence de la respiration.
    breathe: float
    breathe_hz: float
    #: Intensité et vitesse de l'accent qui circule le long du périmètre.
    sweep: float
    sweep_hz: float
    #: Nombre de segments pulsés (exécution d'outil).
    ticks: int
    #: Sensibilité au niveau audio réel.
    audio: float
    #: Ligne de compte à rebours (fenêtre d'écoute après réponse).
    countdown: bool


def _profile(key: str, **kwargs) -> HaloProfile:
    base = dict(
        hue_shift=0.0,
        lightness=0.0,
        forced=None,
        edges=(1.0, 1.0, 1.0, 1.0),
        alpha=Opacities.HALO_BASE,
        breathe=0.12,
        breathe_hz=0.45,
        sweep=0.0,
        sweep_hz=0.0,
        ticks=0,
        audio=0.0,
        countdown=False,
    )
    base.update(kwargs)
    return HaloProfile(key=key, **base)


#: Profils par état. Les valeurs sont le fruit d'un compromis « présence sans
#: gêne » : jamais plus de ~0.9 d'opacité, jamais de bord franc.
HALO_PROFILES: dict[str, HaloProfile] = {
    DesktopState.HIDDEN: _profile(
        DesktopState.HIDDEN, alpha=0.0, breathe=0.0, breathe_hz=0.0
    ),
    DesktopState.LOADING: _profile(
        DesktopState.LOADING,
        lightness=-0.10,
        edges=(0.25, 0.35, 0.70, 0.35),
        alpha=0.28,
        breathe=0.10,
        breathe_hz=0.30,
    ),
    # ÉCOUTE : la lumière monte du bas de l'écran — Jarvis « vient à soi ».
    DesktopState.LISTENING: _profile(
        DesktopState.LISTENING,
        lightness=0.04,
        edges=(0.34, 0.62, 1.0, 0.62),
        alpha=0.58,
        breathe=0.16,
        breathe_hz=0.52,
        audio=0.55,
    ),
    # RÉFLEXION : un accent unique circule lentement autour de l'écran. Ce
    # n'est pas un « spinner » : rien ne tourne en rond au même endroit.
    DesktopState.THINKING: _profile(
        DesktopState.THINKING,
        hue_shift=0.075,
        lightness=-0.02,
        edges=(0.72, 0.72, 0.72, 0.72),
        alpha=0.52,
        breathe=0.07,
        breathe_hz=0.28,
        sweep=0.85,
        sweep_hz=0.21,
    ),
    # ACTION : segments qui s'allument en séquence sur le bord haut — la
    # lecture « Jarvis fait quelque chose » est immédiate.
    DesktopState.TOOL_USE: _profile(
        DesktopState.TOOL_USE,
        hue_shift=-0.115,
        lightness=0.06,
        edges=(1.0, 0.55, 0.55, 0.55),
        alpha=0.60,
        breathe=0.05,
        breathe_hz=0.9,
        ticks=7,
    ),
    # RÉPONSE : pulsation symétrique gauche/droite pilotée par la voix.
    DesktopState.SPEAKING: _profile(
        DesktopState.SPEAKING,
        lightness=0.14,
        edges=(0.55, 1.0, 0.72, 1.0),
        alpha=0.66,
        breathe=0.10,
        breathe_hz=0.8,
        audio=0.85,
    ),
    # APRÈS RÉPONSE : même famille que l'écoute, plus discrète, avec une ligne
    # qui se résorbe : la fenêtre se referme, et ça se voit.
    DesktopState.FOLLOW_UP: _profile(
        DesktopState.FOLLOW_UP,
        lightness=0.0,
        edges=(0.20, 0.45, 0.85, 0.45),
        alpha=0.40,
        breathe=0.12,
        breathe_hz=0.40,
        audio=0.45,
        countdown=True,
    ),
    DesktopState.INTERRUPTED: _profile(
        DesktopState.INTERRUPTED,
        lightness=-0.18,
        edges=(0.4, 0.4, 0.4, 0.4),
        alpha=0.30,
        breathe=0.0,
        breathe_hz=0.0,
    ),
    DesktopState.ERROR: _profile(
        DesktopState.ERROR,
        forced=QColor(255, 96, 96),
        edges=(1.0, 1.0, 1.0, 1.0),
        alpha=0.52,
        breathe=0.22,
        breathe_hz=1.5,
    ),
}


def profile_for(state: str) -> HaloProfile:
    return HALO_PROFILES.get(state, HALO_PROFILES[DesktopState.HIDDEN])


def state_color(theme_color: QColor, profile: HaloProfile) -> QColor:
    """Couleur du halo : dérivée du thème de l'orbe, décalée selon l'état.

    Jarvis garde son identité (changer le thème de l'orbe change le Desktop
    Mode) tout en rendant les états distinguables. Un état où l'ambiguïté est
    interdite (erreur) impose sa couleur.
    """
    if profile.forced is not None:
        return QColor(profile.forced)
    color = QColor(theme_color) if theme_color is not None else QColor(70, 180, 255)
    if not color.isValid():
        color = QColor(70, 180, 255)
    hue, saturation, lightness, _alpha = color.getHslF()
    if hue is None or hue < 0.0:
        hue, saturation = 0.55, 0.75
    hue = (hue + profile.hue_shift) % 1.0
    lightness = max(0.0, min(1.0, lightness + profile.lightness))
    saturation = max(0.0, min(1.0, saturation * 1.02))
    return QColor.fromHslF(hue, saturation, lightness)


def text_color_for(theme_text: QColor) -> QColor:
    color = QColor(theme_text) if theme_text is not None else QColor(214, 236, 255)
    if not color.isValid():
        color = QColor(214, 236, 255)
    color.setAlpha(255)
    return color


def with_alpha(color: QColor, alpha: float) -> QColor:
    out = QColor(color)
    out.setAlpha(max(0, min(255, int(round(alpha * 255)))))
    return out
