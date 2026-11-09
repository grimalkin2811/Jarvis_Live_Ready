"""Protocole JSON entre Jarvis et le module web God's Eye View.

Ce module est **pur Python** (aucun import Qt, aucun réseau) : il définit le
contrat d'actions `world_*` partagé par le pont hôte (PySide6 /
QWebEngineView) et le module web (`web/gods_eye_view/src/bridge.js`).

Les limites et la table des régions sont volontairement dupliquées côté JS
(défense en profondeur : les deux côtés valident). Un test de parité
(tests/test_gods_eye_protocol.py) vérifie que les deux tables correspondent.
"""

from __future__ import annotations

import json
import re
import secrets
from typing import Any

PROTOCOL_VERSION = "0.1.0"

ACTION_SET_VIEW = "world_set_view"
ACTION_SET_CAMERA = "world_set_camera"
ACTION_FOCUS_REGION = "world_focus_region"
ACTION_ENABLE_LAYER = "world_enable_layer"
ACTION_DISABLE_LAYER = "world_disable_layer"
ACTION_GET_STATE = "world_get_state"

ACTIONS = (
    ACTION_SET_VIEW,
    ACTION_SET_CAMERA,
    ACTION_FOCUS_REGION,
    ACTION_ENABLE_LAYER,
    ACTION_DISABLE_LAYER,
    ACTION_GET_STATE,
)

# Seules couches autorisées dans la v1 (whitelist fermée).
LAYER_IDS = ("flights",)

# Vue par défaut (unique en v1).
VIEW_GLOBE = "globe"
VIEWS = (VIEW_GLOBE,)

# Bornes identiques à web/gods_eye_view/src/bridge.js (CAMERA_LIMITS).
ALTITUDE_MIN_M = 1_000.0
ALTITUDE_MAX_M = 40_000_000.0
DURATION_MIN_S = 0.0
DURATION_MAX_S = 10.0
HEADING_MIN_DEG = -360.0
HEADING_MAX_DEG = 360.0
PITCH_MIN_DEG = -89.9
PITCH_MAX_DEG = 89.9

DEFAULT_CAMERA = {
    "altitude_m": 2_000_000.0,
    "heading_deg": 0.0,
    "pitch_deg": -35.0,
    "duration_s": 2.5,
}

# Table fermée des régions nommées — seules destinations de world_focus_region
# sans coordonnées explicites. Doit rester alignée sur web/.../regions.js.
FOCUS_REGIONS: dict[str, dict[str, Any]] = {
    "paris":        {"label": "Paris",         "latitude": 48.8566,  "longitude": 2.3522,    "altitude_m": 150_000.0},
    "londres":      {"label": "Londres",       "latitude": 51.5072,  "longitude": -0.1276,   "altitude_m": 150_000.0},
    "berlin":       {"label": "Berlin",        "latitude": 52.52,    "longitude": 13.405,    "altitude_m": 150_000.0},
    "madrid":       {"label": "Madrid",        "latitude": 40.4168,  "longitude": -3.7038,   "altitude_m": 150_000.0},
    "rome":         {"label": "Rome",          "latitude": 41.9028,  "longitude": 12.4964,   "altitude_m": 150_000.0},
    "amsterdam":    {"label": "Amsterdam",     "latitude": 52.3676,  "longitude": 4.9041,    "altitude_m": 150_000.0},
    "bruxelles":    {"label": "Bruxelles",     "latitude": 50.8503,  "longitude": 4.3517,    "altitude_m": 150_000.0},
    "geneve":       {"label": "Genève",        "latitude": 46.2044,  "longitude": 6.1432,    "altitude_m": 150_000.0},
    "lyon":         {"label": "Lyon",          "latitude": 45.764,   "longitude": 4.8357,    "altitude_m": 120_000.0},
    "marseille":    {"label": "Marseille",     "latitude": 43.2965,  "longitude": 5.3698,    "altitude_m": 120_000.0},
    "toulouse":     {"label": "Toulouse",      "latitude": 43.6047,  "longitude": 1.4442,    "altitude_m": 120_000.0},
    "strasbourg":   {"label": "Strasbourg",    "latitude": 48.5734,  "longitude": 7.7521,    "altitude_m": 120_000.0},
    "nice":         {"label": "Nice",          "latitude": 43.7102,  "longitude": 7.262,     "altitude_m": 120_000.0},
    "nantes":       {"label": "Nantes",        "latitude": 47.2184,  "longitude": -1.5536,   "altitude_m": 120_000.0},
    "lille":        {"label": "Lille",         "latitude": 50.6292,  "longitude": 3.0573,    "altitude_m": 120_000.0},
    "singapour":    {"label": "Singapour",     "latitude": 1.3521,   "longitude": 103.8198,  "altitude_m": 150_000.0},
    "tokyo":        {"label": "Tokyo",         "latitude": 35.6762,  "longitude": 139.6503,  "altitude_m": 150_000.0},
    "istanbul":     {"label": "Istanbul",      "latitude": 41.0082,  "longitude": 28.9784,   "altitude_m": 150_000.0},
    "dubai":        {"label": "Dubaï",         "latitude": 25.2048,  "longitude": 55.2708,   "altitude_m": 150_000.0},
    "newyork":      {"label": "New York",      "latitude": 40.7128,  "longitude": -74.006,   "altitude_m": 150_000.0},
    "montreal":     {"label": "Montréal",      "latitude": 45.5019,  "longitude": -73.5674,  "altitude_m": 150_000.0},
    "sanfrancisco": {"label": "San Francisco", "latitude": 37.7749,  "longitude": -122.4194, "altitude_m": 150_000.0},
    "losangeles":   {"label": "Los Angeles",   "latitude": 34.0522,  "longitude": -118.2437, "altitude_m": 150_000.0},
    "sydney":       {"label": "Sydney",        "latitude": -33.8688, "longitude": 151.2093,  "altitude_m": 150_000.0},
}

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


