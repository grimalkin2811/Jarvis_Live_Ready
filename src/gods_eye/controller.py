"""Contrôleur du module God's Eye View — logique hôte, sans Qt.

Le contrôleur détient :

* le cycle de vie du module (``idle → loading → ready → closing → idle``) ;
* la correspondance requête/réponse (id) avec le module web ;
* l'idempotence des bascules de couches (``changed=False`` si déjà dans l'état
  demandé — aucun message renvoyé au globe) ;
* la protection anti double-abonnement (listeners à identité, transport unique) ;
* des statistiques pour les tests et les diagnostics (``stats()``).

Aucun timer Python : le polling des données vit côté module web (un seul
``setTimeout`` à la fois, désactivé dès que la couche est coupée). Le thread
vocal de Jarvis appelle les outils en synchrone (``asyncio.to_thread``) —
``dispatch`` bloque donc sur un ``threading.Event`` borné par ``timeout``.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from . import protocol

LIFECYCLE_IDLE = "idle"
LIFECYCLE_LOADING = "loading"
LIFECYCLE_READY = "ready"
LIFECYCLE_CLOSING = "closing"

DEFAULT_TIMEOUT_S = 5.0
READY_WAIT_S = 6.0


class WorldUnavailable(RuntimeError):
    """Le module n'est pas ouvert (ou plus utilisable)."""


def _iso_now() -> float:
    return time.time()


