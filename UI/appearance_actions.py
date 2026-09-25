from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

from PySide6.QtGui import QColor


#: Opacité par défaut (0..1) des fonds d'items des menus radiaux, telle que
#: définie en 1.3.1 (constante ``ITEM_BG_REST``). Depuis la 1.3.2 c'est la
#: VALEUR PAR DÉFAUT d'un réglage utilisateur (Appearance → « Item BG
#: Opacity »), pas une constante figée.
ITEM_BG_REST = 0.55


def _clamp01(value: float, default: float = ITEM_BG_REST) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number:  # NaN
        return default
    return max(0.0, min(1.0, number))


@dataclass
class AppearanceState:
    theme_name: str = "blue"
    glow_color: QColor = field(default_factory=lambda: QColor(70, 180, 255))
    bg_color: QColor = field(default_factory=lambda: QColor(0, 0, 0, 0))
    text_color: QColor = field(default_factory=lambda: QColor(210, 235, 255))
    glow_intensity: float = 1.0
    blob_scale: float = 1.0
    time_scale: float = 1.25
    minimal_mode: bool = False
    cinematic_mode: bool = False
    #: Opacité des fonds des items des menus (0 = fond uniquement au survol,
    #: 1 = fond pleinement opaque). Ne touche ni au texte ni au blob.
    item_bg_opacity: float = ITEM_BG_REST
    #: Orbe masqué depuis le menu (Appearance → « Blob Visible »). L'assistant
    #: vocal continue de tourner : seule la fenêtre de l'orbe est cachée.
    #: État de SESSION uniquement (1.3.2) : il n'est jamais relu ni réécrit
    #: dans ``appearance_state.json`` — voir ``state_to_dict``.
    blob_hidden: bool = False


THEME_ORDER = ["white", "yellow", "red", "purple", "pink", "green", "blue"]
THEME_COLORS = {
    "white": (QColor(225, 245, 255), QColor(240, 248, 255)),
    "yellow": (QColor(255, 231, 143), QColor(255, 247, 220)),
    "red": (QColor(255, 120, 120), QColor(255, 225, 225)),
    "purple": (QColor(189, 145, 255), QColor(239, 228, 255)),
    "pink": (QColor(255, 155, 214), QColor(255, 232, 245)),
    "green": (QColor(136, 232, 186), QColor(226, 255, 240)),
    "blue": (QColor(70, 180, 255), QColor(210, 235, 255)),
}


def _apply_theme(state: AppearanceState, theme_name: str) -> None:
    glow_color, text_color = THEME_COLORS[theme_name]
    state.theme_name = theme_name
    state.glow_color = QColor(glow_color)
    state.text_color = QColor(text_color)
    state.bg_color = QColor(0, 0, 0, 0)


def state_to_dict(state: AppearanceState) -> dict:
    # 1.3.2 : ``blob_hidden`` n'est PAS sérialisé. Le masquage est un état
    # temporaire de la session en cours ; relancer Jarvis doit toujours
    # démarrer l'orbe visible, quel que soit le chemin de fermeture (Échap,
    # menu System → Quit, icône de notification…), puisque TOUS les chemins
    # passent par ``save_state``.
    return {
        "theme_name": state.theme_name,
        "glow_intensity": state.glow_intensity,
        "blob_scale": state.blob_scale,
        "time_scale": state.time_scale,
        "minimal_mode": state.minimal_mode,
        "cinematic_mode": state.cinematic_mode,
        "item_bg_opacity": _clamp01(state.item_bg_opacity),
    }


def apply_state_dict(state: AppearanceState, payload: dict) -> None:
    theme_name = payload.get("theme_name", state.theme_name)
    if theme_name not in THEME_COLORS:
        theme_name = "blue"
    _apply_theme(state, theme_name)
    state.glow_intensity = float(payload.get("glow_intensity", state.glow_intensity))
    state.blob_scale = float(payload.get("blob_scale", state.blob_scale))
    state.time_scale = float(payload.get("time_scale", state.time_scale))
    state.minimal_mode = bool(payload.get("minimal_mode", state.minimal_mode))
    state.cinematic_mode = bool(payload.get("cinematic_mode", state.cinematic_mode))
    # 1.3.2 : l'opacité des fonds d'items est persistée avec les autres
    # réglages Appearance ; valeur absente (fichier d'avant 1.3.2) → défaut
    # identique au comportement 1.3.1.
    state.item_bg_opacity = _clamp01(payload.get("item_bg_opacity", state.item_bg_opacity))
    # 1.3.2 : l'état « masqué » est volontairement IGNORÉ au chargement,
    # même s'il figure encore dans un fichier écrit par la 1.2.0–1.3.1 :
    # masqué → fermeture → relancement doit démarrer VISIBLE. Le réglage
    # reste bien manipulable pendant la session (set_blob_hidden).
    state.blob_hidden = False
    if state.minimal_mode:
        state.glow_intensity = min(state.glow_intensity, 0.85)
    if state.cinematic_mode:
        state.time_scale = min(state.time_scale, 1.05)


