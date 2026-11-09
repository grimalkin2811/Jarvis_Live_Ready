"""Outils Gemini Live `world_*` — contrôle structuré du globe God's Eye View.

Conventions Jarvis :

* chaque outil renvoie un dictionnaire ``{"success": bool, ...}`` ;
* jamais d'exception vers l'appelant (Gemini annonce l'échec honnêtement) ;
* noms snake_case préfixés ``world_`` ; enums fermés ; bornes numériques ;
* aucune exécution de code arbitraire : seuls ces 6 outils existent ;
* le module s'ouvre à la demande (création Qt sur le thread UI — voir
  ``widget.py``) ; sans QApplication (tests headless), les outils répondent
  ``world_unavailable``.
"""

from __future__ import annotations

from typing import Any

from . import protocol
from .controller import get_default_controller

_STR = {"type": "string"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}

_REGION_ENUM = sorted(protocol.FOCUS_REGIONS)
_LAYER_ENUM = list(protocol.LAYER_IDS)

WORLD_TOOL_DECLARATIONS: list[dict[str, Any]] = [
    {
        "name": "world_set_view",
        "description": (
            "Remet le globe God's Eye View dans sa vue par défaut (globe entier, "
            "couches désactivées). À appeler quand l'utilisateur dit « montre la Terre entière » "
            "ou veut réinitialiser la vue. N'ouvre pas le module : il doit déjà être visible."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "view": {**_STR, "enum": list(protocol.VIEWS), "description": "Vue à appliquer (v1 : globe)."},
            },
            "required": [],
        },
    },
    {
        "name": "world_set_camera",
        "description": (
            "Déplace la caméra du globe vers un point précis (latitude/longitude, altitude en mètres). "
            "Ouvre le module s'il est fermé. À utiliser pour « montre-moi X » avec des coordonnées, "
            "ou après un calcul. Préfère world_focus_region pour les villes connues."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "latitude": {**_NUM, "description": "Latitude (-90..90)."},
                "longitude": {**_NUM, "description": "Longitude (-180..180)."},
                "altitude_m": {**_INT, "description": "Altitude caméra en mètres (1000..40000000), défaut 2000000."},
                "heading_deg": {**_NUM, "description": "Cap en degrés (défaut 0)."},
                "pitch_deg": {**_NUM, "description": "Inclinaison en degrés (défaut -35, plongée)."},
                "duration_s": {**_NUM, "description": "Durée du vol en secondes (0..10, défaut 2.5)."},
            },
            "required": ["latitude", "longitude"],
        },
    },
    {
        "name": "world_focus_region",
        "description": (
            "Vole vers une région nommée connue (villes françaises et capitales mondiales). "
            "Ouvre le module s'il est fermé. Exemple : « montre-moi les vols autour de Paris » "
            "→ world_enable_layer(flights) puis world_focus_region(paris)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "region": {**_STR, "enum": _REGION_ENUM, "description": "Région cible."},
                "altitude_m": {**_INT, "description": "Altitude caméra en mètres (défaut adapté à la région)."},
            },
            "required": ["region"],
        },
    },
    {
        "name": "world_enable_layer",
        "description": (
            "Active une couche OSINT du globe God's Eye View. Ouvre le module s'il est fermé. "
            "v1 : « flights » (vols en direct, adsb.lol). Activer une couche fermée ou déjà "
            "ouverte est sûr (idempotent)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer_id": {**_STR, "enum": _LAYER_ENUM, "description": "Identifiant de couche (v1 : flights)."},
            },
            "required": ["layer_id"],
        },
    },
    {
        "name": "world_disable_layer",
        "description": (
            "Désactive une couche du globe (destruction des objets, arrêt du polling). "
            "Sans effet si la couche est déjà coupée."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer_id": {**_STR, "enum": _LAYER_ENUM, "description": "Identifiant de couche (v1 : flights)."},
            },
            "required": ["layer_id"],
        },
    },
    {
        "name": "world_get_state",
        "description": (
            "État courant du globe God's Eye View : caméra, couche(s) actives et nombre d'objets, "
            "source des données, état du moteur de rendu. À appeler pour « qu'est-ce qui est affiché ? » "
            "ou avant de décider d'une action."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


def _ensure_module_open(timeout: float = 6.0):
    """Retourne le contrôleur prêt, ou None si l'ouverture est impossible."""
    controller = get_default_controller()
    if controller.lifecycle in ("ready", "loading"):
        return controller
    try:
        from .widget import request_open  # import paresseux : Qt requis seulement ici
    except Exception:
        return None
    try:
        opened = request_open(timeout=timeout)
    except Exception:
        return None
    if not opened:
        return None
    # request_open attend la transition loading/ready ; dispatch gère l'attente ready.
    return controller


def world_set_view(view: str = protocol.VIEW_GLOBE) -> dict[str, Any]:
    controller = get_default_controller()
    if controller.lifecycle == "idle":
        return {
            "success": True,
            "view": protocol.VIEW_GLOBE,
            "note": "module fermé — la vue par défaut s'applique à la prochaine ouverture",
        }
    return controller.set_view(view)


def world_set_camera(latitude: float, longitude: float, altitude_m: float | None = None,
                     heading_deg: float | None = None, pitch_deg: float | None = None,
                     duration_s: float | None = None) -> dict[str, Any]:
    controller = _ensure_module_open()
    if controller is None:
        return {"success": False, "error": "world_unavailable",
                "detail": "Interface Qt indisponible ou module non ouvrable."}
    return controller.set_camera(latitude, longitude, altitude_m, heading_deg, pitch_deg, duration_s)


def world_focus_region(region: str, altitude_m: float | None = None) -> dict[str, Any]:
    controller = _ensure_module_open()
    if controller is None:
        return {"success": False, "error": "world_unavailable",
                "detail": "Interface Qt indisponible ou module non ouvrable."}
    return controller.focus_region(region, altitude_m)


def world_enable_layer(layer_id: str) -> dict[str, Any]:
    controller = _ensure_module_open()
    if controller is None:
        return {"success": False, "error": "world_unavailable",
                "detail": "Interface Qt indisponible ou module non ouvrable."}
    return controller.enable_layer(layer_id)


def world_disable_layer(layer_id: str) -> dict[str, Any]:
    controller = get_default_controller()
    if controller.lifecycle == "idle":
        # Idempotent : rien à fermer.
        return {"success": True, "layer_id": str(layer_id).lower(), "enabled": False, "changed": False}
    return controller.disable_layer(layer_id)


def world_get_state() -> dict[str, Any]:
    controller = get_default_controller()
    return controller.get_state()


WORLD_TOOL_FUNCTIONS = {
    "world_set_view": world_set_view,
    "world_set_camera": world_set_camera,
    "world_focus_region": world_focus_region,
    "world_enable_layer": world_enable_layer,
    "world_disable_layer": world_disable_layer,
    "world_get_state": world_get_state,
}
