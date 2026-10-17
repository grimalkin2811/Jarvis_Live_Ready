"""Thèmes du Dashboard : JARVIS, EDITH, FRIDAY.

Trois **identités** distinctes (et non trois palettes du même gabarit) :

* **JARVIS** — bleu nuit, cyan, anneaux concentriques ; panneaux arrondis à
  liseré lumineux et arcs de cercle ; transitions en fondu-zoom.
* **EDITH** — graphite tactique, rouge/ambre ; panneaux à coins chanfreinés,
  repères d'angle et grille de visée ; libellés capitales à chasse fixe ;
  transitions en glissement latéral.
* **FRIDAY** — violet profond, rose/corail et turquoise ; panneaux « verre »
  très arrondis, ombre douce, aurores en arrière-plan ; typographie fine ;
  transitions en fondu ascendant.

Personnalisation (persistée, application à chaud) : accent, intensité de
lueur, transparence des panneaux, densité, niveau d'animation (léger /
normal / poussé) avec respect des préférences d'accessibilité du système.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, replace

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QColor

from .config import AppearancePrefs, ConfigStore


@dataclass(frozen=True)
class ThemeSpec:
    id: str
    name: str
    tagline: str
    bg_top: str
    bg_bottom: str
    panel: str
    panel_alt: str
    border: str
    text: str
    text_dim: str
    accent: str
    accent2: str
    success: str
    warning: str
    danger: str
    shape: str  # rounded | chamfered | soft
    radius: int
    chamfer: int
    background: str  # rings | grid | aurora
    title_upper: bool
    title_spacing: float
    font_ui: tuple[str, ...]
    font_title: tuple[str, ...]
    font_mono: tuple[str, ...]
    transition: str  # fade_scale | slide | rise
    glow_base: float


THEMES: dict[str, ThemeSpec] = {
    "jarvis": ThemeSpec(
        id="jarvis", name="JARVIS", tagline="Anneaux bleus, lueur cyan, précision",
        bg_top="#071425", bg_bottom="#02060d", panel="#0b1d31", panel_alt="#0f2740",
        border="#2fc6ff", text="#dff6ff", text_dim="#7fa9c4", accent="#36d1ff", accent2="#1f6fff",
        success="#3ee0a1", warning="#ffc65c", danger="#ff5f6d",
        shape="rounded", radius=14, chamfer=0, background="rings",
        title_upper=True, title_spacing=1.6,
        font_ui=("Segoe UI", "Inter", "Noto Sans", "DejaVu Sans"),
        font_title=("Segoe UI Semibold", "Segoe UI", "Inter", "DejaVu Sans"),
        font_mono=("Cascadia Mono", "Consolas", "DejaVu Sans Mono"),
        transition="fade_scale", glow_base=0.85,
    ),
    "edith": ThemeSpec(
        id="edith", name="EDITH", tagline="Panneaux tactiques, géométrie, alerte",
        bg_top="#101113", bg_bottom="#060607", panel="#15171a", panel_alt="#1c1f23",
        border="#ff3b3b", text="#f1f1f1", text_dim="#9b9ea3", accent="#ff3b3b", accent2="#ffb020",
        success="#7ddc6b", warning="#ffb020", danger="#ff3b3b",
        shape="chamfered", radius=2, chamfer=14, background="grid",
        title_upper=True, title_spacing=2.4,
        font_ui=("Segoe UI", "Bahnschrift", "DejaVu Sans"),
        font_title=("Consolas", "Cascadia Mono", "DejaVu Sans Mono"),
        font_mono=("Consolas", "Cascadia Mono", "DejaVu Sans Mono"),
        transition="slide", glow_base=0.55,
    ),
    "friday": ThemeSpec(
        id="friday", name="FRIDAY", tagline="Verre dépoli, aurores, douceur",
        bg_top="#1b1036", bg_bottom="#0a0716", panel="#231743", panel_alt="#2c1f52",
        border="#b48cff", text="#f6f1ff", text_dim="#ab9ccc", accent="#ff7ac6", accent2="#5ee6d0",
        success="#5ee6a8", warning="#ffd27a", danger="#ff6b8b",
        shape="soft", radius=22, chamfer=0, background="aurora",
        title_upper=False, title_spacing=0.3,
        font_ui=("Segoe UI Variable Text", "Segoe UI", "Inter", "DejaVu Sans"),
        font_title=("Segoe UI Variable Display", "Segoe UI Light", "Segoe UI", "DejaVu Sans"),
        font_mono=("Cascadia Mono", "Consolas", "DejaVu Sans Mono"),
        transition="rise", glow_base=0.65,
    ),
}

DENSITY_METRICS = {
    # gap (px entre widgets), padding interne, échelle de police, hauteur de ligne de grille
    "compact": (8, 8, 0.92, 54),
    "normal": (12, 12, 1.0, 60),
    "confortable": (16, 16, 1.08, 68),
}

#: Animations continues, par niveau. « léger » : aucune (zéro minuteur).
#: « normal » : seul l'emblème (34 px) est animé. « poussé » : emblème plus
#: fluide + fond animé à cadence réduite (le coût de repeinture du fond est
#: borné par le cache des cadres de widgets, cf. frame.py).
#: Polices de SYMBOLES (police PRINCIPALE des glyphes de l'interface), par ordre
#: de préférence ; la première installée est retenue.
SYMBOL_FAMILIES = ("Segoe UI Symbol", "Apple Symbols", "Noto Sans Symbols 2", "DejaVu Sans")

# Correctif D01 (validation Windows PR #50 : première ouverture 1 867 ms sur le
# PC de validation), cause établie par une sonde sur un Windows NATIF : Qt 6.11
# construit la liste de repli des polices du SYSTÈME — 155 fichiers de police
# chargés, 3,5 s à froid — (1) dès qu'une police porte PLUSIEURS familles
# (les chaînes « "Segoe UI", "Inter", … » des thèmes, même pour du texte
# latin) et (2) dès qu'un glyphe manque à la police principale (les symboles
# ⌂ ◈ ◎ ☑ ⚙ ✎ ▤ ▦ ◫ ⤢ ✉ ⋯ ✕ ne sont pas dans Segoe UI). Les familles
# secondaires ne sont pas consultées avant ce repli. D'où : UNE famille
# installée par rôle (résolue ici), et les symboles rendus avec une police de
# symboles comme police PRINCIPALE (``UI/dashboard/glyphs.py``).
_INSTALLED_FAMILIES: frozenset[str] | None = None


def installed_families() -> frozenset[str]:
    """Familles installées (liste des noms seulement : aucun fichier chargé)."""
    global _INSTALLED_FAMILIES
    if _INSTALLED_FAMILIES is None:
        try:
            from PySide6.QtGui import QFontDatabase, QGuiApplication

            if QGuiApplication.instance() is None:
                return frozenset()
            _INSTALLED_FAMILIES = frozenset(QFontDatabase.families())
        except Exception:
            return frozenset()
    return _INSTALLED_FAMILIES


def first_installed(families) -> str:
    """Première famille installée de la liste (à défaut : la première de la liste)."""
    installed = installed_families()
    for name in families:
        if name in installed:
            return name
    return families[0] if families else ""


def symbol_family() -> str:
    """Police de symboles installée, ou « » si aucune (le repli de Qt s'applique).

    « Aucune » arrive quand la base de polices n'est pas lisible : plateforme Qt
    sans registre de polices (par exemple ``offscreen`` sous Windows, où
    ``QFontDatabase.families()`` est vide) — c'est le cas des tests de la CI
    Windows. L'interface dégrade alors comme avant le correctif D01 : les
    glyphes sont rendus par la police d'interface, sans règle de style dédiée.
    Sur une plateforme réelle (``windows``), la famille est toujours trouvée.
    """
    installed = installed_families()
    return next((name for name in SYMBOL_FAMILIES if name in installed), "")


EMBLEM_FPS = {"leger": 0, "normal": 15, "pousse": 30}
BACKGROUND_FPS = {"leger": 0, "normal": 0, "pousse": 8}
TRANSITION_MS = {"leger": 0, "normal": 200, "pousse": 300}


def system_prefers_reduced_motion() -> bool:
    """Préférence d'accessibilité du système (animations réduites).

    Windows : ``SPI_GETCLIENTAREAANIMATION`` (« Afficher les animations dans
    Windows »). Ailleurs, ou en cas d'échec : variable
    ``JARVIS_REDUCED_MOTION`` (1/0), sinon pas de réduction.
    """
    override = os.environ.get("JARVIS_REDUCED_MOTION", "").strip().lower()
    if override in {"1", "true", "oui", "yes"}:
        return True
    if override in {"0", "false", "non", "no"}:
        return False
    if sys.platform.startswith("win"):
        try:
            import ctypes

            enabled = ctypes.c_int(1)
            SPI_GETCLIENTAREAANIMATION = 0x1042
            if ctypes.windll.user32.SystemParametersInfoW(SPI_GETCLIENTAREAANIMATION, 0, ctypes.byref(enabled), 0):
                return not bool(enabled.value)
        except Exception:
            return False
    return False


@dataclass(frozen=True)
class ResolvedTheme:
    spec: ThemeSpec
    accent: QColor
    accent2: QColor
    panel: QColor  # alpha appliquée
    panel_alt: QColor
    border: QColor
    text: QColor
    text_dim: QColor
    bg_top: QColor
    bg_bottom: QColor
    success: QColor
    warning: QColor
    danger: QColor
    glow: float  # 0..1
    gap: int
    padding: int
    font_scale: float
    row_height: int
    animation_level: str
    reduced_by_system: bool

    @property
    def emblem_fps(self) -> int:
        return EMBLEM_FPS.get(self.animation_level, 0)

    @property
    def background_fps(self) -> int:
        return BACKGROUND_FPS.get(self.animation_level, 0)

    @property
    def fps(self) -> int:
        """Cadence maximale de toutes les animations continues."""
        return max(self.emblem_fps, self.background_fps)

    @property
    def transition_ms(self) -> int:
        return TRANSITION_MS.get(self.animation_level, 0)

    def status_color(self, status: str) -> QColor:
        return {
            "ok": self.success, "operational": self.success, "success": self.success,
            "warning": self.warning, "degraded": self.warning,
            "error": self.danger, "unavailable": self.danger,
        }.get(status, self.text_dim)

    def font_family(self, kind: str = "ui") -> str:
        """UNE famille installée (jamais une chaîne : cf. correctif D01 ci-dessus)."""
        families = {"ui": self.spec.font_ui, "title": self.spec.font_title, "mono": self.spec.font_mono}[kind]
        return f'"{first_installed(families)}"'

    def title(self, text: str) -> str:
        return text.upper() if self.spec.title_upper else text


def resolve(prefs: AppearancePrefs, reduced_motion: bool | None = None) -> ResolvedTheme:
    spec = THEMES.get(prefs.theme, THEMES["jarvis"])
    accent = QColor(prefs.accent) if prefs.accent and QColor(prefs.accent).isValid() else QColor(spec.accent)
    panel = QColor(spec.panel)
    panel.setAlpha(int(255 * max(0.4, min(1.0, prefs.panel_opacity / 100.0))))
    panel_alt = QColor(spec.panel_alt)
    panel_alt.setAlpha(min(255, panel.alpha() + 20))
    border = QColor(spec.border) if not prefs.accent else QColor(accent)
    gap, padding, scale, row = DENSITY_METRICS.get(prefs.density, DENSITY_METRICS["normal"])
    reduced = system_prefers_reduced_motion() if reduced_motion is None else bool(reduced_motion)
    level = prefs.animation_level
    reduced_by_system = False
    if prefs.respect_system_motion and reduced and level != "leger":
        level = "leger"
        reduced_by_system = True
    return ResolvedTheme(
        spec=spec, accent=accent, accent2=QColor(spec.accent2), panel=panel, panel_alt=panel_alt, border=border,
        text=QColor(spec.text), text_dim=QColor(spec.text_dim), bg_top=QColor(spec.bg_top),
        bg_bottom=QColor(spec.bg_bottom), success=QColor(spec.success), warning=QColor(spec.warning),
        danger=QColor(spec.danger), glow=max(0.0, min(1.0, prefs.glow / 100.0)) * spec.glow_base,
        gap=gap, padding=padding, font_scale=scale, row_height=row, animation_level=level,
        reduced_by_system=reduced_by_system,
    )


def _rgba(color: QColor, alpha: int | None = None) -> str:
    a = color.alpha() if alpha is None else alpha
    return f"rgba({color.red()}, {color.green()}, {color.blue()}, {a})"


def stylesheet(theme: ResolvedTheme) -> str:
    """Feuille de style Qt des contrôles standards (les cadres sont peints)."""
    t = theme
    base = round(13 * t.font_scale)
    small = max(10, round(11.5 * t.font_scale))
    radius = {"rounded": 8, "chamfered": 2, "soft": 12}[t.spec.shape]
    accent = t.accent.name()
    symbol = symbol_family()
    # Éléments qui n'affichent qu'un glyphe (``glyphs.mark``) : police de symboles
    # PRINCIPALE, pour qu'aucun repli des polices du système ne soit déclenché.
    glyph_rule = f'*[glyph="true"] {{ font-family: "{symbol}"; }}' if symbol else ""
    return f"""
    QWidget {{ color: {t.text.name()}; font-family: {t.font_family('ui')}; font-size: {base}px; }}
    QMainWindow, QWidget#DashboardRoot {{ background: transparent; }}
    QToolTip {{ background: {t.panel_alt.name()}; color: {t.text.name()}; border: 1px solid {accent};
               padding: 6px; border-radius: {radius}px; }}
    QLabel[role="dim"] {{ color: {t.text_dim.name()}; font-size: {small}px; }}
    QLabel[role="title"] {{ font-family: {t.font_family('title')}; font-size: {round(15 * t.font_scale)}px;
                           letter-spacing: {t.spec.title_spacing}px; }}
    QLabel[role="metric"] {{ font-family: {t.font_family('title')}; font-size: {round(26 * t.font_scale)}px;
                            color: {t.text.name()}; }}
    QLabel[role="badge"] {{ border: 1px solid {_rgba(t.accent, 160)}; border-radius: {radius}px;
                           padding: 1px 7px; font-size: {small}px; color: {accent}; }}
    QLabel[role="error"] {{ color: {t.danger.name()}; }}
    QLabel[role="warning"] {{ color: {t.warning.name()}; }}
    QLabel[role="success"] {{ color: {t.success.name()}; }}
    QPushButton, QToolButton {{ background: {_rgba(t.accent, 26)}; border: 1px solid {_rgba(t.accent, 120)};
        border-radius: {radius}px; padding: 5px 12px; color: {t.text.name()}; }}
    QPushButton:hover, QToolButton:hover {{ background: {_rgba(t.accent, 60)}; border-color: {accent}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {_rgba(t.accent, 95)}; }}
    QPushButton:checked, QToolButton:checked {{ background: {_rgba(t.accent, 90)}; border-color: {accent}; }}
    QPushButton:disabled, QToolButton:disabled {{ color: {_rgba(t.text_dim, 150)};
        border-color: {_rgba(t.text_dim, 60)}; background: transparent; }}
    QPushButton[role="primary"] {{ background: {_rgba(t.accent, 110)}; border-color: {accent}; }}
    QPushButton[role="danger"] {{ background: {_rgba(t.danger, 50)}; border-color: {t.danger.name()}; }}
    QPushButton[role="nav"] {{ text-align: left; padding: 8px 12px; border: 1px solid transparent;
        background: transparent; }}
    QPushButton[role="nav"]:checked {{ background: {_rgba(t.accent, 55)}; border-color: {_rgba(t.accent, 140)}; }}
    QPushButton[role="nav"]:hover {{ background: {_rgba(t.accent, 35)}; }}
    QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox, QComboBox, QListWidget, QTreeWidget, QTableWidget {{
        background: {_rgba(t.panel_alt, 230)}; border: 1px solid {_rgba(t.border, 70)};
        border-radius: {radius}px; padding: 4px 6px; selection-background-color: {_rgba(t.accent, 120)}; }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{ border-color: {accent}; }}
    QListWidget::item {{ padding: 5px 4px; border-radius: {max(2, radius - 2)}px; }}
    QListWidget::item:selected {{ background: {_rgba(t.accent, 80)}; color: {t.text.name()}; }}
    QComboBox QAbstractItemView {{ background: {t.panel_alt.name()}; border: 1px solid {accent}; }}
    QComboBox:disabled {{ color: {t.text_dim.name()}; }}
    QCheckBox, QRadioButton {{ spacing: 7px; }}
    QCheckBox::indicator {{ width: 15px; height: 15px; border: 1px solid {accent};
        border-radius: {3 if t.spec.shape != 'soft' else 7}px; background: transparent; }}
    QRadioButton::indicator {{ width: 15px; height: 15px; border: 1px solid {accent}; border-radius: 8px;
        background: transparent; }}
    QCheckBox::indicator:checked, QRadioButton::indicator:checked {{ background: {accent}; }}
    QCheckBox:disabled, QRadioButton:disabled {{ color: {_rgba(t.text_dim, 150)}; }}
    QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{ border-color: {_rgba(t.text_dim, 90)}; }}
    QCheckBox::indicator:checked:disabled {{ background: {_rgba(t.text_dim, 110)}; }}
    QToolButton[role="frame"] {{ background: transparent; border: 1px solid transparent; padding: 2px 6px;
        color: {t.text_dim.name()}; }}
    QToolButton[role="frame"]:hover {{ border-color: {_rgba(t.accent, 120)}; color: {t.text.name()}; }}
    {glyph_rule}
    QSlider::groove:horizontal {{ height: 4px; background: {_rgba(t.text_dim, 80)}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; border-radius: 7px; background: {accent}; }}
    QProgressBar {{ border: 1px solid {_rgba(t.border, 90)}; border-radius: {radius}px; background: transparent;
        text-align: center; height: 10px; font-size: {small}px; }}
    QProgressBar::chunk {{ background: {accent}; border-radius: {radius}px; }}
    QScrollArea {{ background: transparent; border: none; }}
    QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {_rgba(t.accent, 90)}; border-radius: 4px; min-height: 26px; }}
    QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {_rgba(t.accent, 90)}; border-radius: 4px; min-width: 26px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
    QMenu {{ background: {t.panel_alt.name()}; border: 1px solid {accent}; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; }}
    QMenu::item:selected {{ background: {_rgba(t.accent, 80)}; }}
    QDialog {{ background: {t.bg_top.name()}; }}
    QGroupBox {{ border: 1px solid {_rgba(t.border, 70)}; border-radius: {radius}px; margin-top: 12px;
                padding-top: 8px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {accent}; }}
    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{ background: transparent; padding: 6px 12px; border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ border-bottom-color: {accent}; color: {accent}; }}
    """


class ThemeManager(QObject):
    """Thème courant (résolu) ; émet ``changed`` à chaque modification."""

    changed = Signal(object)

    def __init__(self, store: ConfigStore, parent: QObject | None = None,
                 reduced_motion_probe=system_prefers_reduced_motion) -> None:
        super().__init__(parent)
        self.store = store
        self._probe = reduced_motion_probe
        self._theme = resolve(store.config.appearance, self._probe())

    @property
    def theme(self) -> ResolvedTheme:
        return self._theme

    def refresh(self) -> ResolvedTheme:
        self._theme = resolve(self.store.config.appearance, self._probe())
        self.changed.emit(self._theme)
        return self._theme

    def update(self, **changes) -> ResolvedTheme:
        """Modifie l'apparence (validée), persiste (différé) et applique à chaud."""
        prefs = self.store.config.appearance
        data = prefs.to_dict()
        data.update(changes)
        notes: list[str] = []
        new_prefs = AppearancePrefs.from_dict(data, notes)
        new_prefs = replace(new_prefs, extras=prefs.extras)
        self.store.config.appearance = new_prefs
        self.store.changed("appearance")
        return self.refresh()
