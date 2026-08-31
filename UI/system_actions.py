from __future__ import annotations

import json
import os
from dataclasses import dataclass

# Modes de réponse de Jarvis (verbosité). Adapté à Gemini Live :
# la valeur sélectionnée est injectée dans le prompt système au moment
# de la connexion (voir src/gemini_live.py).
RESPONSE_MODES = [
    {"label": "Concis", "description": "Réponses courtes et directes."},
    {"label": "Équilibré", "description": "Réponses naturelles et détaillées."},
    {"label": "Détaillé", "description": "Explications complètes et pédagogiques."},
]


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