class GodsEyeController:
    """Contrôleur thread-safe. Une seule instance partagée (get_default_controller)."""

    def __init__(self, *, ready_wait_s: float = READY_WAIT_S, default_timeout_s: float = DEFAULT_TIMEOUT_S) -> None:
        self._ready_wait_s = float(ready_wait_s)
        self._default_timeout_s = float(default_timeout_s)
        self._lock = threading.RLock()
        self._lifecycle = LIFECYCLE_IDLE
        self._transport: Any | None = None
        self._pending: dict[str, dict[str, Any]] = {}
        self._listeners: list[Callable[[str, dict[str, Any]], None]] = []
        self._ready_event = threading.Event()
        self._closed_event = threading.Event()
        self._layers: dict[str, dict[str, Any]] = {
            layer_id: {
                "enabled": False,
                "objectCount": 0,
                "source": "none",
                "lastError": None,
                "lastUpdated": None,
            }
            for layer_id in protocol.LAYER_IDS
        }
        self._camera: dict[str, Any] | None = None
        self._base_layer: str | None = None
        self._governor: dict[str, Any] | None = None
        self._credits: list[str] = []
        self._stats: dict[str, int] = {
            "dispatched": 0,
            "completed": 0,
            "timeouts": 0,
            "stale_responses": 0,
            "duplicate_responses": 0,
            "protocol_errors": 0,
            "layer_enable_requests": 0,
            "layer_disable_requests": 0,
            "layer_toggles_noop": 0,
            "opens": 0,
            "closes": 0,
        }

    # ------------------------------------------------------------------ #
    # Cycle de vie                                                       #
    # ------------------------------------------------------------------ #

    @property
    def lifecycle(self) -> str:
        with self._lock:
            return self._lifecycle

    def is_ready(self) -> bool:
        return self.lifecycle == LIFECYCLE_READY

    def stats(self) -> dict[str, Any]:
        with self._lock:
            snapshot = dict(self._stats)
            snapshot["lifecycle"] = self._lifecycle
            snapshot["pending"] = len(self._pending)
            return snapshot

    def attach_transport(self, transport: Any) -> bool:
        """Enregistre LE transport (singleton). Un éventuel précédent est détaché."""
        with self._lock:
            if self._transport is transport:
                return False
            previous = self._transport
            self._transport = transport
            self._lifecycle = LIFECYCLE_LOADING
            self._ready_event.clear()
            self._closed_event.clear()
            self._stats["opens"] += 1
        if previous is not None:
            try:
                previous.set_response_handler(None)
            except Exception:
                pass
        # Enregistrement du handler de réponse : exactement un par transport.
        try:
            transport.set_response_handler(self._on_response)
        except Exception:
            with self._lock:
                self._transport = None
                self._lifecycle = LIFECYCLE_IDLE
            raise
        return True

    def detach_transport(self, transport: Any | None = None) -> None:
        """Détache le transport (idempotent). Sans argument : détache l'unique transport."""
        with self._lock:
            if transport is not None and self._transport is not transport:
                return
            current = self._transport
            self._transport = None
        if current is not None:
            try:
                current.set_response_handler(None)
            except Exception:
                pass
        self._finish_close()

    def notify_ready(self) -> None:
        """Appelé par l'hôte quand la page a chargé et que le pont répond."""
        with self._lock:
            if self._lifecycle != LIFECYCLE_LOADING:
                return
            self._lifecycle = LIFECYCLE_READY
            self._ready_event.set()
        self._emit("ready", {})

    def notify_destroyed(self) -> None:
        """Appelé par l'hôte après destruction complète de la page web."""
        with self._lock:
            self._transport = None
            self._finish_close_locked()
        self._emit("closed", {})

    def _finish_close(self) -> None:
        with self._lock:
            self._finish_close_locked()
        self._emit("closed", {})

    def _finish_close_locked(self) -> None:
        self._lifecycle = LIFECYCLE_IDLE
        self._ready_event.clear()
        self._closed_event.set()
        self._stats["closes"] += 1
        for layer in self._layers.values():
            layer.update(
                enabled=False,
                objectCount=0,
                source="none",
                lastError=None,
                lastUpdated=None,
            )
        self._camera = None
        self._base_layer = None
        self._governor = None
        self._credits = []
        self._fail_all_pending_locked("world_closed", "Module fermé pendant la requête")

    def _fail_all_pending_locked(self, code: str, message: str) -> None:
        for pending in list(self._pending.values()):
            pending["response"] = {
                "id": pending["id"],
                "ok": False,
                "action": pending["action"],
                "error": message,
                "code": code,
            }
            pending["event"].set()
        self._pending.clear()

    # ------------------------------------------------------------------ #
    # Listeners (exactement-une-fois par événement, pas de doublon)      #
    # ------------------------------------------------------------------ #

    def add_listener(self, listener: Callable[[str, dict[str, Any]], None]) -> bool:
        with self._lock:
            if any(listener is existing for existing in self._listeners):
                return False
            self._listeners.append(listener)
            return True

    def remove_listener(self, listener: Callable[[str, dict[str, Any]], None]) -> bool:
        with self._lock:
            for index, existing in enumerate(self._listeners):
                if existing is listener:
                    del self._listeners[index]
                    return True
            return False

    def listener_count(self) -> int:
        with self._lock:
            return len(self._listeners)

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(event, dict(payload))
            except Exception:
                # Un listener défaillant ne doit pas corrompre le contrôleur.
                continue

    # ------------------------------------------------------------------ #
    # Dispatch                                                           #
    # ------------------------------------------------------------------ #

    def dispatch(
        self,
        action: str,
        args: dict[str, Any] | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> dict[str, Any]:
        try:
            request = protocol.build_request(action, args)
        except protocol.ProtocolError as exc:
            with self._lock:
                self._stats["protocol_errors"] += 1
            return {"success": False, "error": str(exc), "action": str(action)}

        with self._lock:
            if self._lifecycle == LIFECYCLE_IDLE:
                return {
                    "success": False,
                    "error": "world_not_open",
                    "hint": "Le globe God's Eye View n'est pas ouvert. Appelez world_enable_layer ou world_focus_region : Jarvis l'ouvre à la demande.",
                    "action": request["action"],
                }
            if self._lifecycle == LIFECYCLE_CLOSING:
                return {"success": False, "error": "world_closing", "action": request["action"]}
            transport = self._transport

        # Attente bornée du chargement initial (page + Cesium).
        if self.lifecycle != LIFECYCLE_READY and not self._ready_event.wait(self._ready_wait_s):
            return {
                "success": False,
                "error": "world_not_ready",
                "hint": "Le globe se charge encore.",
                "action": request["action"],
            }

        with self._lock:
            transport = self._transport
            if transport is None or self._lifecycle != LIFECYCLE_READY:
                return {"success": False, "error": "world_not_open", "action": request["action"]}
            pending = {
                "id": request["id"],
                "action": request["action"],
                "sent_at": _iso_now(),
                "event": threading.Event(),
                "response": None,
            }
            self._pending[request["id"]] = pending
            self._stats["dispatched"] += 1

        try:
            accepted = transport.send(protocol.dumps_request(request))
        except Exception as exc:
            with self._lock:
                self._pending.pop(request["id"], None)
                self._stats["protocol_errors"] += 1
            return {"success": False, "error": f"transport: {exc}", "action": request["action"]}
        if accepted is False:
            with self._lock:
                self._pending.pop(request["id"], None)
                self._stats["protocol_errors"] += 1
            return {"success": False, "error": "transport_rejected", "action": request["action"]}

        completed = pending["event"].wait(timeout)
        with self._lock:
            response = pending["response"]
            self._pending.pop(request["id"], None)
        if not completed or response is None:
            with self._lock:
                self._stats["timeouts"] += 1
            return {
                "success": False,
                "error": "world_timeout",
                "timeout_s": timeout,
                "action": request["action"],
            }
        return self._finalize(response)

    def _on_response(self, raw: Any) -> None:
        """Appelé par le transport (thread Qt, timer…) — jamais bloquant."""
        try:
            response = protocol.parse_response(raw)
        except protocol.ProtocolError:
            with self._lock:
                self._stats["protocol_errors"] += 1
            return
        request_id = response.get("id")
        with self._lock:
            pending = self._pending.get(request_id) if request_id else None
            if pending is None:
                # Réponse tardive ou inconnue : ignorée, comptée.
                self._stats["stale_responses"] += 1
                return
            if pending["response"] is not None:
                self._stats["duplicate_responses"] += 1
                return
            pending["response"] = response
        pending["event"].set()

    def _finalize(self, response: dict[str, Any]) -> dict[str, Any]:
        action = response.get("action", "")
        ok = response.get("ok") is True
        with self._lock:
            self._stats["completed"] += 1
            if ok:
                self._cache_from_response_locked(action, response)
        if not ok:
            return {
                "success": False,
                "error": response.get("error") or "action refusée par le module",
                "action": action,
                **{k: v for k, v in response.items() if k not in {"id", "ok", "action", "error"}},
            }
        result = {
            "success": True,
            **{k: v for k, v in response.items() if k not in {"id", "ok", "action"}},
        }
        if action == protocol.ACTION_GET_STATE:
            result["source"] = "live"
        result.setdefault("action", action)
        return result

    def _cache_from_response_locked(self, action: str, response: dict[str, Any]) -> None:
        if action in (protocol.ACTION_ENABLE_LAYER, protocol.ACTION_DISABLE_LAYER):
            layer_id = str(response.get("layer_id") or "")
            if layer_id in self._layers:
                layer = self._layers[layer_id]
                enabled = bool(response.get("enabled", layer["enabled"]))
                layer["enabled"] = enabled
                layer["source"] = response.get("source", layer["source"]) or layer["source"]
                if not enabled:
                    layer.update(objectCount=0, lastError=None, lastUpdated=None)
                if "objectCount" in response and response["objectCount"] is not None:
                    layer["objectCount"] = int(response["objectCount"])
        elif action == protocol.ACTION_GET_STATE:
            state = response.get("state") or {}
            camera = state.get("camera")
            if isinstance(camera, dict):
                self._camera = camera
            if isinstance(state.get("baseLayer"), str):
                self._base_layer = state["baseLayer"]
            if isinstance(state.get("governor"), dict):
                self._governor = state["governor"]
            credits = state.get("credit")
            if isinstance(credits, list):
                self._credits = [str(c) for c in credits]
            layers = state.get("layers")
            if isinstance(layers, dict):
                for layer_id, layer_state in layers.items():
                    if layer_id in self._layers and isinstance(layer_state, dict):
                        target = self._layers[layer_id]
                        for key in ("enabled", "objectCount", "source", "lastError", "lastUpdated"):
                            if key in layer_state:
                                target[key] = layer_state[key]
        elif action in (protocol.ACTION_SET_CAMERA, protocol.ACTION_FOCUS_REGION):
            camera = response.get("camera")
            if isinstance(camera, dict):
                self._camera = camera

    # ------------------------------------------------------------------ #
    # API métier (appelée par les outils world_*)                         #
    # ------------------------------------------------------------------ #

    def _layer_state(self, layer_id: str) -> dict[str, Any]:
        return dict(self._layers[layer_id])

    def enable_layer(self, layer_id: str) -> dict[str, Any]:
        try:
            layer_id = protocol.validate_layer_id(layer_id)
        except protocol.ProtocolError as exc:
            return {"success": False, "error": str(exc)}
        with self._lock:
            self._stats["layer_enable_requests"] += 1
            if self._lifecycle == LIFECYCLE_READY and self._layers[layer_id]["enabled"]:
                # Idempotent : déjà dans l'état demandé, aucun message au globe.
                self._stats["layer_toggles_noop"] += 1
                state = self._layer_state(layer_id)
                return {"success": True, "layer_id": layer_id, "enabled": True, "changed": False, **state}
        response = self.dispatch(protocol.ACTION_ENABLE_LAYER, {"layer_id": layer_id})
        response.setdefault("layer_id", layer_id)
        response["enabled"] = True if response.get("success") else self._layers[layer_id]["enabled"]
        return response

    def disable_layer(self, layer_id: str) -> dict[str, Any]:
        try:
            layer_id = protocol.validate_layer_id(layer_id)
        except protocol.ProtocolError as exc:
            return {"success": False, "error": str(exc)}
        with self._lock:
            self._stats["layer_disable_requests"] += 1
            if not self._layers[layer_id]["enabled"] and self._lifecycle != LIFECYCLE_LOADING:
                self._stats["layer_toggles_noop"] += 1
                return {"success": True, "layer_id": layer_id, "enabled": False, "changed": False}
        response = self.dispatch(protocol.ACTION_DISABLE_LAYER, {"layer_id": layer_id})
        response.setdefault("layer_id", layer_id)
        response["enabled"] = False if response.get("success") else self._layers[layer_id]["enabled"]
        return response

    def set_view(self, view: str = protocol.VIEW_GLOBE) -> dict[str, Any]:
        response = self.dispatch(protocol.ACTION_SET_VIEW, {"view": view})
        response.setdefault("view", protocol.VIEW_GLOBE)
        return response

    def set_camera(
        self,
        latitude: float,
        longitude: float,
        altitude_m: float | None = None,
        heading_deg: float | None = None,
        pitch_deg: float | None = None,
        duration_s: float | None = None,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {"latitude": latitude, "longitude": longitude}
        if altitude_m is not None:
            args["altitude_m"] = altitude_m
        if heading_deg is not None:
            args["heading_deg"] = heading_deg
        if pitch_deg is not None:
            args["pitch_deg"] = pitch_deg
        if duration_s is not None:
            args["duration_s"] = duration_s
        return self.dispatch(protocol.ACTION_SET_CAMERA, args)

    def focus_region(self, region: str, altitude_m: float | None = None) -> dict[str, Any]:
        args: dict[str, Any] = {"region": region}
        if altitude_m is not None:
            args["altitude_m"] = altitude_m
        response = self.dispatch(protocol.ACTION_FOCUS_REGION, args)
        response.setdefault("region", str(region).lower().strip())
        return response

    def get_state(self, timeout: float = 1.5) -> dict[str, Any]:
        """État live si possible, cache sinon — jamais d'erreur brute."""
        if self.lifecycle == LIFECYCLE_READY:
            live = self.dispatch(protocol.ACTION_GET_STATE, {}, timeout=timeout)
            if live.get("success"):
                return live
            return {
                "success": True,
                "state": self.cached_state(),
                "source": "cache",
                "warning": live.get("error"),
            }
        return {
            "success": True,
            "state": self.cached_state(),
            "source": "cache",
            "lifecycle": self.lifecycle,
        }

    def cached_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "camera": dict(self._camera) if self._camera else None,
                "baseLayer": self._base_layer,
                "layers": {k: dict(v) for k, v in self._layers.items()},
                "governor": dict(self._governor) if self._governor else None,
                "credit": list(self._credits),
                "lifecycle": self._lifecycle,
            }


_default_controller: GodsEyeController | None = None
_default_lock = threading.Lock()


def get_default_controller() -> GodsEyeController:
    global _default_controller
    with _default_lock:
        if _default_controller is None:
            _default_controller = GodsEyeController()
        return _default_controller


def reset_default_controller() -> None:
    """Pour les tests : détruit le singleton."""
    global _default_controller
    with _default_lock:
        _default_controller = None
