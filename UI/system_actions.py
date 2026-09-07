from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

# Modes de réponse de Jarvis (verbosité). Adapté à Gemini Live :
# la valeur sélectionnée est injectée dans le prompt système au moment
# de la connexion (voir src/gemini_live.py).
RESPONSE_MODES = [
    {"label": "Concis", "description": "Réponses courtes et directes."},
    {"label": "Équilibré", "description": "Réponses naturelles et détaillées."},
    {"label": "Détaillé", "description": "Explications complètes et pédagogiques."},
]


def _startup_launcher_path() -> Path:
    """Chemin du lanceur Jarvis dans le dossier de démarrage Windows."""
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return Path()
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Jarvis.bat"


def startup_status() -> bool:
    """Vrai si Jarvis est réellement configuré pour démarrer avec Windows."""
    try:
        return _startup_launcher_path().is_file()
    except Exception:
        return False


def set_startup(enabled: bool) -> dict:
    """Active/désactive le lancement de Jarvis au démarrage de Windows.

    Crée ou supprime un petit .bat dans le dossier ``Startup`` du profil.
    Sur les autres systèmes, l'action échoue proprement : l'UI affiche alors
    un message honnête au lieu de prétendre que le réglage a été appliqué.
    """
    try:
        launcher = _startup_launcher_path()
        if os.name != "nt" or not str(launcher):
            return {
                "success": False,
                "error": "Lancement automatique disponible uniquement sur Windows",
            }
        jarvis_bat = Path(__file__).resolve().parents[1] / "Jarvis.bat"
        if not jarvis_bat.is_file():
            return {"success": False, "error": "Jarvis.bat introuvable"}
        if enabled:
            content = (
                "@echo off\r\n"
                f'start "" "{jarvis_bat}" --ui\r\n'
            )
            launcher.parent.mkdir(parents=True, exist_ok=True)
            launcher.write_text(content, encoding="ascii")
        elif launcher.is_file():
            launcher.unlink()
        return {"success": True, "enabled": enabled}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@dataclass
class SystemState:
    response_mode_index: int = 1

    @property
    def response_mode_label(self) -> str:
        return RESPONSE_MODES[self.response_mode_index % len(RESPONSE_MODES)]["label"]

    @property
    def response_mode_description(self) -> str:
        return RESPONSE_MODES[self.response_mode_index % len(RESPONSE_MODES)]["description"]


def _state_payload(state: SystemState) -> dict:
    return {"response_mode_index": int(state.response_mode_index)}


def _apply_payload(state: SystemState, payload: dict) -> None:
    idx = int(payload.get("response_mode_index", state.response_mode_index))
    state.response_mode_index = idx % len(RESPONSE_MODES)


def load_state(path: str) -> SystemState:
    state = SystemState()
    if not path or not os.path.exists(path):
        return state
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            _apply_payload(state, payload)
    except Exception:
        pass
    return state


def save_state(state: SystemState, path: str) -> None:
    if not path:
        return
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(_state_payload(state), handle, indent=2)
    except Exception:
        pass


def cycle_response_mode(state: SystemState) -> None:
    state.response_mode_index = (state.response_mode_index + 1) % len(RESPONSE_MODES)
    print(f"[system] response_mode={state.response_mode_label}")


def response_mode_info(state: SystemState) -> dict:
    entry = RESPONSE_MODES[state.response_mode_index % len(RESPONSE_MODES)]
    return {"label": entry["label"], "description": entry["description"]}