def load_state(path: str) -> AppearanceState:
    state = AppearanceState()
    if not path or not os.path.exists(path):
        return state
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            apply_state_dict(state, payload)
    except Exception:
        pass
    return state


def save_state(state: AppearanceState, path: str) -> None:
    if not path:
        return
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state_to_dict(state), handle, indent=2)
    except Exception:
        pass


def _print_action(name: str) -> None:
    print(f"[appearance] {name}")


def set_theme_blue(state: AppearanceState) -> None:
    _apply_theme(state, "blue")
    _print_action("theme=blue")


def set_theme_white(state: AppearanceState) -> None:
    _apply_theme(state, "white")
    _print_action("theme=white")


def cycle_theme(state: AppearanceState) -> None:
    current_index = THEME_ORDER.index(state.theme_name) if state.theme_name in THEME_ORDER else 0
    next_theme = THEME_ORDER[(current_index + 1) % len(THEME_ORDER)]
    _apply_theme(state, next_theme)
    _print_action(f"theme={next_theme}")


def toggle_minimal_mode(state: AppearanceState) -> None:
    state.minimal_mode = not state.minimal_mode
    if state.minimal_mode:
        state.glow_intensity = min(state.glow_intensity, 0.85)
    _print_action(f"minimal_mode={state.minimal_mode}")


def increase_glow(state: AppearanceState) -> None:
    state.glow_intensity = min(2.0, state.glow_intensity + 0.12)
    _print_action(f"glow={state.glow_intensity:.2f}")


def decrease_glow(state: AppearanceState) -> None:
    state.glow_intensity = max(0.35, state.glow_intensity - 0.12)
    _print_action(f"glow={state.glow_intensity:.2f}")


def increase_blob_size(state: AppearanceState) -> None:
    state.blob_scale = min(1.45, state.blob_scale + 0.04)
    _print_action(f"blob_scale={state.blob_scale:.2f}")


def decrease_blob_size(state: AppearanceState) -> None:
    state.blob_scale = max(0.72, state.blob_scale - 0.04)
    _print_action(f"blob_scale={state.blob_scale:.2f}")


def set_item_bg_opacity(state: AppearanceState, value: float) -> None:
    """Règle l'opacité des fonds d'items des menus (0.0 à 1.0).

    Ne touche qu'aux fonds : le texte, les pastilles, le liseré et le blob
    gardent leur propre opacité (pilotée par le survol et le rendu).
    """
    state.item_bg_opacity = _clamp01(value)
    _print_action(f"item_bg_opacity={state.item_bg_opacity:.2f}")


def toggle_cinematic_mode(state: AppearanceState) -> None:
    state.cinematic_mode = not state.cinematic_mode
    if state.cinematic_mode:
        state.time_scale = 1.05
        state.glow_intensity = min(2.0, state.glow_intensity + 0.20)
    else:
        state.time_scale = 1.25
    _print_action(f"cinematic_mode={state.cinematic_mode}")


#: Message affiché quand l'orbe disparaît : sans fenêtre visible, c'est la
#: seule trace du chemin de retour.
BLOB_RESTORE_HINT = (
    "Icône de notification → « Afficher Jarvis » pour réafficher l'orbe."
)


def set_blob_hidden(state: AppearanceState, hidden: bool) -> None:
    """Masque (``True``) ou réaffiche (``False``) l'orbe.

    Ne touche qu'à l'affichage : l'assistant vocal (wake word, Gemini Live,
    routines, rappels) tourne dans son propre thread et n'est jamais arrêté.
    """
    state.blob_hidden = bool(hidden)
    _print_action(f"blob_hidden={state.blob_hidden}")


def toggle_blob_visibility(state: AppearanceState) -> None:
    """Bascule l'affichage de l'orbe (menu Appearance → « Blob Visible »)."""
    set_blob_hidden(state, not state.blob_hidden)
