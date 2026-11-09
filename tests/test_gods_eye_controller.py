"""Contrôleur world_* — cycle de vie, idempotence, anti double-abonnement."""

from __future__ import annotations

import json
import threading
import time
import unittest

from src.gods_eye import protocol
from src.gods_eye.controller import GodsEyeController


class FakeTransport:
    """Transport en mémoire : répond de façon synchrone (comme le pont Qt)."""

    def __init__(self, *, repeat: int = 1, drop: bool = False):
        self._handler = None
        self.sent: list[dict] = []
        self.repeat = repeat
        self.drop = drop
        self.respond = None  # callable(request) -> response dict

    def set_response_handler(self, handler):
        self._handler = handler

    def send(self, payload: str) -> bool:
        request = json.loads(payload)
        self.sent.append(request)
        if self.drop:
            return True  # message perdu : aucune réponse n'arrivera
        response = None
        if self.respond is not None:
            response = self.respond(request)
        else:
            response = self._default_response(request)
        for _ in range(self.repeat):
            if self._handler is not None:
                self._handler(json.dumps(response))
        return True

    @staticmethod
    def _default_response(request):
        action = request["action"]
        base = {"id": request["id"], "ok": True, "action": action}
        if action == protocol.ACTION_ENABLE_LAYER:
            base.update(layer_id=request["args"]["layer_id"], enabled=True, changed=True, objectCount=0, source="adsb.lol")
        elif action == protocol.ACTION_DISABLE_LAYER:
            base.update(layer_id=request["args"]["layer_id"], enabled=False, changed=True)
        elif action == protocol.ACTION_FOCUS_REGION:
            base.update(region=request["args"]["region"], camera={"latitude": 48.85, "longitude": 2.35, "altitude_m": 150_000.0, "heading_deg": 0.0, "pitch_deg": -40.0}, arrived=True)
        elif action == protocol.ACTION_SET_CAMERA:
            base.update(camera={"latitude": request["args"]["latitude"], "longitude": request["args"]["longitude"], "altitude_m": request["args"].get("altitude_m", 2_000_000.0), "heading_deg": 0.0, "pitch_deg": -35.0}, arrived=True)
        elif action == protocol.ACTION_SET_VIEW:
            base.update(view=request["args"].get("view", "globe"))
        elif action == protocol.ACTION_GET_STATE:
            base.update(state={
                "camera": {"latitude": 48.85, "longitude": 2.35, "altitude_m": 18_000_000.0, "heading_deg": 0.0, "pitch_deg": -90.0},
                "baseLayer": "esri-imagery",
                "layers": {"flights": {"enabled": False, "objectCount": 0, "source": "none", "lastError": None, "lastUpdated": None}},
                "governor": {"installed": True, "mode": "idle", "holds": [], "recentRequests": []},
                "credit": ["Powered by Esri"],
            })
        return base


def _ready_controller(transport=None, **kwargs) -> GodsEyeController:
    controller = GodsEyeController(**kwargs)
    transport = transport or FakeTransport()
    controller.attach_transport(transport)
    controller.notify_ready()
    return controller


