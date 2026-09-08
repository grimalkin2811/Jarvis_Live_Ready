"""Jarvis UI prototype — stage 1

A minimal futuristic orb that deforms toward the mouse.

Requirements:
    pip install pyside6

Run:
    python jarvis_morphing_orb_stage1.py
"""

from __future__ import annotations

import math
import os
import re
import sys
import threading
from dataclasses import dataclass
from typing import Callable, List

from PySide6.QtCore import QPointF, QTimer, Qt, QRectF
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QApplication, QWidget

from . import appearance_actions
from . import menu_state
from . import system_actions


voice_energy = 0.0

#: État de présence de Jarvis, piloté par le backend vocal :
#: "loading" (modèle en cours de chargement), "listening", "thinking",
#: "speaking" ou "hidden" (veille). Simple chaîne : l'écriture depuis un
#: autre thread est atomique et sûre.
presence_state = "loading"


def set_voice_energy(value: float) -> None:
    """Permet au backend (Gemini Live) de piloter la réactivité de l'orbe.

    `value` doit être compris entre 0.0 (repos) et 1.0 (parole/écoute active).
    """
    global voice_energy
    voice_energy = clamp(float(value), 0.0, 1.0)


def set_presence_state(state: str) -> None:
    """Informe l'orbe de l'état vocal courant (thread-safe)."""
    global presence_state
    value = str(state or "hidden").strip().lower()
    allowed = {"loading", "listening", "thinking", "speaking", "hidden"}
    presence_state = value if value in allowed else "hidden"


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


@dataclass
class BlobPoint:
    angle: float
    radius: float
    target_radius: float


@dataclass(frozen=True)
class MenuItemSpec:
    label: str
    kind: str
    routine_name: str = ""


@dataclass(frozen=True)
class MenuSpec:
    name: str
    icon: str
    glow: QColor
    accent: QColor
    body: QColor
    items: List[MenuItemSpec]
    reveal_scale: float
    branch_bias: float
    panel_width: float
    panel_padding: float


@dataclass
class MenuNode:
    label: str
    position: QPointF
    hover_amount: float
    radius: float
    visible_amount: float
    callback: Callable[[], None] | None
    active: bool
    section: str
    kind: str
    click_amount: float = 0.0


MENU_SPECS = [
    MenuSpec(
        name="Voice",
        icon="♪",
        glow=QColor(165, 235, 255),
        accent=QColor(225, 250, 255),
        body=QColor(16, 28, 40),
        items=[
            MenuItemSpec("TTS Volume", "slider"),
            MenuItemSpec("Voice Select", "chips"),
            MenuItemSpec("Speech Speed", "slider"),
            MenuItemSpec("Mic Toggle", "toggle"),
            MenuItemSpec("Hotword Sens.", "slider"),
            MenuItemSpec("Always Listening", "toggle"),
            MenuItemSpec("Interrupt Word", "toggle"),
            MenuItemSpec("Stop Speaking", "pulse"),
            MenuItemSpec("Audio Test", "pulse"),
        ],
        reveal_scale=0.92,
        branch_bias=1.20,
        panel_width=186.0,
        panel_padding=18.0,
    ),
    MenuSpec(
        name="System",
        icon="◫",
        glow=QColor(120, 205, 255),
        accent=QColor(200, 230, 255),
        body=QColor(12, 20, 32),
        items=[
            MenuItemSpec("Startup", "toggle"),
            MenuItemSpec("Always on Top", "toggle"),
            MenuItemSpec("Transparency", "slider"),
            MenuItemSpec("Response Mode", "meter"),
            MenuItemSpec("Reset Settings", "pulse"),
            MenuItemSpec("Quit", "pulse"),
        ],
        reveal_scale=0.86,
        branch_bias=1.05,
        panel_width=194.0,
        panel_padding=16.0,
    ),
    MenuSpec(
        name="Memory",
        icon="◌",
        glow=QColor(110, 160, 255),
        accent=QColor(190, 220, 255),
        body=QColor(10, 16, 30),
        items=[
            MenuItemSpec("Long-term Memory", "toggle"),
            MenuItemSpec("Memory Count", "status"),
            MenuItemSpec("Model State", "status"),
        ],
        reveal_scale=1.00,
        branch_bias=1.28,
        panel_width=204.0,
        panel_padding=18.0,
    ),
    MenuSpec(
        name="Appearance",
        icon="✦",
        glow=QColor(165, 185, 255),
        accent=QColor(235, 240, 255),
        body=QColor(14, 18, 30),
        items=[
            MenuItemSpec("Color", "action"),
            MenuItemSpec("Glow +", "action"),
            MenuItemSpec("Glow -", "action"),
            MenuItemSpec("Blob Size +", "action"),
            MenuItemSpec("Blob Size -", "action"),
            MenuItemSpec("Minimal Mode", "toggle"),
            MenuItemSpec("Cinematic Mode", "toggle"),
        ],
        reveal_scale=0.98,
        branch_bias=1.12,
        panel_width=192.0,
        panel_padding=16.0,
    ),
]


# ---------------------------------------------------------------------------
# Menu Routines (construit dynamiquement : il reflète le fichier routines.json)
# ---------------------------------------------------------------------------

#: Nombre maximum de raccourcis de macros personnelles dans le menu radial.
ROUTINE_SLOTS = 6

#: Contrôles fixes autour des raccourcis de macros personnelles.
ROUTINE_STATIC_ITEMS = [
    MenuItemSpec("Catalogue", "buttonless"),
    MenuItemSpec("Reminders", "status"),
    MenuItemSpec("Reload", "buttonless"),
]


def _routine_names(limit: int = ROUTINE_SLOTS) -> List[str]:
    """Noms des routines enregistrées, sans jamais faire échouer l'UI."""
    try:
        from src.routines import get_default_routine_manager

        result = get_default_routine_manager().list_routines()
        if not result.get("success"):
            return []
        # Les presets ont leur panneau défilant : ne pas les tronquer
        # aux six raccourcis réservés ici aux macros personnelles actives.
        return [str(item["name"]) for item in result.get("routines", [])
                if not item.get("preset_id") and item.get("enabled")][:limit]
    except Exception:
        return []


def build_routines_spec() -> MenuSpec:
    """Construit le menu Routines à partir des routines réellement définies."""
    names = _routine_names()
    items = [MenuItemSpec(name[:22], "pulse", routine_name=name) for name in names]
    items = [ROUTINE_STATIC_ITEMS[0]] + items + ROUTINE_STATIC_ITEMS[1:]
    return MenuSpec(
        name="Routines",
        icon="▷",
        glow=QColor(140, 255, 215),
        accent=QColor(215, 255, 240),
        body=QColor(10, 26, 26),
        items=items,
        reveal_scale=0.96,
        branch_bias=1.18,
        panel_width=200.0,
        panel_padding=18.0,
    )


MENU_SPECS.append(build_routines_spec())


