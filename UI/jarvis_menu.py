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
import sys
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


def set_voice_energy(value: float) -> None:
    """Permet au backend (Gemini Live) de piloter la réactivité de l'orbe.

    `value` doit être compris entre 0.0 (repos) et 1.0 (parole/écoute active).
    """
    global voice_energy
    voice_energy = clamp(float(value), 0.0, 1.0)


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
            MenuItemSpec("Hotword Sens.", "meter"),
            MenuItemSpec("Listen Mode", "toggle"),
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
            MenuItemSpec("Overlay", "toggle"),
            MenuItemSpec("Always on Top", "toggle"),
            MenuItemSpec("Transparency", "slider"),
            MenuItemSpec("Shortcuts", "chips"),
            MenuItemSpec("Permissions", "status"),
            MenuItemSpec("Response Mode", "meter"),
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
            MenuItemSpec("Chat History", "card"),
            MenuItemSpec("Long-term Memory", "card"),
            MenuItemSpec("Temp Memory", "card"),
            MenuItemSpec("Clear Cache", "buttonless"),
            MenuItemSpec("Context Summary", "card"),
            MenuItemSpec("Model State", "status"),
            MenuItemSpec("Saved Threads", "meter"),
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

class MorphingOrbWidget(QWidget):

    def __init__(self) -> None:
        super().__init__()

        self.setWindowTitle("Jarvis — Morphing Orb Prototype")
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
        self._apply_appearance_state()
        self._system_state_path = os.path.join(os.path.dirname(__file__), "system_state.json")
        self.system_state = system_actions.load_state(self._system_state_path)

        # STATE
        self.center = QPointF(self.width() / 2, self.height() / 2)
        self.current_center = QPointF(self.center)
        self.cursor = QPointF(self.center)

        self.base_radius = self._base_radius_default

        self.time = 0.0
        self.pulse = 0.0

        self.offset_x = 0.0
        self.offset_y = 0.0

        self.stable_angle = 0.0
        self._prev_cursor = QPointF(self.cursor)
        self._cursor_speed = 0.0
        self._halo_energy = 0.0
        self._deform_energy = 0.0
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
        self._menu_toggle_state: dict[str, bool] = {}
        self._menu_layout_mode = "line"
        self._menu_hold_seconds = 0.38
        self._menu_release_seconds = 0.18
        self._menu_exit_seconds = 0.24
        # Distance-based close + sector switching: interaction du menu radial.
        self._menu_distance_close_enabled = True
        # Interactions directes sur les nœuds (sliders / molette).
        self._menu_drag_index = -1
        self._menu_drag_axis = ""
        self._menu_drag_start_x = 0.0
        self._menu_drag_start_value = 0
        self._menu_wheel_index = -1
        # État interactif persistant du menu.
        self._menu_state_path = os.path.join(os.path.dirname(__file__), "menu_state.json")
        self.menu_state = menu_state.load_state(self._menu_state_path)
        self._menu_open_projection = self.base_radius * 0.42
        self._menu_close_projection = self.base_radius * 3.00
        self._menu_open_lateral = self.base_radius * 1.15

        # SHAPE
        self.num_points = 96
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

        if self._menu_sector >= 0:
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
            self._menu_sector = sector
            active_spec = MENU_SPECS[sector]
            self._menu_last_stable_time = self.time
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
                sector_angle = (self._menu_sector * (math.pi / 2.0)) - (math.pi / 2.0)
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
                delta = pos.x() - self._menu_drag_start_x
                factor = 100.0 / 140.0  # 140 px de glissement = 100% de variation
                value = self._menu_drag_start_value + int(round(delta * factor))
                self._menu_set_slider(spec, item, value)
                self._menu_action_flash = f"{item.label}: {self._menu_slider_value(spec, item)}%"
                self._menu_action_flash_time = self.time

        self.update()

    def mouseReleaseEvent(self, event):
        self._menu_drag_index = -1
        self._menu_drag_axis = ""
        self.update()
        super().mouseReleaseEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton and self._menu_nodes:
            hit = self._menu_hit_test(event.position())
            if hit is not None:
                index, node, spec, item = hit
                if spec is not None and item is not None and self._menu_is_slider(spec, item):
                    # Démarre un glisser-ajuster sur le slider, sans déclencher le callback.
                    self._menu_drag_index = index
                    self._menu_drag_axis = item.label
                    self._menu_drag_start_x = event.position().x()
                    self._menu_drag_start_value = self._menu_slider_value(spec, item)
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
            node = self._menu_hot_node
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
                        step = 5 if event.angleDelta().y() > 0 else -5
                        self._menu_set_slider(spec, item, self._menu_slider_value(spec, item) + step)
                        self._menu_action_flash = f"{item.label}: {self._menu_slider_value(spec, item)}%"
                        self._menu_action_flash_time = self.time
                        event.accept()
                        return
                    if self._menu_is_option(spec, item):
                        self._menu_cycle_option(spec, item)
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
            hit_radius = node.radius * (1.45 + 0.20 * node.visible_amount)
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
        if event.key() == Qt.Key_Escape:
            appearance_actions.save_state(self.appearance_state, self._appearance_state_path)
            system_actions.save_state(self.system_state, self._system_state_path)
            self._save_menu_state()
            self.close()
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
        self._save_menu_state()
        super().closeEvent(event)

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
        angle = (sector * (math.pi / 2.0)) - (math.pi / 2.0)
        return math.cos(angle), math.sin(angle)

    def _sector_from_cursor(self, dx: float, dy: float) -> tuple[int, float, float]:
        dist = math.hypot(dx, dy)
        if dist <= 1e-6:
            return -1, 0.0, 0.0

        mx = dx / dist
        my = dy / dist

        axes = [
            (0, 0.0, -1.0),  # Voice / top
            (1, 1.0, 0.0),   # System / right
            (2, 0.0, 1.0),   # Memory / bottom
            (3, -1.0, 0.0),  # Appearance / left
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
            "toggle": 7.5,
            "slider": 7.0,
            "meter": 6.8,
            "status": 6.6,
            "card": 6.5,
            "chips": 6.8,
            "pulse": 7.2,
            "swatches": 7.0,
            "palette": 7.0,
            "buttonless": 6.4,
        }
        return radii.get(item.kind, 6.8)

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
        if label == "Listen Mode":
            return "On" if st.listen_mode else "Off"
        return ""

    def _system_value(self, label: str) -> str:
        st = self.menu_state
        if label == "Startup":
            return "On" if st.startup else "Off"
        if label == "Overlay":
            return "On" if st.overlay else "Off"
        if label == "Always on Top":
            return "On" if st.always_on_top else "Off"
        if label == "Transparency":
            return f"{st.transparency}%"
        if label == "Shortcuts":
            return menu_state.SHORTCUT_PRESETS[st.shortcuts % len(menu_state.SHORTCUT_PRESETS)]
        if label == "Permissions":
            return "OK"
        if label == "Response Mode":
            return self.system_state.response_mode_label
        return ""

    def _memory_value(self, label: str) -> str:
        if label == "Clear Cache":
            return "Clear"
        if label == "Saved Threads":
            return "0"
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
        return ""

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
            ("System", "Shortcuts"),
        }

    def _menu_toggle_value(self, spec: MenuSpec, item: MenuItemSpec) -> bool:
        name, label = spec.name, item.label
        if name == "Voice" and label == "Mic Toggle":
            return self.menu_state.mic_enabled
        if name == "Voice" and label == "Listen Mode":
            return self.menu_state.listen_mode
        if name == "System" and label == "Startup":
            return self.menu_state.startup
        if name == "System" and label == "Overlay":
            return self.menu_state.overlay
        if name == "System" and label == "Always on Top":
            return self.menu_state.always_on_top
        return False

    def _menu_set_toggle(self, spec: MenuSpec, item: MenuItemSpec, value: bool) -> None:
        name, label = spec.name, item.label
        value = bool(value)
        if name == "Voice" and label == "Mic Toggle":
            self.menu_state.mic_enabled = value
            menu_state.LIVE.set_mic_enabled(value)
        elif name == "Voice" and label == "Listen Mode":
            self.menu_state.listen_mode = value
        elif name == "System" and label == "Startup":
            self.menu_state.startup = value
        elif name == "System" and label == "Overlay":
            self.menu_state.overlay = value
        elif name == "System" and label == "Always on Top":
            self.menu_state.always_on_top = value
            self._apply_always_on_top(value)
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
        elif name == "Voice" and label == "Speech Speed":
            self.menu_state.speech_speed = value
        elif name == "Voice" and label == "Hotword Sens.":
            self.menu_state.hotword_sensitivity = value
            menu_state.LIVE.set_hotword_sensitivity(value)
        elif name == "System" and label == "Transparency":
            self.menu_state.transparency = value
            self._apply_transparency(value)
        self._save_menu_state()

    def _menu_cycle_option(self, spec: MenuSpec, item: MenuItemSpec) -> None:
        name, label = spec.name, item.label
        if name == "Voice" and label == "Voice Select":
            self.menu_state.voice_select = (self.menu_state.voice_select + 1) % len(menu_state.VOICE_OPTIONS)
        elif name == "System" and label == "Shortcuts":
            self.menu_state.shortcuts = (self.menu_state.shortcuts + 1) % len(menu_state.SHORTCUT_PRESETS)
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
        """Émet un signal sonore de test court (bip système)."""
        try:
            QApplication.beep()
        except Exception:
            pass
        self.pulse = 1.0

    def _clear_cache(self) -> None:
        self._menu_toggle_state.clear()

    def _save_menu_state(self) -> None:
        menu_state.save_state(self.menu_state, self._menu_state_path)

    def _menu_callback_for(self, spec: MenuSpec, item: MenuItemSpec) -> Callable[[], None]:
        if spec.name == "Appearance":
            return self._appearance_callback_for(item)

        def _callback() -> None:
            name, label = spec.name, item.label
            if self._menu_is_slider(spec, item):
                self._menu_action_flash = f"{label}: {self._menu_slider_value(spec, item)}%"
            elif self._menu_is_option(spec, item):
                self._menu_cycle_option(spec, item)
                self._menu_action_flash = f"{label}: {self._menu_value(spec, item)}"
            elif item.kind == "toggle":
                current = self._menu_toggle_value(spec, item)
                self._menu_set_toggle(spec, item, not current)
                self._menu_action_flash = f"{label}: {'On' if not current else 'Off'}"
            elif name == "Voice" and label == "Audio Test":
                self._audio_test()
                self._menu_action_flash = f"{label} ▶"
            elif name == "System" and label == "Response Mode":
                system_actions.cycle_response_mode(self.system_state)
                system_actions.save_state(self.system_state, self._system_state_path)
                menu_state.LIVE.set_response_mode_index(self.system_state.response_mode_index)
                self._menu_action_flash = f"Response: {self.system_state.response_mode_label}"
            elif name == "Memory" and label == "Clear Cache":
                self._clear_cache()
                self._menu_action_flash = "Cache cleared"
            elif name == "Memory":
                self._menu_action_flash = item.label
            else:
                self._menu_action_flash = item.label
            self._menu_action_flash_time = self.time

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
            self._menu_action_flash = f"{item.label}: {self._appearance_value(item.label)}"
            self._menu_action_flash_time = self.time
        return _callback

    def _apply_appearance_state(self) -> None:
        state = self.appearance_state
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
        if len(self._menu_nodes) != len(spec.items) or any(node.section != spec.name for node in self._menu_nodes):
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
        spec = self._menu_spec()
        if spec is None or self._menu_alpha <= 0.01:
            for node in self._menu_nodes:
                node.visible_amount = lerp(node.visible_amount, 0.0, 0.12)
                node.hover_amount = lerp(node.hover_amount, 0.0, 0.12)
                node.click_amount = lerp(node.click_amount, 0.0, 0.16)
            if all(node.visible_amount < 0.02 for node in self._menu_nodes):
                self._menu_nodes.clear()
            self._menu_hot_node = -1
            return

        self._sync_menu_nodes(spec)
        reveal = clamp(self._menu_reveal, 0.0, 1.25)
        alpha = clamp(self._menu_alpha, 0.0, 1.0)
        anchor, sx, sy, perp_x, perp_y = self._menu_anchor(spec, reveal)
        count = len(self._menu_nodes)
        if count == 0:
            return

        self._menu_layout_mode = "grid" if self._menu_sector in (0, 2) else "line"

        hover_index = -1
        hover_score = -1.0
        count_mid = (count - 1) * 0.5
        spacing = 18.0 + 2.0 * spec.branch_bias
        if self._menu_sector in (1, 3):
            spacing += 14.0
        curve_strength = 7.0 + 5.0 * reveal

        for index, node in enumerate(self._menu_nodes):
            if self._menu_layout_mode == "grid":
                columns = 2
                rows = max(1, math.ceil(count / columns))
                col = index % columns
                row = index // columns
                col_offset = (col - 0.5) * 126.0
                row_offset = (row - (rows - 1) * 0.5) * 42.0
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
            hit_radius = node.radius * (1.45 + 0.18 * node.visible_amount)
            hover_target = clamp(1.0 - (dist / max(hit_radius, 1.0)), 0.0, 1.0)
            node.hover_amount = lerp(node.hover_amount, hover_target, 0.18)

            if node.hover_amount > hover_score:
                hover_score = node.hover_amount
                hover_index = index

        self._menu_hot_node = hover_index

        if self._menu_action_flash_time >= 0.0:
            if (self.time - self._menu_action_flash_time) > 0.7:
                self._menu_action_flash = ""
                self._menu_action_flash_time = -1.0

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
        text_offset = 13.0 + 9.0 * hover + 3.0 * click
        label_dx = 22.0 + 4.0 * hover + 2.0 * click
        sector_index = self._menu_sector
        if self._menu_layout_mode == "grid":
            if sector_index == 2:  # bottom
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
        else:
            if sector_index == 3:  # left
                label_pos = QPointF(node.position.x() - label_dx, node.position.y())
                label_rect = QRectF(label_pos.x() - 130.0, label_pos.y() - 9.0, 120.0, 18.0)
                label_alignment = Qt.AlignRight | Qt.AlignVCenter
            elif sector_index == 1:  # right
                label_pos = QPointF(node.position.x() + label_dx, node.position.y())
                label_rect = QRectF(label_pos.x(), label_pos.y() - 9.0, 140.0, 18.0)
                label_alignment = Qt.AlignLeft | Qt.AlignVCenter
            elif sector_index == 2:  # bottom
                label_pos = QPointF(node.position.x() + label_dx, node.position.y())
                label_rect = QRectF(label_pos.x(), label_pos.y() - 9.0, 160.0, 18.0)
                label_alignment = Qt.AlignLeft | Qt.AlignVCenter
            else:  # top
                label_pos = QPointF(node.position.x() + label_dx, node.position.y())
                label_rect = QRectF(label_pos.x(), label_pos.y() - 9.0, 150.0, 18.0)
                label_alignment = Qt.AlignLeft | Qt.AlignVCenter
        painter.setPen(QColor(220, 245, 255, text_alpha))
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

    def _draw_glow(self, painter: QPainter, path: QPainterPath):

        state = self.appearance_state
        halo = clamp(self._halo_energy * state.glow_intensity, 0.0, 1.85)
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
        halo = clamp(self._halo_energy * state.glow_intensity, 0.0, 1.85)
        voice = clamp(voice_energy, 0.0, 1.0)

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

        core_radius = self.base_radius * (0.22 + 0.04 * voice) + 8 * self.pulse + 6.0 * halo

        core = QPainterPath()
        core.addEllipse(self.current_center, core_radius, core_radius)

        painter.setBrush(QColor(state.glow_color.red(), state.glow_color.green(), state.glow_color.blue(), int(150 + 55 * halo + 30 * voice)))
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
        finally:
            painter.end()


def main() -> int:
    app = QApplication(sys.argv)
    window = MorphingOrbWidget()
    window.showFullScreen()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