class LifecycleTests(unittest.TestCase):
    def test_transitions_idle_loading_ready_idle(self):
        controller = GodsEyeController()
        self.assertEqual(controller.lifecycle, "idle")
        transport = FakeTransport()
        self.assertTrue(controller.attach_transport(transport))
        self.assertEqual(controller.lifecycle, "loading")
        controller.notify_ready()
        self.assertEqual(controller.lifecycle, "ready")
        controller.detach_transport()
        self.assertEqual(controller.lifecycle, "idle")

    def test_attach_retourne_false_si_meme_transport(self):
        controller = GodsEyeController()
        transport = FakeTransport()
        self.assertTrue(controller.attach_transport(transport))
        self.assertFalse(controller.attach_transport(transport))

    def test_detach_idempotent(self):
        controller = GodsEyeController()
        controller.detach_transport()  # no-op, ne doit pas lever
        transport = FakeTransport()
        controller.attach_transport(transport)
        controller.detach_transport(transport)
        controller.detach_transport(transport)  # idempotent
        self.assertEqual(controller.lifecycle, "idle")

    def test_dispatch_sans_ouverture(self):
        controller = GodsEyeController()
        result = controller.dispatch(protocol.ACTION_GET_STATE)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "world_not_open")

    def test_dispatch_timeout_si_jamais_pret(self):
        controller = GodsEyeController(ready_wait_s=0.05)
        transport = FakeTransport()
        controller.attach_transport(transport)  # jamais notify_ready
        result = controller.dispatch(protocol.ACTION_GET_STATE, timeout=0.1)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "world_not_ready")

    def test_dispatch_timeout_si_reponse_perdue(self):
        controller = _ready_controller(FakeTransport(drop=True))
        result = controller.dispatch(protocol.ACTION_GET_STATE, timeout=0.05)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "world_timeout")
        self.assertEqual(controller.stats()["timeouts"], 1)

    def test_destroyed_pendant_dispatch_echoue_vite(self):
        controller = GodsEyeController(ready_wait_s=5.0)
        transport = FakeTransport(drop=True)  # jamais de réponse
        controller.attach_transport(transport)
        controller.notify_ready()
        outcome: dict = {}

        def run():
            outcome.update(controller.dispatch(protocol.ACTION_GET_STATE, timeout=5.0))

        worker = threading.Thread(target=run)
        worker.start()
        time.sleep(0.05)
        controller.notify_destroyed()
        worker.join(timeout=2.0)
        self.assertFalse(worker.is_alive(), "dispatch n'a pas été libéré par notify_destroyed")
        self.assertFalse(outcome["success"])
        self.assertEqual(outcome["error"], "Module fermé pendant la requête")


class LayerIdempotenceTests(unittest.TestCase):
    def test_enable_une_seule_fois_envoyee_au_globe(self):
        transport = FakeTransport()
        controller = _ready_controller(transport)
        first = controller.enable_layer("flights")
        second = controller.enable_layer("flights")
        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        enables = [r for r in transport.sent if r["action"] == protocol.ACTION_ENABLE_LAYER]
        self.assertEqual(len(enables), 1, "double abonnement : la couche a été activée deux fois")

    def test_disable_une_seule_fois_envoyee(self):
        transport = FakeTransport()
        controller = _ready_controller(transport)
        controller.enable_layer("flights")
        first = controller.disable_layer("flights")
        second = controller.disable_layer("flights")
        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        disables = [r for r in transport.sent if r["action"] == protocol.ACTION_DISABLE_LAYER]
        self.assertEqual(len(disables), 1)

    def test_churn_enable_disable_repete(self):
        transport = FakeTransport()
        controller = _ready_controller(transport)
        for _ in range(10):
            self.assertTrue(controller.enable_layer("flights")["success"])
            self.assertTrue(controller.disable_layer("flights")["success"])
        stats = controller.stats()
        self.assertEqual(stats["pending"], 0)
        self.assertEqual(stats["timeouts"], 0)
        self.assertEqual(stats["stale_responses"], 0)

    def test_enable_disable_rapide_alterne(self):
        transport = FakeTransport()
        controller = _ready_controller(transport)
        sequence = [controller.enable_layer("flights") for _ in range(1)]
        controller.disable_layer("flights")
        controller.enable_layer("flights")
        controller.enable_layer("flights")  # idempotent
        disables = [r for r in transport.sent if r["action"] == protocol.ACTION_DISABLE_LAYER]
        enables = [r for r in transport.sent if r["action"] == protocol.ACTION_ENABLE_LAYER]
        self.assertEqual(len(disables), 1)
        self.assertEqual(len(enables), 2)  # 1er + celui après disable ; pas le idempotent

    def test_couche_inconnue_rejetee(self):
        controller = _ready_controller(FakeTransport())
        result = controller.enable_layer("cctv")
        self.assertFalse(result["success"])
        self.assertIn("couche non autorisée", result["error"])

    def test_etat_couche_synchronise_apres_enable(self):
        transport = FakeTransport()
        controller = _ready_controller(transport)
        controller.enable_layer("flights")
        state = controller.cached_state()
        self.assertTrue(state["layers"]["flights"]["enabled"])
        self.assertEqual(state["layers"]["flights"]["source"], "adsb.lol")