class ProtocolError(ValueError):
    """Action ou argument invalide côté protocole."""


def clamp(value: Any, minimum: float, maximum: float, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if number != number:  # NaN
        return float(default)
    return float(max(minimum, min(maximum, number)))


def new_request_id() -> str:
    return secrets.token_hex(8)


def _require_str(value: Any, name: str) -> str:
    text = str(value).strip() if value is not None else ""
    if not text:
        raise ProtocolError(f"{name} manquant")
    return text


def validate_action(action: Any) -> str:
    name = _require_str(action, "action")
    if name not in ACTIONS:
        raise ProtocolError(
            f"action inconnue: {name!r} — actions autorisées: {', '.join(ACTIONS)}"
        )
    return name


def validate_layer_id(layer_id: Any) -> str:
    name = _require_str(layer_id, "layer_id").lower()
    if name not in LAYER_IDS:
        raise ProtocolError(
            f"couche non autorisée: {name!r} — v1: {', '.join(LAYER_IDS)}"
        )
    return name


def validate_view(view: Any) -> str:
    name = str(view if view is not None else VIEW_GLOBE).strip().lower() or VIEW_GLOBE
    if name not in VIEWS:
        raise ProtocolError(f"vue inconnue: {name!r} — v1: {', '.join(VIEWS)}")
    return name


def validate_region(region: Any) -> str:
    name = _require_str(region, "region").lower()
    if name not in FOCUS_REGIONS:
        raise ProtocolError(
            f"région inconnue: {name!r} — attendu: {', '.join(sorted(FOCUS_REGIONS))}"
        )
    return name


def normalize_camera_args(args: dict[str, Any] | None) -> dict[str, float]:
    raw = dict(args or {})
    try:
        latitude = float(raw.get("latitude"))
        longitude = float(raw.get("longitude"))
    except (TypeError, ValueError):
        raise ProtocolError("latitude/longitude numériques requis") from None
    if not -90.0 <= latitude <= 90.0:
        raise ProtocolError(f"latitude hors bornes (-90..90): {latitude}")
    if not -180.0 <= longitude <= 180.0:
        raise ProtocolError(f"longitude hors bornes (-180..180): {longitude}")
    return {
        "latitude": latitude,
        "longitude": longitude,
        "altitude_m": clamp(raw.get("altitude_m"), ALTITUDE_MIN_M, ALTITUDE_MAX_M, DEFAULT_CAMERA["altitude_m"]),
        "heading_deg": clamp(raw.get("heading_deg"), HEADING_MIN_DEG, HEADING_MAX_DEG, DEFAULT_CAMERA["heading_deg"]),
        "pitch_deg": clamp(raw.get("pitch_deg"), PITCH_MIN_DEG, PITCH_MAX_DEG, DEFAULT_CAMERA["pitch_deg"]),
        "duration_s": clamp(raw.get("duration_s"), DURATION_MIN_S, DURATION_MAX_S, DEFAULT_CAMERA["duration_s"]),
    }


def normalize_args(action: str, args: dict[str, Any] | None) -> dict[str, Any]:
    raw = dict(args or {})
    if action in (ACTION_ENABLE_LAYER, ACTION_DISABLE_LAYER):
        return {"layer_id": validate_layer_id(raw.get("layer_id"))}
    if action == ACTION_SET_VIEW:
        return {"view": validate_view(raw.get("view"))}
    if action == ACTION_SET_CAMERA:
        return normalize_camera_args(raw)
    if action == ACTION_FOCUS_REGION:
        normalized: dict[str, Any] = {"region": validate_region(raw.get("region"))}
        if raw.get("altitude_m") is not None:
            normalized["altitude_m"] = clamp(
                raw["altitude_m"], ALTITUDE_MIN_M, ALTITUDE_MAX_M, FOCUS_REGIONS[normalized["region"]]["altitude_m"]
            )
        return normalized
    if action == ACTION_GET_STATE:
        return {}
    raise ProtocolError(f"action inconnue: {action!r}")


def build_request(action: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
    name = validate_action(action)
    return {
        "id": new_request_id(),
        "action": name,
        "args": normalize_args(name, args),
        "protocol": PROTOCOL_VERSION,
    }


def dumps_request(request: dict[str, Any]) -> str:
    return json.dumps(request, ensure_ascii=True, separators=(",", ":"))


def parse_response(text: Any) -> dict[str, Any]:
    if isinstance(text, dict):
        response = text
    elif isinstance(text, str):
        try:
            response = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"réponse JSON invalide: {exc}") from None
    else:
        raise ProtocolError(f"type de réponse inattendu: {type(text).__name__}")
    if not isinstance(response, dict):
        raise ProtocolError("la réponse doit être un objet JSON")
    request_id = response.get("id")
    if request_id is not None and not isinstance(request_id, str):
        raise ProtocolError("id de réponse invalide")
    if "ok" not in response or not isinstance(response["ok"], bool):
        raise ProtocolError("la réponse doit porter un booléen 'ok'")
    return response


def parse_request(text: Any) -> dict[str, Any]:
    """Validation symétrique (utilisée par les tests du côté module web)."""
    request = parse_response(text) if isinstance(text, str) else dict(text or {})
    if not isinstance(request.get("action"), str):
        raise ProtocolError("action requise")
    validate_action(request["action"])
    if request.get("id") is not None and not _ID_RE.match(str(request["id"])):
        raise ProtocolError("id invalide")
    request["args"] = normalize_args(request["action"], request.get("args"))
    return request