class MorphingOrbWidget(QWidget):

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Jarvis")
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.Window)
        self.resize(1100, 760)
        self.setMouseTracking(True)

        self.setAttribute(Qt.WA_OpaquePaintEvent, False)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)

        # COLORS
        self.bg = QColor(0, 0, 0, 0)
        self.glow_color = QColor(70, 180, 255)
        self.text_color = QColor(210, 235, 255)
        self._base_radius_default = 110.0
        self._appearance_state_path = os.path.join(os.path.dirname(__file__), "appearance_state.json")
        self.appearance_state = appearance_actions.load_state(self._appearance_state_path)
        self._appearance_signature = None
        self._apply_appearance_state()
        self._system_state_path = os.path.join(os.path.dirname(__file__), "system_state.json")
        self.system_state = system_actions.load_state(self._system_state_path)

        # STATE
        self.center = QPointF(self.width() / 2, self.height() / 2)
        self.current_center = QPointF(self.center)
        self.cursor = QPointF(self.center)

        # self.base_radius est défini par _apply_appearance_state() ci-dessus
        # (il dépend de blob_scale).

        self.time = 0.0
        self.pulse = 0.0

        self.offset_x = 0.0
        self.offset_y = 0.0

        self.stable_angle = 0.0
        self._prev_cursor = QPointF(self.cursor)
        self._cursor_speed = 0.0
        self._halo_energy = 0.0
        self._deform_energy = 0.0
        # Énergie liée à l'état vocal (écoute / parole) pour un retour
        # visuel immédiat, même sans niveau micro.
        self._presence_energy = 0.0
        self._mic_mute_factor = 1.0
        self._menu_sector = -1
        self._menu_candidate = -1
        self._menu_candidate_time = 0.0
        self._menu_last_stable_time = -1.0
        self._menu_reveal = 0.0
        self._menu_alpha = 0.0
        self._menu_branch_strength = 0.0
        self._menu_nodes: List[MenuNode] = []
        self._menu_hot_node = -1
        self._menu_action_flash = ""
        self._menu_action_flash_time = -1.0
        self._menu_layout_mode = "line"
        self._routines_mtime = -1.0
        self._routines_checked_at = -10.0
        self._routines_cache: dict[str, str] = {}
        self._menu_hold_seconds = 0.30
        self._menu_release_seconds = 0.18
        self._menu_exit_seconds = 0.24
        # Distance-based close + sector switching: interaction du menu radial.
        self._menu_distance_close_enabled = True
        # Interactions directes sur les nœuds (sliders / molette).
        self._menu_drag_index = -1
        self._menu_drag_axis = ""
        self._menu_drag_start_x = 0.0
        self._menu_drag_start_y = 0.0
        self._menu_drag_start_value = 0
        self._menu_drag_perp = (1.0, 0.0)
        self._menu_wheel_index = -1
        # Focus clavier dans le menu ouvert (navigation flèches + Entrée).
        self._menu_focus_index = -1
        # Vrai quand le menu a été ouvert par raccourci clavier : il reste
        # affiché jusqu'à fermeture explicite, sans dépendre de la souris.
        self._menu_keyboard_open = False
        # Cache des valeurs « coûteuses » (SQLite / disque) : le rendu tourne
        # à ~60 FPS, on ne doit pas interroger la base à chaque image.
        self._status_cache: dict[str, tuple[float, str]] = {}
        # Sauvegarde de l'état menu « débouncée » : pendant le glissement
        # d'un slider, on n'écrit pas le JSON à chaque image.
        self._menu_state_dirty = False
        self._menu_state_last_save = -10.0
        # Curseur pointeur au survol d'une cible cliquable.
        self._pointer_cursor_active = False
        # En mode jeu, l'orbe se masque totalement pour ne rien afficher par-dessus le jeu.
        self._hidden_by_game_mode = False
        self._mode_checked_at = -10.0
        self._visuals_suppressed_by_mode = False
        # État interactif persistant du menu.
        self._menu_state_path = os.path.join(os.path.dirname(__file__), "menu_state.json")
        self.menu_state = menu_state.load_state(self._menu_state_path)
        self._menu_open_projection = self.base_radius * 0.42
        self._menu_close_projection = self.base_radius * 3.00
        self._menu_open_lateral = self.base_radius * 1.15

        # SHAPE
        self.num_points = 84
        self.points: List[BlobPoint] = []

        for i in range(self.num_points):
            angle = (i / self.num_points) * math.tau
            self.points.append(
                BlobPoint(angle=angle, radius=self.base_radius, target_radius=self.base_radius)
            )

        # TIMER
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(16)

        # state for outside/inside hysteresis to avoid flicker at the visual rim
        self._outside_state = False
        # debug toggle (press 'D' to enable console diagnostics)
        self.debug_mode = False
        # debug log file path and throttle state
        try:
            base_dir = os.path.dirname(__file__)
        except NameError:
            base_dir = os.getcwd()
        self._debug_log_path = os.path.join(base_dir, "jarvis_debug.log")
        self._debug_last_write = -1.0
        # overall time scale for animation speed (1.0 = normal). Increase for more reactive feel.
        self.time_scale = 1.25

    # =========================================================
    # ANIMATION
    # =========================================================

    def tick(self):

        # advance time with global timescale for increased responsiveness
        dt = 0.016 * getattr(self, 'time_scale', 1.0)
        self.time += dt
        self._apply_appearance_state()
        self.pulse = 0.5 + 0.5 * math.sin(self.time * 1.8 + voice_energy * 0.6)

        # Sauvegarde débouncée de l'état du menu (glissement de slider…).
        if (
            self._menu_state_dirty
            and (self.time - self._menu_state_last_save) > 0.5
        ):
            self._save_menu_state(force=True)

        # Mode jeu : aucune surimpression ni menu au-dessus du jeu.
        if self._mode_suppresses_visuals():
            if self.isVisible():
                self._close_radial_menu()
                self.hide()
            self._hidden_by_game_mode = True
            return
        if self._hidden_by_game_mode:
            self._hidden_by_game_mode = False
            if not self.isVisible():
                self.showFullScreen()

        # Nettoyage du flash d'action (indépendant de l'état du menu : le
        # flash doit aussi disparaître quand aucun menu n'est ouvert).
        if self._menu_action_flash_time >= 0.0:
            if (self.time - self._menu_action_flash_time) > 2.2:
                self._menu_action_flash = ""
                self._menu_action_flash_time = -1.0

        # Énergie liée à l'état vocal : l'orbe respire plus fort quand Jarvis
        # écoute ou parle, et s'atténue quand le micro est coupé.
        presence_targets = {
            "listening": 0.45,
            "thinking": 0.60,
            "speaking": 0.90,
        }
        try:
            mic_on = menu_state.LIVE.get_mic_enabled()
        except Exception:
            mic_on = True
        presence_target = presence_targets.get(presence_state, 0.0)
        if not mic_on:
            presence_target = 0.0
        self._presence_energy = lerp(self._presence_energy, presence_target, 0.06)
        self._mic_mute_factor = lerp(self._mic_mute_factor, 1.0 if mic_on else 0.55, 0.08)

        # CURSOR VECTOR (pre-update)
        dx = self.cursor.x() - self.current_center.x()
        dy = self.cursor.y() - self.current_center.y()

        raw_dist_pre = math.hypot(dx, dy)
        # used for legacy surface calculations (keep minimum to avoid extreme exponents)
        dist = max(raw_dist_pre, 18)

        # normalized mouse direction vector for offsets (use true distance, guarded)
        ndist_pre = max(raw_dist_pre, 1.0)
        mx_dir = dx / ndist_pre
        my_dir = dy / ndist_pre

        cursor_step_x = self.cursor.x() - self._prev_cursor.x()
        cursor_step_y = self.cursor.y() - self._prev_cursor.y()
        cursor_speed = math.hypot(cursor_step_x, cursor_step_y) / max(dt, 0.001)
        cursor_speed_norm = clamp(cursor_speed / 28.0, 0.0, 1.0)
        self._cursor_speed = lerp(self._cursor_speed, cursor_speed_norm, 0.12)
        self._prev_cursor = QPointF(self.cursor)

        # estimate the visible rim along the mouse direction by projecting current
        # deformed points onto the mouse direction (takes current deformation into account)
        glow_padding = 22.0
        rim_proj = 0.0
        for p in self.points:
            px = math.cos(p.angle)
            py = math.sin(p.angle)
            alignment = px * mx_dir + py * my_dir
            if alignment > 0.0:
                proj = p.radius * alignment
                if proj > rim_proj:
                    rim_proj = proj

        if rim_proj <= 0.0:
            rim_proj = self.base_radius

        # use deformed rim + padding as the visual limit for hysteresis/proximity
        visual_radius = rim_proj + glow_padding

        # stable center vector: compute from static `self.center` to avoid feedback
        dx_center = self.cursor.x() - self.center.x()
        dy_center = self.cursor.y() - self.center.y()
        raw_dist_center = math.hypot(dx_center, dy_center)
        ndist_center = max(raw_dist_center, 1.0)
        mx_dir_center = dx_center / ndist_center
        my_dir_center = dy_center / ndist_center

        # =====================================================
        # STABLE SURFACE MODEL (anti oscillation)
        # =====================================================

        # (visual_radius computed earlier from deformed rim projection)

        # Legacy surface model (kept for visual blending but not used for offset decision)
        signed = dist - visual_radius
        abs_dist = abs(signed)

        surface = 1.0 - clamp(abs_dist / 120.0, 0.0, 1.0)
        surface = surface ** 2.6

        global_fade = 1.0 - clamp(dist / 520.0, 0.0, 1.0)
        global_fade = global_fade ** 1.3

        border_dist = dist - visual_radius
        border_zone = math.exp(-(border_dist * border_dist) / (2 * 85.0 * 85.0))

        border_lock = 1.0 - clamp(abs(border_dist) / 160.0, 0.0, 1.0)
        border_lock = border_lock ** 1.5

        # =====================================================
        # CENTER OFFSET
        # Compute a deformation proximity based on gaussian radial field and hysteresis
        # (use pre-update raw distance)
        sigma_out = 120.0
        sigma_in = 90.0
        gauss_global = math.exp(-(raw_dist_center * raw_dist_center) / (2 * (520.0 ** 2)))

        signed_pre = raw_dist_center - visual_radius
        hysteresis_px = 8.0
        if signed_pre > hysteresis_px:
            is_outside_pre = True
        elif signed_pre < -hysteresis_px:
            is_outside_pre = False
        else:
            is_outside_pre = self._outside_state

        if is_outside_pre:
            gauss_local_pre = math.exp(-((raw_dist_center - visual_radius) ** 2) / (2 * (sigma_out ** 2)))
            proximity_pre = clamp(gauss_local_pre * 1.0 + gauss_global * 0.2, 0.0, 1.0)
        else:
            gauss_local_pre = math.exp(-((raw_dist_center - visual_radius) ** 2) / (2 * (sigma_in ** 2)))
            inner_factor_pre = clamp(raw_dist_center / visual_radius, 0.0, 1.0)
            proximity_pre = clamp((gauss_local_pre * 0.6 + gauss_global * 0.1) * inner_factor_pre, 0.0, 1.0)

        menu_proximity = proximity_pre

        # limit offset so the center never crosses the cursor (avoid sign inversion)
        max_offset_allowed = max(0.0, raw_dist_center * 0.45)
        target_offset_strength = min(proximity_pre * 34.0, max_offset_allowed)

        # Use the pure mouse direction vector from base center for center offset (removes feedback)
        self.offset_x = lerp(
            self.offset_x,
            mx_dir_center * target_offset_strength,
            0.10
        )

        self.offset_y = lerp(
            self.offset_y,
            my_dir_center * target_offset_strength,
            0.10
        )

        target_center = QPointF(
            self.center.x() + self.offset_x,
            self.center.y() + self.offset_y
        )

        self.current_center.setX(
            lerp(self.current_center.x(), target_center.x(), 0.10)
        )

        self.current_center.setY(
            lerp(self.current_center.y(), target_center.y(), 0.10)
        )

        # =====================================================
        # INTENTION / ORGANIC MENU
        # =====================================================
        core_dx = self.cursor.x() - self.center.x()
        core_dy = self.cursor.y() - self.center.y()
        core_dist = math.hypot(core_dx, core_dy)
        sector_dx = core_dx
        sector_dy = core_dy
        sector_dist = core_dist
        sector, sector_alignment, sector_margin = self._sector_from_cursor(sector_dx, sector_dy)

        direction_stability = clamp((sector_margin - 0.06) / 0.42, 0.0, 1.0)
        open_distance = self.base_radius * 0.92
        close_distance = self.base_radius * 2.50
        hard_close_distance = self.base_radius * 3
        sector_ok = sector_alignment >= 0.72 and direction_stability >= 0.28
        menu_gate = clamp((core_dist - open_distance) / (self.base_radius * 0.80), 0.0, 1.0)
        menu_gate *= clamp(menu_proximity * 1.0 + self._cursor_speed * 0.35, 0.0, 1.0)

        if sector != self._menu_candidate:
            self._menu_candidate = sector
            self._menu_candidate_time = self.time

        candidate_age = self.time - self._menu_candidate_time
        stable_intent = (
            sector >= 0
            and sector == self._menu_candidate
            and candidate_age >= self._menu_hold_seconds
            and sector_ok
            and menu_gate >= 0.12
        )

        if self._menu_sector >= 0 and not self._menu_keyboard_open:
            # Fermeture par l'interaction radiale :
            # revenir vers l'orbite ou partir loin ferme ; rester dans un secteur
            # (ou en surgir) conserve le menu et permet de changer de menu.
            active_sx, active_sy = self._sector_vector(self._menu_sector)
            active_projection = sector_dx * active_sx + sector_dy * active_sy
            active_lateral = abs(sector_dx * (-active_sy) + sector_dy * active_sx)
            near_center = core_dist < self.base_radius * 0.55
            far_away = core_dist >= hard_close_distance
            in_sector = sector >= 0 and sector_ok and direction_stability >= 0.22
            keep_open = (
                not near_center
                and not far_away
                and (in_sector or active_projection >= open_distance * 0.60)
                and active_lateral <= self.base_radius * 1.45
            )

            if near_center or far_away:
                self._menu_sector = -1
                self._menu_candidate = -1
            elif keep_open:
                self._menu_last_stable_time = self.time
            elif (self.time - self._menu_last_stable_time) > self._menu_release_seconds:
                self._menu_sector = -1
                self._menu_candidate = -1

        if stable_intent:
            # Une intention souris claire reprend la main sur le menu épinglé
            # au clavier (permet de changer de menu à la souris).
            self._menu_keyboard_open = False
            self._menu_sector = sector
            active_spec = MENU_SPECS[sector]
            self._menu_last_stable_time = self.time
            self._menu_reveal = lerp(self._menu_reveal, active_spec.reveal_scale, 0.10)
            self._menu_alpha = lerp(self._menu_alpha, 1.0, 0.12)
        elif self._menu_keyboard_open and self._menu_sector >= 0:
            # Menu ouvert par raccourci clavier : il reste affiché jusqu'à
            # fermeture explicite (Échap, clic droit) — la position de la
            # souris ne doit pas le refermer aussitôt.
            self._menu_last_stable_time = self.time
            active_spec = MENU_SPECS[self._menu_sector]
            self._menu_reveal = lerp(self._menu_reveal, active_spec.reveal_scale, 0.10)
            self._menu_alpha = lerp(self._menu_alpha, 1.0, 0.12)
        else:
            if self._menu_sector >= 0 and (self.time - self._menu_last_stable_time) < self._menu_release_seconds:
                self._menu_reveal = lerp(self._menu_reveal, MENU_SPECS[self._menu_sector].reveal_scale * 0.92, 0.05)
                self._menu_alpha = lerp(self._menu_alpha, 0.95, 0.06)
            else:
                self._menu_sector = -1
                self._menu_reveal = lerp(self._menu_reveal, 0.0, 0.06)
                self._menu_alpha = lerp(self._menu_alpha, 0.0, 0.08)

        active_spec = MENU_SPECS[self._menu_sector] if self._menu_sector >= 0 else None

        self._menu_branch_strength = clamp(
            self._menu_reveal
            * (0.55 + 0.45 * direction_stability)
            * (0.60 + 0.40 * menu_proximity)
            * (active_spec.branch_bias if active_spec is not None else 1.0),
            0.0,
            1.0,
        )

        self._update_menu_nodes()

        # =====================================================
        # SHAPE DEFORMATION
        # =====================================================
        # Build a stable mouse direction vector and a smooth radial influence
        # Use the displayed center (`current_center`) so deformation and rendering align
        mdx = self.cursor.x() - self.current_center.x()
        mdy = self.cursor.y() - self.current_center.y()

        raw_dist = math.hypot(mdx, mdy)
        raw_dist = max(raw_dist, 1.0)

        # normalized mouse direction (unit vector) — avoids trig-based angle diffs
        mx = mdx / raw_dist
        my = mdy / raw_dist

        # radial influence: smooth gaussian around the visual radius and global gaussian falloff
        sigma_out = 120.0
        sigma_in = 90.0
        gauss_global = math.exp(-(raw_dist * raw_dist) / (2 * (520.0 ** 2)))

        # HYSTERESIS: avoid flipping inside/outside state due to small cursor movements/noise
        signed = raw_dist - visual_radius
        hysteresis_px = 8.0
        if signed > hysteresis_px:
            is_outside = True
        elif signed < -hysteresis_px:
            is_outside = False
        else:
            is_outside = self._outside_state

        # Cursor outside or inside behaviour with smooth gaussian profiles
        if is_outside:
            gauss_local = math.exp(-((raw_dist - visual_radius) ** 2) / (2 * (sigma_out ** 2)))
            proximity = clamp(gauss_local * 1.0 + gauss_global * 0.2, 0.0, 1.0)
        else:
            gauss_local = math.exp(-((raw_dist - visual_radius) ** 2) / (2 * (sigma_in ** 2)))
            inner_factor = clamp(raw_dist / visual_radius, 0.0, 1.0)
            proximity = clamp((gauss_local * 0.6 + gauss_global * 0.1) * inner_factor, 0.0, 1.0)

        # store for next frame
        self._outside_state = is_outside

        # global stretch strength scaled by proximity (controls per-point deformation)
        stretch_strength_total = proximity * (130.0 + 18.0 * voice_energy)

        # per-frame offset strength (center pulls) — larger when cursor is near ring from outside
        target_offset_strength = proximity * 34.0

        # Apply shape deformation per point using pure vector dot-product model
        # Diagnostic printing when debug enabled and cursor near the visual rim
        if getattr(self, 'debug_mode', False) and abs(raw_dist - visual_radius) < 80.0:
            # sample alignments at cardinal directions
            dirs = [(0.0, 'right'), (math.tau/4.0, 'down'), (math.pi, 'left'), (3*math.tau/4.0, 'up')]
            aligns = []
            for ang, name in dirs:
                px = math.cos(ang)
                py = math.sin(ang)
                aligns.append((name, px * mx + py * my))
            line1 = f"DBG t={self.time:.3f} raw_dist={raw_dist:.2f} visual_radius={visual_radius:.2f} is_outside={is_outside} proximity={proximity:.3f}\n"
            line2 = f"     cursor=({self.cursor.x():.1f},{self.cursor.y():.1f}) current_center=({self.current_center.x():.1f},{self.current_center.y():.1f}) offset=({self.offset_x:.2f},{self.offset_y:.2f})\n"
            aligns_lines = "".join([f"     align_{name}={val:.3f}\n" for name, val in aligns])
            # throttle file writes to ~8Hz
            if self._debug_last_write < 0 or (self.time - self._debug_last_write) >= 0.12:
                try:
                    with open(self._debug_log_path, "a", encoding="utf-8") as f:
                        f.write(line1)
                        f.write(line2)
                        f.write(aligns_lines)
                except Exception:
                    pass
                self._debug_last_write = self.time

        base_targets = []
        deformation_sum = 0.0
        for point in self.points:
            angle = point.angle  # keep stored angle unchanged (no extra rotation)

            # unit vector for this point (pure radial basis)
            px = math.cos(angle)
            py = math.sin(angle)

            # dot product = cos(theta) between point direction and mouse direction
            alignment = px * mx + py * my
            alignment = clamp(alignment, -1.0, 1.0)

            # smooth angular weighting, softer and more organic than a hard lobe
            front_weight = max(0.0, alignment) ** 1.45
            side_weight = (1.0 - abs(alignment)) ** 1.18
            back_weight = max(0.0, -alignment) ** 1.35

            # local stretch/compress scaled by global stretch strength
            stretch = front_weight * stretch_strength_total
            compress = back_weight * stretch_strength_total * 0.30

            menu_branch = 0.0
            if self._menu_sector >= 0 and self._menu_reveal > 0.01:
                sector_angle = self._sector_angle(self._menu_sector)
                sx = math.cos(sector_angle)
                sy = math.sin(sector_angle)
                sector_alignment = clamp(px * sx + py * sy, -1.0, 1.0)
                sector_forward = max(0.0, sector_alignment) ** 1.75
                sector_shell = (1.0 - abs(sector_alignment)) ** 1.25
                menu_branch = (
                    sector_forward * 18.0
                    + sector_shell * 5.0
                ) * self._menu_branch_strength
                if sector_forward > 0.12:
                    menu_branch += math.sin(self.time * 2.1 + angle * 5.0) * self._menu_branch_strength * 0.8

            # micro oscillations: light procedural waves with no hard direction bias
            wave_a = math.sin(self.time * 1.55 + angle * 6.0)
            wave_b = math.sin(self.time * 2.35 - angle * 9.0 + raw_dist * 0.018)
            wave_c = math.sin(self.time * 3.75 + angle * 13.0 + self.pulse * 1.2)
            breathing = (wave_a + 0.55 * wave_b + 0.30 * wave_c) * (1.05 + 1.05 * voice_energy)
            breathing *= 0.50 + 0.30 * self._cursor_speed

            # target radius computed from base radius plus smooth contributions
            target = self.base_radius + stretch - compress + menu_branch + side_weight * (1.0 + 0.5 * voice_energy) * 1.4 + breathing * 1.45
            base_targets.append(target)
            deformation_sum += abs(target - self.base_radius)

        if base_targets:
            membrane_blend = 0.12 + 0.06 * proximity + 0.06 * self._cursor_speed + 0.05 * voice_energy
            membrane_blend = clamp(membrane_blend, 0.08, 0.28)

            smoothed_targets = []
            count = len(base_targets)
            for i in range(count):
                prev_target = base_targets[(i - 1) % count]
                curr_target = base_targets[i]
                next_target = base_targets[(i + 1) % count]
                neighborhood = (prev_target + curr_target + next_target) / 3.0
                wide_neighborhood = (
                    base_targets[(i - 2) % count]
                    + base_targets[(i + 2) % count]
                ) * 0.5

                target = lerp(curr_target, neighborhood, membrane_blend)
                target = lerp(target, (target + wide_neighborhood) * 0.5, membrane_blend * 0.25)
                target = clamp(target, self.base_radius * 0.60, self.base_radius * 1.85)
                smoothed_targets.append(target)

            deform_energy = (deformation_sum / len(base_targets)) / max(self.base_radius, 1.0)
            self._deform_energy = lerp(self._deform_energy, clamp(deform_energy, 0.0, 1.0), 0.10)

            halo_target = (
                0.18
                + proximity * 0.42
                + self._cursor_speed * 0.34
                + self._deform_energy * 0.46
                + voice_energy * 0.78
                + self._presence_energy * 0.55
            )
            self._halo_energy = lerp(self._halo_energy, clamp(halo_target, 0.0, 1.65), 0.08)

            radius_lerp = clamp(0.12 + 0.06 * self._cursor_speed + 0.05 * voice_energy, 0.10, 0.24)
            for point, target in zip(self.points, smoothed_targets):
                point.target_radius = target
                point.radius = lerp(point.radius, point.target_radius, radius_lerp)

        self.update()

    # =========================================================
    # EVENTS
    # =========================================================

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.center = QPointF(self.width() / 2, self.height() / 2)

    def mouseMoveEvent(self, event):
        pos = event.position()
        self.cursor = pos

        if self._menu_drag_index >= 0 and self._menu_nodes:
            node = self._menu_nodes[self._menu_drag_index] if self._menu_drag_index < len(self._menu_nodes) else None
            spec = self._spec_for_name(node.section) if node is not None else None
            items = spec.items if spec is not None else []
            item = None
            for it in items:
                if it.label == node.label:
                    item = it
                    break
            if spec is not None and item is not None and self._menu_is_slider(spec, item):
                # Glissement projeté sur l'axe perpendiculaire au secteur :
                # le geste reste naturel que le menu soit déployé vers le
                # haut, le bas, la gauche ou la droite.
                perp_x, perp_y = self._menu_drag_perp
                delta = (pos.x() - self._menu_drag_start_x) * perp_x + (
                    pos.y() - self._menu_drag_start_y
                ) * perp_y
                factor = 100.0 / 220.0  # 220 px de glissement = 100% de variation
                value = self._menu_drag_start_value + int(round(delta * factor))
                self._menu_set_slider(spec, item, value)
                self._menu_action_flash = f"{item.label}: {self._menu_slider_value(spec, item)}%"
                self._menu_action_flash_time = self.time

        self.update()

    def mouseReleaseEvent(self, event):
        if self._menu_drag_index >= 0:
            # Fin de glissement : on force la sauvegarde immédiate.
            self._menu_drag_index = -1
            self._menu_drag_axis = ""
            self._save_menu_state(force=True)
        self.update()
        super().mouseReleaseEvent(event)

    def mousePressEvent(self, event):
        # Clic droit : ferme le menu radial ouvert (geste d'annulation).
        if event.button() == Qt.RightButton:
            if self._menu_sector >= 0 or self._menu_alpha > 0.05:
                self._close_radial_menu()
                event.accept()
                return

        if event.button() == Qt.LeftButton and self._menu_nodes:
            hit = self._menu_hit_test(event.position())
            if hit is not None:
                index, node, spec, item = hit
                if spec is not None and item is not None and self._menu_is_slider(spec, item):
                    # Démarre un glisser-ajuster sur le slider, sans déclencher le callback.
                    self._menu_drag_index = index
                    self._menu_drag_axis = item.label
                    self._menu_drag_start_x = event.position().x()
                    self._menu_drag_start_y = event.position().y()
                    self._menu_drag_start_value = self._menu_slider_value(spec, item)
                    sector_index = MENU_SPECS.index(spec)
                    sx, sy = self._sector_vector(sector_index)
                    self._menu_drag_perp = (-sy, sx)
                    node.click_amount = 0.5
                else:
                    node.click_amount = 1.0
                    if node.callback is not None:
                        node.callback()
                self.update()
                event.accept()
                return

        super().mousePressEvent(event)

    def wheelEvent(self, event):
        if self._menu_nodes:
            direction = 1 if event.angleDelta().y() > 0 else -1
            # Le nœud actif est celui du focus clavier s'il existe, sinon
            # celui survolé par la souris.
            node = (
                self._menu_focus_index
                if 0 <= self._menu_focus_index < len(self._menu_nodes)
                else self._menu_hot_node
            )
            if node >= 0 and node < len(self._menu_nodes):
                target = self._menu_nodes[node]
                spec = self._spec_for_name(target.section)
                items = spec.items if spec is not None else []
                item = None
                for it in items:
                    if it.label == target.label:
                        item = it
                        break
                if spec is not None and item is not None:
                    if self._menu_is_slider(spec, item):
                        step = 2 * direction
                        self._menu_set_slider(spec, item, self._menu_slider_value(spec, item) + step)
                        self._menu_action_flash = f"{item.label}: {self._menu_slider_value(spec, item)}%"
                        self._menu_action_flash_time = self.time
                        event.accept()
                        return
                    if self._menu_is_option(spec, item):
                        self._menu_cycle_option(spec, item, step=direction)
                        self._menu_action_flash = f"{item.label}: {self._menu_value(spec, item)}"
                        self._menu_action_flash_time = self.time
                        event.accept()
                        return

        super().wheelEvent(event)

    def _menu_hit_test(self, pos) -> tuple[int, MenuNode, MenuSpec | None, MenuItemSpec | None] | None:
        cursor_x = pos.x()
        cursor_y = pos.y()
        best_index = -1
        best_distance = float("inf")
        for index, node in enumerate(self._menu_nodes):
            if node.visible_amount <= 0.05:
                continue
            # Zone de clic confortable (~19-21 px) : les micro-cibles de 10 px
            # du prototype rendaient les clics trop difficiles (loi de Fitts).
            hit_radius = node.radius * 1.45 + 7.0
            distance = math.hypot(cursor_x - node.position.x(), cursor_y - node.position.y())
            if distance <= hit_radius and distance < best_distance:
                best_index = index
                best_distance = distance

        if best_index < 0:
            return None
        node = self._menu_nodes[best_index]
        spec = self._spec_for_name(node.section)
        item = None
        if spec is not None:
            for it in spec.items:
                if it.label == node.label:
                    item = it
                    break
        return best_index, node, spec, item

    def keyPressEvent(self, event):
        # Échap : ferme d'abord le menu radial ouvert ; ce n'est que s'il
        # n'y a plus rien à fermer qu'Échap quitte Jarvis. On évite ainsi
        # les fermetures accidentelles de l'application.
        if event.key() == Qt.Key_Escape:
            if self._menu_sector >= 0 or self._menu_alpha > 0.05:
                self._close_radial_menu()
                return
            appearance_actions.save_state(self.appearance_state, self._appearance_state_path)
            system_actions.save_state(self.system_state, self._system_state_path)
            self._save_menu_state(force=True)
            self.close()
            return

        # Raccourci « M » : couper/rétablir le micro sans ouvrir le menu.
        if event.text().lower() == "m" and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            st = self.menu_state
            new_value = not st.mic_enabled
            st.mic_enabled = new_value
            menu_state.LIVE.set_mic_enabled(new_value)
            self._save_menu_state()
            self._flash(f"Micro : {'activé' if new_value else 'coupé'}")
            return

        # Raccourci « S » : couper immédiatement la réponse en cours.
        if event.text().lower() == "s" and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self._stop_speaking()
            return

        # Touches 1..9 : ouvrir directement le menu radial correspondant.
        digit = event.key() - Qt.Key_1
        if 0 <= digit < len(MENU_SPECS) and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self._open_radial_menu(digit)
            self._flash(f"{MENU_SPECS[digit].name}")
            return

        # Navigation clavier du menu ouvert : flèches + Entrée/Espace.
        if self._menu_nodes and self._menu_sector >= 0:
            key = event.key()
            if key in (Qt.Key_Up, Qt.Key_Left, Qt.Key_Down, Qt.Key_Right):
                step = -1 if key in (Qt.Key_Up, Qt.Key_Left) else 1
                if self._menu_layout_mode == "line":
                    # Menu déployé vers la gauche/droite : les nœuds sont
                    # empilés verticalement, Haut/Bas sont donc l'axe naturel.
                    step = -1 if key in (Qt.Key_Up, Qt.Key_Down) else step
                count = len(self._menu_nodes)
                base = self._menu_focus_index if self._menu_focus_index >= 0 else self._menu_hot_node
                if base < 0:
                    # Premier appui : sélection du premier item sans avancer.
                    self._menu_focus_index = 0
                else:
                    self._menu_focus_index = (base + step) % count
                event.accept()
                return
            if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
                index = (
                    self._menu_focus_index
                    if self._menu_focus_index >= 0
                    else self._menu_hot_node
                )
                if 0 <= index < len(self._menu_nodes):
                    node = self._menu_nodes[index]
                    node.click_amount = 1.0
                    if node.callback is not None:
                        node.callback()
                    event.accept()
                    return

        # toggle debug printing
        if event.key() == Qt.Key_D:
            self.debug_mode = not getattr(self, 'debug_mode', False)
            msg = f"debug_mode={self.debug_mode}\n"
            try:
                with open(self._debug_log_path, "a", encoding="utf-8") as f:
                    f.write(msg)
            except Exception:
                pass
            return

        super().keyPressEvent(event)

    def closeEvent(self, event):
        appearance_actions.save_state(self.appearance_state, self._appearance_state_path)
        system_actions.save_state(self.system_state, self._system_state_path)
        self._save_menu_state(force=True)
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Helpers d'interaction (flash d'action, ouverture/fermeture clavier)
    # ------------------------------------------------------------------
    def _flash(self, message: str) -> None:
        """Affiche un retour d'action au-dessus de l'orbe.

        C'est le seul canal de confirmation visuelle des clics menu : sans
        lui, l'utilisateur ne sait pas si son action a été prise en compte.
        """
        self._menu_action_flash = str(message)[:80]
        self._menu_action_flash_time = self.time

    def _close_radial_menu(self) -> None:
        """Ferme le menu radial ouvert (Échap ou clic droit)."""
        self._menu_sector = -1
        self._menu_candidate = -1
        self._menu_focus_index = -1
        self._menu_drag_index = -1
        self._menu_keyboard_open = False
        self.update()

    def _open_radial_menu(self, sector: int) -> None:
        """Ouvre le menu radial demandé (raccourcis clavier 1..N)."""
        if not (0 <= sector < len(MENU_SPECS)):
            return
        self._menu_sector = sector
        self._menu_candidate = sector
        self._menu_candidate_time = self.time
        self._menu_last_stable_time = self.time
        self._menu_focus_index = -1
        self._menu_keyboard_open = True
        self._sync_menu_nodes(MENU_SPECS[sector])
        self.update()

    def _cached_status(self, key: str, producer, ttl: float = 1.0) -> str:
        """Valeur statique coûteuse (SQLite, disque) mise en cache.

        Le rendu appelle ces valeurs à chaque image (~60 FPS) : sans cache,
        on interrogerait la base soixante fois par seconde.
        """
        cached = self._status_cache.get(key)
        if cached is not None and (self.time - cached[0]) < ttl:
            return cached[1]
        try:
            value = str(producer())
        except Exception:
            value = "?"
        self._status_cache[key] = (self.time, value)
        return value

    def _invalidate_status_cache(self) -> None:
        self._status_cache.clear()

    def _mode_suppresses_visuals(self) -> bool:
        """Lecture légère du mode jeu : l'orbe ne doit rien afficher en jeu."""
        if (self.time - self._mode_checked_at) < 0.5:
            return self._visuals_suppressed_by_mode
        self._mode_checked_at = self.time
        try:
            from src.modes import get_default_mode_manager

            self._visuals_suppressed_by_mode = bool(
                get_default_mode_manager().should_suppress_visuals()
            )
        except Exception:
            self._visuals_suppressed_by_mode = False
        return self._visuals_suppressed_by_mode

    # =========================================================
    # GEOMETRY
    # =========================================================

    def _blob_path(self) -> QPainterPath:

        path = QPainterPath()

        first = self._point_to_screen(
            self.points[0].angle,
            self.points[0].radius
        )

        path.moveTo(first)

        for i in range(len(self.points)):

            p0 = self.points[i]
            p1 = self.points[(i + 1) % len(self.points)]

            a0 = self._point_to_screen(p0.angle, p0.radius)
            a1 = self._point_to_screen(p1.angle, p1.radius)

            mid = QPointF(
                (a0.x() + a1.x()) / 2,
                (a0.y() + a1.y()) / 2
            )

            path.quadTo(a0, mid)

        path.closeSubpath()
        return path

    def _point_to_screen(self, angle: float, radius: float) -> QPointF:
        return QPointF(
            self.current_center.x() + math.cos(angle) * radius,
            self.current_center.y() + math.sin(angle) * radius
        )

    def _sector_vector(self, sector: int) -> tuple[float, float]:
        angle = self._sector_angle(sector)
        return math.cos(angle), math.sin(angle)

    def _sector_angle(self, sector: int) -> float:
        """Angle du secteur : les menus se repartissent sur tout le cercle.

        Avec quatre menus on retrouve exactement les quadrants d'origine
        (haut, droite, bas, gauche) ; au-dela, l'espacement reste regulier.
        """
        count = max(1, len(MENU_SPECS))
        return (sector * (2.0 * math.pi / count)) - (math.pi / 2.0)

    def _sector_from_cursor(self, dx: float, dy: float) -> tuple[int, float, float]:
        dist = math.hypot(dx, dy)
        if dist <= 1e-6:
            return -1, 0.0, 0.0

        mx = dx / dist
        my = dy / dist

        axes = [
            (sector, *self._sector_vector(sector)) for sector in range(len(MENU_SPECS))
        ]

        scores = []
        for sector, ax, ay in axes:
            scores.append((sector, mx * ax + my * ay))

        scores.sort(key=lambda item: item[1], reverse=True)
        best_sector, best_score = scores[0]
        second_score = scores[1][1]
        return best_sector, best_score, best_score - second_score

    def _menu_spec(self) -> MenuSpec | None:
        if self._menu_sector < 0:
            return None
        return MENU_SPECS[self._menu_sector]

    def _menu_node_radius(self, item: MenuItemSpec) -> float:
        radii = {
            "toggle": 8.8,
            "slider": 8.4,
            "meter": 8.2,
            "status": 8.0,
            "card": 7.9,
            "chips": 8.2,
            "pulse": 8.6,
            "swatches": 8.4,
            "palette": 8.4,
            "buttonless": 7.8,
        }
        return radii.get(item.kind, 8.2)

    def _spec_for_name(self, name: str) -> MenuSpec | None:
        for spec in MENU_SPECS:
            if spec.name == name:
                return spec
        return None

    def _item_for(self, spec: MenuSpec, label: str) -> MenuItemSpec | None:
        for item in spec.items:
            if item.label == label:
                return item
        return None

    # ------------------------------------------------------------------
    # Valeurs affichées (textes) associées aux nœuds
    # ------------------------------------------------------------------
    def _appearance_value(self, label: str) -> str:
        if label == "Color":
            return self.appearance_state.theme_name.capitalize()
        if label == "Minimal Mode":
            return "On" if self.appearance_state.minimal_mode else "Off"
        if label == "Cinematic Mode":
            return "On" if self.appearance_state.cinematic_mode else "Off"
        if label.startswith("Glow"):
            return f"{self.appearance_state.glow_intensity:.2f}"
        if label.startswith("Blob Size"):
            return f"{self.appearance_state.blob_scale:.2f}"
        return ""

    def _voice_value(self, label: str) -> str:
        st = self.menu_state
        if label == "TTS Volume":
            return f"{st.tts_volume}%"
        if label == "Speech Speed":
            return f"{st.speech_speed}%"
        if label == "Voice Select":
            return menu_state.VOICE_OPTIONS[st.voice_select % len(menu_state.VOICE_OPTIONS)]
        if label == "Mic Toggle":
            return "On" if st.mic_enabled else "Off"
        if label == "Hotword Sens.":
            return f"{st.hotword_sensitivity}%"
        if label == "Always Listening":
            return "On" if st.listen_mode else "Off"
        if label == "Interrupt Word":
            return "On" if st.barge_in else "Off"
        if label == "Stop Speaking":
            return "Stop"
        return ""

    def _system_value(self, label: str) -> str:
        st = self.menu_state
        if label == "Startup":
            def _startup():
                return "On" if system_actions.startup_status() else "Off"
            return self._cached_status("startup", _startup, ttl=5.0)
        if label == "Always on Top":
            return "On" if st.always_on_top else "Off"
        if label == "Transparency":
            return f"{st.transparency}%"
        if label == "Response Mode":
            return self.system_state.response_mode_label
        if label == "Reset Settings":
            return "↺"
        if label == "Quit":
            return "⏻"
        return ""

    def _memory_value(self, label: str) -> str:
        if label == "Memory Count":
            def _count():
                from src.memory import get_default_memory_manager
                result = get_default_memory_manager().list_memories(limit=500)
                if result.get("success"):
                    return str(result.get("count", 0))
                return "0"
            return self._cached_status("memory_count", _count)
        if label == "Model State":
            def _state():
                from src.memory import get_default_memory_manager
                manager = get_default_memory_manager()
                return "On" if manager.enabled and manager.available else "Off"
            return self._cached_status("memory_state", _state)
        return ""

    def _menu_value(self, spec: MenuSpec, item: MenuItemSpec) -> str:
        if spec.name == "Appearance":
            return self._appearance_value(item.label)
        if spec.name == "Voice":
            return self._voice_value(item.label)
        if spec.name == "System":
            return self._system_value(item.label)
        if spec.name == "Memory":
            return self._memory_value(item.label)
        if spec.name == "Routines":
            return self._routines_value(item.label)
        return ""

    # ------------------------------------------------------------------
    # Routines
    # ------------------------------------------------------------------
    def _refresh_routines_spec(self) -> None:
        """Reconstruit le menu Routines quand routines.json a changé.

        La lecture disque est limitée à une fois par seconde et n'échoue
        jamais : l'orbe doit continuer à tourner même sans routines.
        """
        if (self.time - self._routines_checked_at) < 1.0:
            return
        self._routines_checked_at = self.time
        try:
            from src.routines import get_default_routine_manager

            mtime = get_default_routine_manager().mtime()
        except Exception:
            return
        if mtime == self._routines_mtime:
            return
        self._routines_mtime = mtime
        try:
            index = next(i for i, spec in enumerate(MENU_SPECS) if spec.name == "Routines")
        except StopIteration:
            return
        MENU_SPECS[index] = build_routines_spec()
        self._routines_cache = {}

    def _routines_value(self, label: str) -> str:
        if label == "Catalogue":
            def _count():
                from src.routines import get_default_routine_manager
                result = get_default_routine_manager().list_routines()
                items = result.get("routines", [])
                return f"{sum(bool(item.get('enabled')) for item in items)}/{len(items)}"
            return self._cached_status("routines_count", _count)
        if label == "Reload":
            return "↻"
        if label == "No routine":
            return "—"
        if label == "Reminders":
            def _count():
                from src.scheduler import get_default_scheduler

                result = get_default_scheduler().list_reminders(limit=100)
                return str(result.get("count", 0)) if result.get("success") else "Off"
            return self._cached_status("reminders_count", _count)

        cached = self._routines_cache.get(label)
        if cached is not None:
            return cached
        try:
            from src.routines import get_default_routine_manager

            result = get_default_routine_manager().list_routines()
            value = "▶"
            for item in result.get("routines", []):
                if str(item["name"])[:22] == label:
                    planning = str(item.get("planification") or "aucune")
                    hour = re.search(r"\d{1,2}:\d{2}", planning)
                    # Une routine planifiée affiche son heure, sinon un simple « lire ».
                    value = f"⏱ {hour.group(0)}" if hour else "▶"
                    break
        except Exception:
            value = "▶"
        self._routines_cache[label] = value
        return value

    def _run_routine_async(self, name: str) -> None:
        """Lance une routine hors du thread Qt : l'orbe ne doit jamais figer."""

        def _worker() -> None:
            try:
                from src.routines import get_default_routine_manager

                result = get_default_routine_manager().run_routine(name)
                if result.get("success"):
                    message = f"{name} ✓"
                else:
                    message = f"{name} : {result.get('error') or 'échec partiel'}"
            except Exception as exc:
                message = f"{name} : {exc}"
            # Écriture cross-thread tolérée (simple assignation, protégée
            # par le GIL) : le flash sera affiché par la prochaine image.
            self._menu_action_flash = message[:80]
            self._menu_action_flash_time = self.time

        threading.Thread(target=_worker, name="jarvis-routine", daemon=True).start()

    # ------------------------------------------------------------------
    # Contrôles interactifs (toggles / sliders / options)
    # ------------------------------------------------------------------
    def _menu_is_slider(self, spec: MenuSpec, item: MenuItemSpec) -> bool:
        return (spec.name, item.label) in {
            ("Voice", "TTS Volume"),
            ("Voice", "Speech Speed"),
            ("Voice", "Hotword Sens."),
            ("System", "Transparency"),
        }

    def _menu_is_option(self, spec: MenuSpec, item: MenuItemSpec) -> bool:
        return (spec.name, item.label) in {
            ("Voice", "Voice Select"),
        }

    def _menu_toggle_value(self, spec: MenuSpec, item: MenuItemSpec) -> bool:
        name, label = spec.name, item.label
        if name == "Voice" and label == "Mic Toggle":
            return self.menu_state.mic_enabled
        if name == "Voice" and label == "Always Listening":
            return self.menu_state.listen_mode
        if name == "Voice" and label == "Interrupt Word":
            return self.menu_state.barge_in
        if name == "System" and label == "Startup":
            # État réel (fichier de démarrage présent ou non), mis en cache :
            # cette valeur est lue à chaque image pour le rendu.
            return self._cached_status("startup_bool", system_actions.startup_status, ttl=5.0) == "True"
        if name == "System" and label == "Always on Top":
            return self.menu_state.always_on_top
        if name == "Memory" and label == "Long-term Memory":
            def _enabled():
                from src.memory import get_default_memory_manager
                manager = get_default_memory_manager()
                return bool(manager.enabled and manager.available)
            return self._cached_status("memory_enabled", _enabled) == "True"
        return False

    def _menu_set_toggle(self, spec: MenuSpec, item: MenuItemSpec, value: bool) -> None:
        name, label = spec.name, item.label
        value = bool(value)
        if name == "Voice" and label == "Mic Toggle":
            self.menu_state.mic_enabled = value
            menu_state.LIVE.set_mic_enabled(value)
        elif name == "Voice" and label == "Always Listening":
            self.menu_state.listen_mode = value
            menu_state.LIVE.set_listen_mode(value)
            self._flash(
                "Écoute continue : activée (plus besoin de dire Hey Jarvis)"
                if value
                else "Écoute continue : désactivée (« Hey Jarvis » à nouveau requis)"
            )
        elif name == "Voice" and label == "Interrupt Word":
            self.menu_state.barge_in = value
            menu_state.LIVE.set_barge_in(value)
            self._flash(
                "Interruption vocale : activée (dites « stop » pour couper Jarvis)"
                if value
                else "Interruption vocale : désactivée"
            )
        elif name == "System" and label == "Startup":
            result = system_actions.set_startup(value)
            self.menu_state.startup = bool(result.get("success") and value)
            self.menu_state.startup_managed = True
            self._invalidate_status_cache()
            if not result.get("success"):
                self._flash(f"Lancement auto : {result.get('error', 'indisponible')}")
                return
            self._flash("Lancement avec Windows : " + ("activé" if value else "désactivé"))
        elif name == "System" and label == "Always on Top":
            self.menu_state.always_on_top = value
            self._apply_always_on_top(value)
        elif name == "Memory" and label == "Long-term Memory":
            try:
                from src.memory import get_default_memory_manager
                get_default_memory_manager().set_enabled(value)
            except Exception:
                pass
            self._invalidate_status_cache()
            self._flash("Mémoire longue durée : " + ("activée" if value else "désactivée"))
        self._save_menu_state()

    def _menu_slider_value(self, spec: MenuSpec, item: MenuItemSpec) -> int:
        name, label = spec.name, item.label
        if name == "Voice" and label == "TTS Volume":
            return self.menu_state.tts_volume
        if name == "Voice" and label == "Speech Speed":
            return self.menu_state.speech_speed
        if name == "Voice" and label == "Hotword Sens.":
            return self.menu_state.hotword_sensitivity
        if name == "System" and label == "Transparency":
            return self.menu_state.transparency
        return 0

    def _menu_set_slider(self, spec: MenuSpec, item: MenuItemSpec, value: int) -> None:
        name, label = spec.name, item.label
        value = max(0, min(100, int(value)))
        if name == "Voice" and label == "TTS Volume":
            self.menu_state.tts_volume = value
            # Appliqué immédiatement sur la sortie audio.
            menu_state.LIVE.set_tts_volume(value)
        elif name == "Voice" and label == "Speech Speed":
            self.menu_state.speech_speed = value
            # Injecté dans le prompt système de la prochaine session.
            menu_state.LIVE.set_speech_speed(value)
        elif name == "Voice" and label == "Hotword Sens.":
            self.menu_state.hotword_sensitivity = value
            menu_state.LIVE.set_hotword_sensitivity(value)
        elif name == "System" and label == "Transparency":
            self.menu_state.transparency = value
            self._apply_transparency(value)
        self._save_menu_state()

    def _menu_cycle_option(self, spec: MenuSpec, item: MenuItemSpec, step: int = 1) -> None:
        name, label = spec.name, item.label
        if name == "Voice" and label == "Voice Select":
            count = len(menu_state.VOICE_OPTIONS)
            current = self.menu_state.voice_select % count
            self.menu_state.voice_select = (current + step) % count
            # La nouvelle voix demande une reconnexion de la session Live :
            # le backend la détecte via LIVE.get_voice_version().
            menu_state.LIVE.set_voice_index(self.menu_state.voice_select)
        self._save_menu_state()

    # ------------------------------------------------------------------
    # Effets appliqués au widget
    # ------------------------------------------------------------------
    def _apply_always_on_top(self, value: bool) -> None:
        try:
            self.setWindowFlag(Qt.WindowStaysOnTopHint, bool(value))
            self.show()
        except Exception:
            pass

    def _apply_transparency(self, value: int) -> None:
        try:
            self.setWindowOpacity(max(0.15, min(1.0, int(value) / 100.0)))
        except Exception:
            pass

    def _audio_test(self) -> None:
        """Émet un bip de test synthétisé (sinus doux), avec repli sur le
        bip système si l'audio multimédia n'est pas disponible."""
        try:
            self._play_test_tone()
        except Exception:
            try:
                QApplication.beep()
            except Exception:
                pass
        self.pulse = 1.0

    def _play_test_tone(self) -> None:
        import math as _math
        import struct
        import tempfile
        import wave

        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QSoundEffect

        if getattr(self, "_test_sound_path", None) is None:
            rate = 24000
            duration = 0.22
            samples = []
            n = int(rate * duration)
            for i in range(n):
                # Enveloppe douce pour éviter les clics audibles.
                envelope = _math.sin(_math.pi * i / n) ** 2
                value = int(12000 * envelope * _math.sin(2 * _math.pi * 880.0 * i / rate))
                samples.append(struct.pack("<h", value))
            handle = tempfile.NamedTemporaryFile(
                suffix=".wav", prefix="jarvis_test_", delete=False
            )
            try:
                with wave.open(handle, "wb") as wav:
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(rate)
                    wav.writeframes(b"".join(samples))
                handle.close()
                self._test_sound_path = handle.name
            except Exception:
                handle.close()
                raise

        effect = QSoundEffect(self)
        effect.setSource(QUrl.fromLocalFile(self._test_sound_path))
        effect.setVolume(0.6)
        effect.play()
        # Référence gardée vive le temps de la lecture.
        self._test_effect = effect

    def _stop_speaking(self) -> None:
        """Coupe immédiatement la réponse en cours (bouton / touche S).

        Le backend vocal enregistre sa poignée dans ``menu_state.LIVE`` ;
        si l'UI tourne seule (aucun assistant démarré), on le dit
        honnêtement plutôt que de faire semblant.
        """
        stopped = False
        try:
            stopped = menu_state.LIVE.request_stop_speaking()
        except Exception:
            stopped = False
        self.pulse = 1.0
        self._flash(
            "Réponse interrompue" if stopped else "Aucune réponse à interrompre"
        )

    def _reset_settings(self) -> None:
        """Réinitialise les réglages interactifs du menu (valeurs par défaut)."""
        self.menu_state = menu_state.MenuState()
        menu_state.save_state(self.menu_state, self._menu_state_path)
        self._apply_always_on_top(self.menu_state.always_on_top)
        self._apply_transparency(self.menu_state.transparency)
        self._invalidate_status_cache()
        self._flash("Réglages réinitialisés")

    def _save_menu_state(self, force: bool = False) -> None:
        """Sauvegarde l'état du menu, débouncée pendant les interactions.

        Un glissement de slider génère des dizaines d'appels par seconde :
        on écrit au plus toutes les 0,5 s, et immédiatement à la fin du
        glissement ou à la fermeture (`force=True`).
        """
        if not force:
            if (self.time - self._menu_state_last_save) < 0.5:
                self._menu_state_dirty = True
                return
        menu_state.save_state(self.menu_state, self._menu_state_path)
        self._menu_state_last_save = self.time
        self._menu_state_dirty = False

    def _menu_callback_for(self, spec: MenuSpec, item: MenuItemSpec) -> Callable[[], None]:
        if spec.name == "Appearance":
            return self._appearance_callback_for(item)

        def _callback() -> None:
            name, label = spec.name, item.label
            if name == "Routines" and item.routine_name:
                self._flash(f"{label} …")
                self._run_routine_async(item.routine_name)
            elif name == "Routines" and label == "Catalogue":
                from .routines_dialog import show_routines_dialog
                self._close_radial_menu()
                show_routines_dialog(self)
            elif self._menu_is_slider(spec, item):
                self._flash(f"{label}: {self._menu_slider_value(spec, item)}%")
            elif self._menu_is_option(spec, item):
                self._menu_cycle_option(spec, item)
                extra = ""
                if name == "Voice" and label == "Voice Select":
                    extra = " — reconnexion…"
                self._flash(f"{label}: {self._menu_value(spec, item)}{extra}")
            elif item.kind == "toggle":
                current = self._menu_toggle_value(spec, item)
                self._menu_set_toggle(spec, item, not current)
                # Les toggles riches affichent leur propre message explicite
                # via _flash ; on n'écrase pas leur libellé.
                if label in {
                    "Always Listening",
                    "Interrupt Word",
                    "Startup",
                    "Long-term Memory",
                }:
                    pass
                else:
                    new_value = not current
                    messages = {
                        "Mic Toggle": ("Micro : activé", "Micro : coupé"),
                        "Always on Top": (
                            "Toujours au premier plan : activé",
                            "Toujours au premier plan : désactivé",
                        ),
                    }
                    if label in messages:
                        self._flash(messages[label][0 if new_value else 1])
                    else:
                        self._flash(f"{label}: {'On' if new_value else 'Off'}")
            elif name == "Voice" and label == "Stop Speaking":
                self._stop_speaking()
            elif name == "Voice" and label == "Audio Test":
                self._audio_test()
                self._flash(f"{label} ▶")
            elif name == "System" and label == "Response Mode":
                system_actions.cycle_response_mode(self.system_state)
                system_actions.save_state(self.system_state, self._system_state_path)
                menu_state.LIVE.set_response_mode_index(self.system_state.response_mode_index)
                self._flash(f"Réponses : {self.system_state.response_mode_label}")
            elif name == "System" and label == "Reset Settings":
                self._reset_settings()
            elif name == "System" and label == "Quit":
                self._flash("À bientôt !")
                self.close()
            elif name == "Routines" and label == "Reload":
                self._routines_mtime = -1.0
                self._routines_checked_at = -10.0
                self._routines_cache = {}
                self._invalidate_status_cache()
                self._flash("Routines rechargées")
            elif name == "Routines" and label in {"No routine", "Reminders"}:
                if label == "Reminders":
                    count = self._menu_value(spec, item)
                    self._flash(f"Rappels actifs : {count} (demandez-les à la voix)")
                else:
                    self._flash("Aucune routine — créez-la à la voix")
            elif name == "Routines":
                self._flash(f"{label} …")
                self._run_routine_async(label)
            else:
                self._flash(item.label)

        return _callback

    def _appearance_callback_for(self, item: MenuItemSpec) -> Callable[[], None]:
        action_map = {
            "Color": appearance_actions.cycle_theme,
            "Glow +": appearance_actions.increase_glow,
            "Glow -": appearance_actions.decrease_glow,
            "Blob Size +": appearance_actions.increase_blob_size,
            "Blob Size -": appearance_actions.decrease_blob_size,
            "Minimal Mode": appearance_actions.toggle_minimal_mode,
            "Cinematic Mode": appearance_actions.toggle_cinematic_mode,
        }
        action = action_map.get(item.label)
        if action is None:
            return lambda: None
        def _callback() -> None:
            action(self.appearance_state)
            self._apply_appearance_state()
            appearance_actions.save_state(self.appearance_state, self._appearance_state_path)
            self._flash(f"{item.label}: {self._appearance_value(item.label)}")
        return _callback

    def _apply_appearance_state(self) -> None:
        state = self.appearance_state
        # Appelé à chaque frame par tick() : on ne recrée les QColor que si
        # l'état d'apparence a réellement changé.
        signature = (
            state.theme_name,
            round(state.glow_intensity, 3),
            round(state.blob_scale, 3),
            round(state.time_scale, 3),
            state.minimal_mode,
            state.cinematic_mode,
        )
        if signature == self._appearance_signature:
            return
        self._appearance_signature = signature
        self.base_radius = self._base_radius_default * state.blob_scale
        self.glow_color = QColor(state.glow_color)
        self.bg = QColor(state.bg_color)
        self.text_color = QColor(state.text_color)
        self.time_scale = state.time_scale

    def _apply_system_state(self) -> None:
        pass

    def _menu_anchor(self, spec: MenuSpec, reveal: float) -> tuple[QPointF, float, float, float, float]:
        sector_index = MENU_SPECS.index(spec)
        sx, sy = self._sector_vector(sector_index)
        perp_x = -sy
        perp_y = sx
        branch_extent = self.base_radius * (1.16 + 0.24 * reveal + 0.05 * spec.branch_bias)
        anchor = QPointF(
            self.current_center.x() + sx * branch_extent,
            self.current_center.y() + sy * branch_extent,
        )
        return anchor, sx, sy, perp_x, perp_y

    def _sync_menu_nodes(self, spec: MenuSpec) -> None:
        labels = [item.label for item in spec.items]
        stale = (
            len(self._menu_nodes) != len(spec.items)
            or any(node.section != spec.name for node in self._menu_nodes)
            or [node.label for node in self._menu_nodes] != labels
        )
        if stale:
            self._menu_nodes = [
                MenuNode(
                    label=item.label,
                    position=QPointF(self.current_center),
                    hover_amount=0.0,
                    radius=self._menu_node_radius(item),
                    visible_amount=0.0,
                    callback=self._menu_callback_for(spec, item),
                    active=True,
                    section=spec.name,
                    kind=item.kind,
                )
                for item in spec.items
            ]
            self._menu_hot_node = -1

    def _update_menu_nodes(self) -> None:
        self._refresh_routines_spec()
        spec = self._menu_spec()
        if spec is None or self._menu_alpha <= 0.01:
            for node in self._menu_nodes:
                node.visible_amount = lerp(node.visible_amount, 0.0, 0.12)
                node.hover_amount = lerp(node.hover_amount, 0.0, 0.12)
                node.click_amount = lerp(node.click_amount, 0.0, 0.16)
            if all(node.visible_amount < 0.02 for node in self._menu_nodes):
                self._menu_nodes.clear()
            self._menu_hot_node = -1
            self._menu_focus_index = -1
            self._set_pointer_cursor(False)
            return

        self._sync_menu_nodes(spec)
        if not (0 <= self._menu_focus_index < len(self._menu_nodes)):
            self._menu_focus_index = -1
        reveal = clamp(self._menu_reveal, 0.0, 1.25)
        alpha = clamp(self._menu_alpha, 0.0, 1.0)
        anchor, sx, sy, perp_x, perp_y = self._menu_anchor(spec, reveal)
        count = len(self._menu_nodes)
        if count == 0:
            return

        # Menu plutot vertical (haut/bas) : grille ; plutot horizontal : ligne.
        self._menu_layout_mode = "grid" if abs(sy) >= abs(sx) else "line"

        hover_index = -1
        hover_score = -1.0
        count_mid = (count - 1) * 0.5
        spacing = 20.0 + 2.0 * spec.branch_bias
        if self._menu_layout_mode == "line":
            spacing += 16.0
        curve_strength = 7.0 + 5.0 * reveal

        for index, node in enumerate(self._menu_nodes):
            if self._menu_layout_mode == "grid":
                columns = 2
                rows = max(1, math.ceil(count / columns))
                col = index % columns
                row = index // columns
                col_offset = (col - 0.5) * 126.0
                row_offset = (row - (rows - 1) * 0.5) * 46.0
                target = QPointF(
                    anchor.x() + perp_x * col_offset + sx * (34.0 + row * 5.0 + curve_strength),
                    anchor.y() + perp_y * col_offset + sy * (34.0 + row * 5.0 + curve_strength) + row_offset,
                )
            else:
                lane = (index - count_mid)
                target = QPointF(
                    anchor.x() + perp_x * lane * spacing + sx * (18.0 + index * 2.4 + curve_strength),
                    anchor.y() + perp_y * lane * spacing + sy * (18.0 + index * 2.4 + curve_strength),
                )
            node.position.setX(lerp(node.position.x(), target.x(), 0.14))
            node.position.setY(lerp(node.position.y(), target.y(), 0.14))
            node.active = True
            node.visible_amount = lerp(node.visible_amount, alpha * (0.82 + 0.05 * index), 0.12)
            node.click_amount = lerp(node.click_amount, 0.0, 0.12)

            dist = math.hypot(self.cursor.x() - node.position.x(), self.cursor.y() - node.position.y())
            hit_radius = node.radius * 1.45 + 7.0
            hover_target = clamp(1.0 - (dist / max(hit_radius, 1.0)), 0.0, 1.0)
            node.hover_amount = lerp(node.hover_amount, hover_target, 0.18)

            # Un nœud n'est « chaud » que s'il est réellement survolé :
            # sinon le premier nœud de la liste gardait un focus fantôme
            # (la molette modifiait alors un réglage sans survol).
            if node.hover_amount > hover_score and node.hover_amount > 0.05:
                hover_score = node.hover_amount
                hover_index = index

        self._menu_hot_node = hover_index
        self._set_pointer_cursor(hover_index >= 0)

    def _set_pointer_cursor(self, active: bool) -> None:
        """Curseur « main » au survol d'une cible cliquable."""
        if active == self._pointer_cursor_active:
            return
        self._pointer_cursor_active = active
        try:
            from PySide6.QtGui import QCursor

            self.setCursor(QCursor(Qt.PointingHandCursor if active else Qt.ArrowCursor))
        except Exception:
            pass

    def _draw_organic_menu_v2(self, painter: QPainter):
        spec = self._menu_spec()
        if spec is None or not self._menu_nodes:
            return

        reveal = clamp(self._menu_reveal, 0.0, 1.25)
        anchor, sx, sy, perp_x, perp_y = self._menu_anchor(spec, reveal)
        title_pos = QPointF(
            (anchor.x() + self.current_center.x()) / 2.0,
            (anchor.y() + self.current_center.y()) / 2.0,
        )

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        alpha = int(235 * self._menu_alpha)
        title_font = QFont("Segoe UI", 9)
        title_font.setLetterSpacing(QFont.AbsoluteSpacing, 1.2)
        painter.setFont(title_font)
        state = self.appearance_state
        painter.setPen(QColor(state.text_color.red(), state.text_color.green(), state.text_color.blue(), alpha))
        painter.drawText(
            QRectF(title_pos.x() - 95.0, title_pos.y() - 10.0, 190.0, 18.0),
            Qt.AlignCenter,
            f"{spec.icon} {spec.name.upper()}",
        )

        for index, node in enumerate(self._menu_nodes):
            self._draw_menu_node(painter, spec, node, index, anchor, sx, sy, perp_x, perp_y)

        painter.restore()

    def _draw_menu_node(
        self,
        painter: QPainter,
        spec: MenuSpec,
        node: MenuNode,
        index: int,
        anchor: QPointF,
        sx: float,
        sy: float,
        perp_x: float,
        perp_y: float,
    ) -> None:
        visible = clamp(node.visible_amount, 0.0, 1.0)
        if visible <= 0.01:
            return

        item = self._item_for(spec, node.label)
        hover = clamp(node.hover_amount, 0.0, 1.0)
        # Le nœud portant le focus clavier est mis en évidence comme s'il
        # était survolé (navigation flèches + Entrée).
        if index == self._menu_focus_index:
            hover = max(hover, 0.85)
        click = clamp(node.click_amount, 0.0, 1.0)
        line_alpha = int((52 + 120 * hover + 80 * click) * visible)
        node_alpha = int((130 + 90 * hover + 60 * click) * visible)
        text_alpha = int((175 + 60 * hover + 40 * click) * visible)
        state = self.appearance_state

        # Couleur d'accent pour les toggles actifs (état ON).
        is_toggle_on = (
            item is not None
            and item.kind == "toggle"
            and self._menu_toggle_value(spec, item)
        )

        control = QPointF(
            (anchor.x() + node.position.x()) / 2.0 + perp_x * (6.0 + 4.0 * hover),
            (anchor.y() + node.position.y()) / 2.0 + perp_y * (6.0 + 4.0 * hover),
        )
        path = QPainterPath(anchor)
        path.quadTo(control, node.position)
        curve_color = QColor(state.glow_color.red(), state.glow_color.green(), state.glow_color.blue(), line_alpha)
        painter.setPen(QPen(curve_color, 1.2 + 1.1 * hover))
        painter.setBrush(Qt.NoBrush)
        painter.drawPath(path)

        pulse_radius = node.radius * (1.0 + 0.18 * hover + 0.08 * click)
        glow_radius = pulse_radius * (1.75 + 0.25 * hover)
        glow_color = QColor(state.glow_color.red(), state.glow_color.green(), state.glow_color.blue(), int(node_alpha * 0.35))
        painter.setPen(Qt.NoPen)
        painter.setBrush(glow_color)
        painter.drawEllipse(node.position, glow_radius, glow_radius)

        fill_color = QColor(
            state.glow_color.red(),
            state.glow_color.green(),
            state.glow_color.blue(),
            int(node_alpha * 0.18),
        )
        if is_toggle_on:
            fill_color = QColor(
                state.glow_color.red(),
                state.glow_color.green(),
                state.glow_color.blue(),
                int(node_alpha * 0.40),
            )
        outline_color = QColor(
            state.text_color.red(),
            state.text_color.green(),
            state.text_color.blue(),
            int(node_alpha * 0.82),
        )
        if is_toggle_on:
            outline_color = QColor(120, 240, 170, int(node_alpha * 0.95))
        painter.setBrush(fill_color)
        painter.setPen(QPen(outline_color, 1.0 + 0.55 * hover))
        painter.drawEllipse(node.position, pulse_radius, pulse_radius)

        # Petit indicateur de valeur pour les sliders.
        if item is not None:
            if self._menu_is_slider(spec, item):
                value = self._menu_slider_value(spec, item) / 100.0
                bar_len = pulse_radius * 1.9
                bar_height = 2.4 + 1.2 * hover
                bar_x = node.position.x() - bar_len / 2.0
                bar_y = node.position.y() + pulse_radius + 5.0
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(255, 255, 255, int(node_alpha * 0.22)))
                painter.drawRect(QRectF(bar_x, bar_y, bar_len, bar_height))
                painter.setBrush(QColor(
                    state.glow_color.red(),
                    state.glow_color.green(),
                    state.glow_color.blue(),
                    int(node_alpha * 0.9),
                ))
                painter.drawRect(QRectF(bar_x, bar_y, bar_len * value, bar_height))

        text_font = QFont("Segoe UI", 9)
        if self._menu_layout_mode == "grid":
            text_font.setPointSizeF(8.3)
        text_font.setLetterSpacing(QFont.AbsoluteSpacing, 0.6 + 0.5 * hover)
        painter.setFont(text_font)
        label_dx = 22.0 + 4.0 * hover + 2.0 * click
        sector_sx, sector_sy = self._sector_vector(max(0, self._menu_sector))
        if self._menu_layout_mode == "grid":
            if sector_sy > 0.0:  # menu deploye vers le bas
                label_pos = QPointF(node.position.x() + label_dx, node.position.y())
                label_rect = QRectF(label_pos.x(), label_pos.y() - 9.0, 160.0, 18.0)
                label_alignment = Qt.AlignLeft | Qt.AlignVCenter
            else:
                label_pos = QPointF(
                    node.position.x() + perp_x * (34.0 + 3.0 * hover),
                    node.position.y() + perp_y * (34.0 + 3.0 * hover),
                )
                label_rect = QRectF(label_pos.x() - 8.0, label_pos.y() - 8.0, 170.0, 18.0)
                label_alignment = Qt.AlignLeft | Qt.AlignVCenter
        elif sector_sx < 0.0:  # menu deploye vers la gauche : libelle aligne a droite
            label_pos = QPointF(node.position.x() - label_dx, node.position.y())
            label_rect = QRectF(label_pos.x() - 130.0, label_pos.y() - 9.0, 120.0, 18.0)
            label_alignment = Qt.AlignRight | Qt.AlignVCenter
        else:  # menu deploye vers la droite : libelle a droite du noeud
            label_pos = QPointF(node.position.x() + label_dx, node.position.y())
            label_rect = QRectF(label_pos.x(), label_pos.y() - 9.0, 150.0, 18.0)
            label_alignment = Qt.AlignLeft | Qt.AlignVCenter
        painter.setPen(QColor(state.text_color.red(), state.text_color.green(), state.text_color.blue(), text_alpha))
        label_text = node.label
        if node.label == "Long-term Memory":
            label_text = "Long-term\nMemory"
            label_rect = QRectF(label_rect.x(), label_rect.y() - 3.0, label_rect.width(), 30.0)
        else:
            value_text = self._menu_value(spec, node)
            if value_text and value_text != "Clear":
                label_text = f"{node.label} · {value_text}"
        painter.drawText(
            label_rect,
            label_alignment,
            label_text,
        )

    # =========================================================
    # DRAWING
    # =========================================================

    def _draw_action_flash(self, painter: QPainter) -> None:
        """Retour d'action : pastille de confirmation au-dessus de l'orbe.

        Sans ce feedback, un clic dans le menu radial ne donnait aucune
        confirmation visuelle (« est-ce que ça a marché ? »).
        """
        message = self._menu_action_flash
        if not message or self._menu_action_flash_time < 0.0:
            return
        age = self.time - self._menu_action_flash_time
        duration = 2.2
        if age > duration or age < 0.0:
            return
        fade = 1.0
        if age < 0.12:
            fade = age / 0.12  # apparition rapide
        elif age > duration - 0.6:
            fade = max(0.0, (duration - age) / 0.6)

        state = self.appearance_state
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        font = QFont("Segoe UI", 10)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 0.7)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(message)
        text_h = metrics.height()
        pad_x = 14.0
        pad_y = 7.0
        cx = self.current_center.x()
        cy = self.current_center.y() - self.base_radius - 44.0
        rect = QRectF(
            cx - (text_w + 2 * pad_x) / 2.0,
            cy - (text_h + 2 * pad_y) / 2.0,
            text_w + 2 * pad_x,
            text_h + 2 * pad_y,
        )
        alpha = int(215 * fade)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(8, 16, 26, alpha))
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        border = QColor(
            state.glow_color.red(),
            state.glow_color.green(),
            state.glow_color.blue(),
            int(150 * fade),
        )
        painter.setPen(QPen(border, 1.2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(rect, rect.height() / 2.0, rect.height() / 2.0)
        text_color = QColor(
            state.text_color.red(),
            state.text_color.green(),
            state.text_color.blue(),
            int(245 * fade),
        )
        painter.setPen(text_color)
        painter.drawText(rect, Qt.AlignCenter, message)
        painter.restore()

    def _draw_status_label(self, painter: QPainter) -> None:
        """Indicateur discret d'état sous l'orbe : initialisation, écoute,
        réponse, veille ou micro coupé. L'utilisateur sait toujours où il
        en est sans regarder la console."""
        try:
            mic_on = menu_state.LIVE.get_mic_enabled()
        except Exception:
            mic_on = True

        state = self.appearance_state
        if not mic_on:
            label = "MICRO COUPÉ"
            dot_color = QColor(255, 140, 130)
        elif presence_state == "loading":
            label = "INITIALISATION…"
            dot_color = QColor(160, 170, 190)
        elif presence_state == "listening":
            label = "À L'ÉCOUTE"
            dot_color = QColor(state.glow_color)
        elif presence_state == "thinking":
            label = "RÉFLEXION…"
            dot_color = QColor(state.glow_color)
        elif presence_state == "speaking":
            label = "RÉPONSE EN COURS"
            dot_color = QColor(state.glow_color).lighter(130)
        else:
            return

        # Discret : alpha qui respire doucement.
        alpha = int(120 + 40 * self.pulse)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        font = QFont("Segoe UI", 8)
        font.setLetterSpacing(QFont.AbsoluteSpacing, 2.0)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_w = metrics.horizontalAdvance(label)
        cx = self.current_center.x()
        cy = self.current_center.y() + self.base_radius + 40.0

        dot = QColor(dot_color)
        dot.setAlpha(alpha)
        painter.setPen(Qt.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QPointF(cx - text_w / 2.0 - 12.0, cy), 3.0, 3.0)

        text_color = QColor(
            state.text_color.red(),
            state.text_color.green(),
            state.text_color.blue(),
            alpha,
        )
        painter.setPen(text_color)
        painter.drawText(
            QRectF(cx - text_w / 2.0, cy - metrics.height() / 2.0, text_w + 4.0, metrics.height()),
            Qt.AlignLeft | Qt.AlignVCenter,
            label,
        )
        painter.restore()

    def _draw_glow(self, painter: QPainter, path: QPainterPath):

        state = self.appearance_state
        halo = clamp(self._halo_energy * state.glow_intensity * self._mic_mute_factor, 0.0, 1.85)
        voice = clamp(voice_energy, 0.0, 1.0)

        layers = [
            (44 + halo * 6.0, 6 + int(halo * 5.0)),
            (32 + halo * 4.0, 12 + int(halo * 8.0)),
            (20 + halo * 3.0, 24 + int(halo * 14.0)),
            (10 + halo * 2.0, 54 + int(halo * 12.0) + int(voice * 8.0)),
        ]

        if state.minimal_mode:
            layers = layers[-2:]

        for width, alpha in layers:

            c = QColor(state.glow_color)
            c.setAlpha(int(clamp(alpha + self._cursor_speed * 10.0, 0, 255)))

            painter.setPen(QPen(c, width))
            painter.setBrush(Qt.NoBrush)
            painter.drawPath(path)

    def _draw_inner(self, painter: QPainter, path: QPainterPath):

        state = self.appearance_state
        halo = clamp(self._halo_energy * state.glow_intensity * self._mic_mute_factor, 0.0, 1.85)
        voice = clamp(voice_energy, 0.0, 1.0)
        presence = clamp(self._presence_energy, 0.0, 1.0)

        gradient = QRadialGradient(
            self.current_center,
            self.base_radius * (1.45 + 0.12 * halo + 0.10 * voice)
        )

        gradient.setColorAt(0.0, QColor(state.glow_color))
        gradient.setColorAt(0.10, QColor(state.glow_color).lighter(145))
        gradient.setColorAt(0.36, QColor(state.glow_color).darker(220))
        gradient.setColorAt(1.0, QColor(4, 8, 16))

        painter.setPen(Qt.NoPen)
        painter.setBrush(gradient)
        painter.drawPath(path)

        core_radius = (
            self.base_radius * (0.22 + 0.04 * voice + 0.05 * presence)
            + 8 * self.pulse
            + 6.0 * halo
            + 10.0 * presence
        )

        core = QPainterPath()
        core.addEllipse(self.current_center, core_radius, core_radius)

        painter.setBrush(QColor(state.glow_color.red(), state.glow_color.green(), state.glow_color.blue(), int(150 + 55 * halo + 30 * voice + 40 * presence)))
        painter.drawPath(core)

    def _draw_organic_menu(self, painter: QPainter):
        # Ancienne implémentation du menu (prototype). Remplacée par
        # `_draw_organic_menu_v2`. Conservée comme coquille vide pour
        # éviter toute référence à des symboles supprimés (SECTOR_ITEMS).
        return

    # =========================================================
    # PAINT
    # =========================================================

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)

            painter.fillRect(self.rect(), Qt.transparent)

            path = self._blob_path()

            self._draw_glow(painter, path)
            self._draw_inner(painter, path)
            self._draw_organic_menu_v2(painter)
            self._draw_status_label(painter)
            self._draw_action_flash(painter)
        finally:
            painter.end()


def main() -> int:
    app = QApplication(sys.argv)
    # Démo standalone : aucun backend vocal ne suivra, on repart en veille
    # plutôt que d'afficher « INITIALISATION… » pour toujours.
    set_presence_state("hidden")
    window = MorphingOrbWidget()
    window.showFullScreen()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