class ResponseHandlingTests(unittest.TestCase):
    def test_reponse_inconnue_ignoree_et_comptee(self):
        controller = _ready_controller(FakeTransport())
        controller._on_response(json.dumps({"id": "f" * 16, "ok": True, "action": "bogus"}))
        self.assertEqual(controller.stats()["stale_responses"], 1)

    def test_reponse_double_comptee_mais_reponse_unique(self):
        controller = _ready_controller(FakeTransport(repeat=2))
        result = controller.dispatch(protocol.ACTION_GET_STATE)
        self.assertTrue(result["success"])
        self.assertEqual(controller.stats()["duplicate_responses"], 1)

    def test_reponse_malformee_comptee(self):
        controller = _ready_controller(FakeTransport())
        controller._on_response("{pas json")
        self.assertEqual(controller.stats()["protocol_errors"], 1)

    def test_get_state_live(self):
        transport = FakeTransport()
        controller = _ready_controller(transport)
        result = controller.get_state()
        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "live")
        self.assertEqual(result["state"]["baseLayer"], "esri-imagery")

    def test_get_state_cache_si_ferme(self):
        controller = GodsEyeController()
        result = controller.get_state()
        self.assertTrue(result["success"])
        self.assertEqual(result["source"], "cache")
        self.assertEqual(result["state"]["lifecycle"], "idle")

    def test_focus_region_retourne_camera(self):
        transport = FakeTransport()
        controller = _ready_controller(transport)
        result = controller.focus_region("paris")
        self.assertTrue(result["success"])
        self.assertEqual(result["camera"]["altitude_m"], 150_000.0)

    def test_action_invalide_retourne_erreur_protocole(self):
        controller = _ready_controller(FakeTransport())
        result = controller.dispatch("world_nuke_everything")
        self.assertFalse(result["success"])
        self.assertEqual(controller.stats()["protocol_errors"], 1)


class ListenerTests(unittest.TestCase):
    def test_listener_double_enregistrement(self):
        controller = GodsEyeController()
        calls = []

        def listener(event, payload):
            calls.append((event, payload))

        self.assertTrue(controller.add_listener(listener))
        self.assertFalse(controller.add_listener(listener))  # pas de doublon
        self.assertEqual(controller.listener_count(), 1)
        controller._emit("ready", {})
        controller._emit("ready", {})
        self.assertEqual([c[0] for c in calls], ["ready", "ready"])

    def test_listener_defaillant_ne_casse_pas_les_autres(self):
        controller = GodsEyeController()
        calls = []

        def bad(event, payload):
            raise RuntimeError("boom")

        def good(event, payload):
            calls.append(event)

        controller.add_listener(bad)
        controller.add_listener(good)
        controller._emit("closed", {})
        self.assertEqual(calls, ["closed"])

    def test_remove_listener(self):
        controller = GodsEyeController()

        def listener(event, payload):
            pass

        controller.add_listener(listener)
        self.assertTrue(controller.remove_listener(listener))
        self.assertFalse(controller.remove_listener(listener))
        self.assertEqual(controller.listener_count(), 0)

    def test_evenements_pret_et_ferme_emis(self):
        controller = GodsEyeController()
        events = []
        controller.add_listener(lambda event, payload: events.append(event))
        transport = FakeTransport()
        controller.attach_transport(transport)
        controller.notify_ready()
        controller.detach_transport()
        self.assertEqual(events, ["ready", "closed"])


if __name__ == "__main__":
    unittest.main()
