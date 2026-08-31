"""État du menu radial de Jarvis.

Ce module centralise :

* les réglages interactifs du menu (toggles, sliders, options) qui doivent
  survivre au redémarrage via ``UI/menu_state.json`` ;
* un pont ``LIVE`` thread-safe que le backend audio (``src/audio.py``) et
  Gemini (``src/gemini_live.py``) consultent pour appliquer en temps réel les
  réglages de la voix (micro coupé, sensibilité du wake word) et le mode de
  réponse.

Il reste volontairement découplé de Qt afin de pouvoir être importé depuis
les threads du backend sans dépendance à PySide6.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, fields, asdict

# ---------------------------------------------------------------------------
# Options cyclables (menus de type "chips")
# ---------------------------------------------------------------------------

VOICE_OPTIONS = ["Jarvis", "Aria", "Orion", "Nova", "Atlas", "Luna"]
SHORTCUT_PRESETS = ["Default", "Compact", "Power"]


# ---------------------------------------------------------------------------
# État persistant
# ---------------------------------------------------------------------------

@dataclass
class MenuState:
    """Réglages interactifs du menu radial (persistés dans menu_state.json)."""

    # Voice
    tts_volume: int = 70
    voice_select: int = 0
    speech_speed: int = 50
    mic_enabled: bool = True
    hotword_sensitivity: int = 50
    listen_mode: bool = False

    # System
    startup: bool = True
    overlay: bool = False
    always_on_top: bool = False
    transparency: int = 100
    shortcuts: int = 0

    def __post_init__(self) -> None:
        for f in fields(self):
            value = getattr(self, f.name)
            if "volume" in f.name or "transparency" in f.name:
                setattr(self, f.name, _clamp_int(value, 0, 100))
            elif "sensitivity" in f.name or "speed" in f.name:
                setattr(self, f.name, _clamp_int(value, 0, 100))


def _clamp_int(value: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return low


def _clamp_float(value: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return low


_BOOL_FIELDS = {"mic_enabled", "startup", "overlay", "always_on_top", "listen_mode"}


def _apply_payload(state: MenuState, payload: dict) -> None:
    for f in fields(state):
        if f.name in payload:
            value = payload[f.name]
            if f.name in _BOOL_FIELDS:
                setattr(state, f.name, bool(value))
            else:
                try:
                    setattr(state, f.name, int(value))
                except (TypeError, ValueError):
                    pass
    state.__post_init__()


def state_to_dict(state: MenuState) -> dict:
    return asdict(state)


def load_state(path: str) -> MenuState:
    state = MenuState()
    if not path or not os.path.exists(path):
        return state
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            _apply_payload(state, payload)
    except Exception:
        pass
    _sync_live(state)
    return state


def save_state(state: MenuState, path: str) -> None:
    _sync_live(state)
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


# ---------------------------------------------------------------------------
# Pont live thread-safe (appliqué en temps réel par le backend)
# ---------------------------------------------------------------------------

class LiveControls:
    """Réglages lus par le backend (audio / Gemini) depuis n'importe quel thread."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self.mic_enabled = True
        self.hotword_sensitivity = 50
        self.response_mode_index = 1

    # Mic ---------------------------------------------------------------
    def get_mic_enabled(self) -> bool:
        with self._lock:
            return self.mic_enabled

    def set_mic_enabled(self, value: bool) -> None:
        with self._lock:
            self.mic_enabled = bool(value)

    # Wake word sensitivity ---------------------------------------------
    def get_hotword_sensitivity(self) -> int:
        with self._lock:
            return self.hotword_sensitivity

    def get_wake_threshold(self) -> float:
        with self._lock:
            sens = self.hotword_sensitivity
        # Sensibilité 0 -> seuil haut (difficile) ; 100 -> seuil bas (facile).
        return _clamp_float(0.92 - (sens / 100.0) * 0.62, 0.30, 0.92)

    def set_hotword_sensitivity(self, value: int) -> None:
        with self._lock:
            self.hotword_sensitivity = _clamp_int(value, 0, 100)

    # Response mode ------------------------------------------------------
    def get_response_mode_index(self) -> int:
        with self._lock:
            return self.response_mode_index

    def set_response_mode_index(self, value: int) -> None:
        with self._lock:
            self.response_mode_index = int(value)


LIVE = LiveControls()

RESPONSE_MODE_LABELS = ["Concis", "Équilibré", "Détaillé"]


def response_mode_label_from_live() -> str:
    """Libellé du mode de réponse courant, lu via le pont thread-safe."""
    idx = LIVE.get_response_mode_index() % len(RESPONSE_MODE_LABELS)
    return RESPONSE_MODE_LABELS[idx]


def _sync_live(state: MenuState) -> None:
    """Pousse l'état persistant vers le pont live."""
    LIVE.set_mic_enabled(state.mic_enabled)
    LIVE.set_hotword_sensitivity(state.hotword_sensitivity)
