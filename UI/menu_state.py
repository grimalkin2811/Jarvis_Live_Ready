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
#: Correspondance entre les noms conviviaux du menu et les voix prébuilt
#: Gemini Live. Modifier ce mapping suffit pour proposer d'autres voix.
GEMINI_VOICE_NAMES = {
    "Jarvis": "Charon",
    "Aria": "Aoede",
    "Orion": "Orus",
    "Nova": "Kore",
    "Atlas": "Fenrir",
    "Luna": "Leda",
}


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
    #: Interruption vocale : dire « stop » coupe la réponse en cours.
    barge_in: bool = True

    # System
    startup: bool = False
    startup_managed: bool = False
    overlay: bool = False
    always_on_top: bool = False
    transparency: int = 100

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


_BOOL_FIELDS = {"mic_enabled", "startup", "startup_managed", "overlay", "always_on_top", "listen_mode", "barge_in"}


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
    # Migration : l'ancien toggle « Startup » n'avait aucun effet réel. Sans
    # action explicite de l'utilisateur (startup_managed), on repart de
    # l'état honnête « désactivé » plutôt que d'afficher un mensonge.
    if "startup_managed" not in payload:
        state.startup = False
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
        self.tts_volume = 70
        self.speech_speed = 50
        self.listen_mode = False
        self.barge_in = True
        self.voice_name = GEMINI_VOICE_NAMES[VOICE_OPTIONS[0]]
        self._voice_version = 0
        #: Poignée fournie par le backend vocal pour couper la réponse en
        #: cours (bouton « Stop Speaking » / raccourci clavier).
        self._stop_speaking_handler = None

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

    # Volume de la voix (appliqué en temps réel sur la sortie audio) ----
    def get_tts_volume(self) -> int:
        with self._lock:
            return self.tts_volume

    def set_tts_volume(self, value: int) -> None:
        with self._lock:
            self.tts_volume = _clamp_int(value, 0, 100)

    # Débit de parole (injecté dans le prompt système à la connexion) ---
    def get_speech_speed(self) -> int:
        with self._lock:
            return self.speech_speed

    def set_speech_speed(self, value: int) -> None:
        with self._lock:
            self.speech_speed = _clamp_int(value, 0, 100)

    def get_speech_pace(self) -> str:
        """Consigne de débit : "posé", "normal" ou "vif"."""
        with self._lock:
            speed = self.speech_speed
        if speed < 35:
            return "posé"
        if speed > 65:
            return "vif"
        return "normal"

    # Écoute continue (pas besoin de « Hey Jarvis ») --------------------
    def get_listen_mode(self) -> bool:
        with self._lock:
            return self.listen_mode

    def set_listen_mode(self, value: bool) -> None:
        with self._lock:
            self.listen_mode = bool(value)

    # Interruption vocale (« stop » coupe la réponse en cours) -----------
    def get_barge_in(self) -> bool:
        with self._lock:
            return self.barge_in

    def set_barge_in(self, value: bool) -> None:
        with self._lock:
            self.barge_in = bool(value)

    # Arrêt immédiat de la réponse (bouton / raccourci) ------------------
    def set_stop_speaking_handler(self, handler) -> None:
        """Enregistre (ou retire avec ``None``) la fonction qui coupe la
        réponse en cours. Elle est fournie par le backend vocal."""
        with self._lock:
            self._stop_speaking_handler = handler

    def request_stop_speaking(self) -> bool:
        """Coupe la réponse en cours. Renvoie False si aucun backend vocal
        n'est branché (UI lancée seule, par exemple)."""
        with self._lock:
            handler = self._stop_speaking_handler
        if handler is None:
            return False
        try:
            handler()
        except Exception:
            return False
        return True

    # Voix Gemini Live ---------------------------------------------------
    def get_voice_name(self) -> str:
        with self._lock:
            return self.voice_name

    def get_voice_version(self) -> int:
        """Incrémenté à chaque changement de voix : permet au backend de
        détecter qu'il doit rouvrir la session pour appliquer la nouvelle
        voix (Gemini Live ne permet pas de changer de voix en cours de
        session)."""
        with self._lock:
            return self._voice_version

    def set_voice_index(self, index: int) -> None:
        with self._lock:
            try:
                idx = int(index) % len(VOICE_OPTIONS)
            except (TypeError, ValueError):
                return
            name = GEMINI_VOICE_NAMES.get(VOICE_OPTIONS[idx], "")
            if not name:
                return
            if name != self.voice_name:
                self.voice_name = name
                self._voice_version += 1

    def set_voice_name(self, name: str) -> None:
        """Force une voix par son nom convivial (sans bump de version :
        utilisé au chargement initial pour synchroniser le pont)."""
        with self._lock:
            mapped = GEMINI_VOICE_NAMES.get(str(name), "")
            if mapped:
                self.voice_name = mapped

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
    LIVE.set_tts_volume(state.tts_volume)
    LIVE.set_speech_speed(state.speech_speed)
    LIVE.set_listen_mode(state.listen_mode)
    LIVE.set_barge_in(state.barge_in)
    LIVE.set_voice_name(VOICE_OPTIONS[state.voice_select % len(VOICE_OPTIONS)])
