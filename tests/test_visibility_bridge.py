"""Tests du pont de visibilité explicite (Blob / menu) et de ses outils.

Ces tests ne dépendent pas de Qt : le pont est la couche que le backend
vocal écrit ; l'application par l'interface est couverte dans
``test_orb_visibility.py`` (rendu offscreen).

Couvre :
* l'intention explicite = une ACTION (pas un filtre de configuration) ;
* la résolution des 5 noms de menu (fr/ang) ;
* les outils show/hide blob/menu + get_ui_state (avec/sans interface) ;
* le passage des commandes de visibilité dans le garde-fou de mode jeu ;
* l'absence d'accumulation de requêtes (un slot par action).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from UI import visibility_bridge
from src import modes, tools


def _fresh_bridge() -> visibility_bridge.VisibilityBridge:
    return visibility_bridge.VisibilityBridge()


class BridgeTests(unittest.TestCase):
    def test_explicit_show_is_an_action_not_a_filter(self) -> None:
        bridge = _fresh_bridge()
        # Aucun filtre de configuration ici : la demande est simplement
        # mémorisée et remise à l'interface, qui DOIT l'appliquer.
        bridge.show_blob()
        requests = bridge.consume_requests()
        self.assertEqual([r["action"] for r in requests], ["show_blob"])
        # Consommé : rien ne reste.
        self.assertEqual(bridge.consume_requests(), [])

    def test_one_slot_per_action_no_accumulation(self) -> None:
        bridge = _fresh_bridge()
        bridge.show_blob()
        bridge.show_blob()
        bridge.show_blob()
        self.assertEqual(len(bridge.consume_requests()), 1)

    def test_all_four_requests_are_delivered(self) -> None:
        bridge = _fresh_bridge()
        bridge.show_blob()
        bridge.hide_menu()
        bridge.show_menu("System")
        bridge.hide_blob()
        requests = bridge.consume_requests()
        # Ordre de livraison déterministe (blob d'abord, puis les menus).
        self.assertEqual(
            [r["action"] for r in requests],
            ["show_blob", "hide_blob", "show_menu", "hide_menu"],
        )
        self.assertEqual(next(r for r in requests if r["action"] == "show_menu")["menu"], "System")

    def test_state_reporting(self) -> None:
        bridge = _fresh_bridge()
        self.assertFalse(bridge.ui_state()["ui_attached"])
        bridge.report_state(ui_attached=True, blob_visible=True, menu_open=True, menu="Routines")
        state = bridge.ui_state()
        self.assertTrue(state["ui_attached"])
        self.assertTrue(state["blob_visible"])
        self.assertEqual(state["menu"], "Routines")


class MenuNameResolutionTests(unittest.TestCase):
    def test_all_five_menus_resolve(self) -> None:
        for name in ("Voice", "System", "Memory", "Appearance", "Routines"):
            self.assertEqual(visibility_bridge.resolve_menu_name(name), name)

    def test_french_aliases(self) -> None:
        cases = {
            "voix": "Voice",
            "système": "System",
            "syteme": "System",
            "SYSTEME": "System",
            "mémoire": "Memory",
            "memoire": "Memory",
            "apparence": "Appearance",
            "routine": "Routines",
        }
        for alias, expected in cases.items():
            self.assertEqual(visibility_bridge.resolve_menu_name(alias), expected, alias)

    def test_unknown_menu_stays_unknown(self) -> None:
        self.assertIsNone(visibility_bridge.resolve_menu_name("kitchen"))
        self.assertIsNone(visibility_bridge.resolve_menu_name(""))
        self.assertIsNone(visibility_bridge.resolve_menu_name(None))


def _manager(directory: Path) -> modes.JarvisModeManager:
    return modes.JarvisModeManager(directory / "mode.json")


class VisibilityToolsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.manager = _manager(self.directory)
        previous_manager = modes._DEFAULT_MANAGER
        modes.set_default_mode_manager(self.manager)
        self.addCleanup(modes.set_default_mode_manager, previous_manager)
        previous_bridge = visibility_bridge.VISIBILITY
        visibility_bridge.VISIBILITY = _fresh_bridge()
        self.addCleanup(setattr, visibility_bridge, "VISIBILITY", previous_bridge)
        for name in ("_set_jarvis_low_priority", "_restore_jarvis_priority"):
            patcher = patch.object(
                modes.JarvisModeManager, name, return_value={"success": True, "applique": False}
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------
    # Sans interface (console / overlay) : réponse honnête, pas de crash.
    # ------------------------------------------------------------------
    def test_tools_without_ui_are_honest(self) -> None:
        for name in ("show_blob", "hide_blob", "show_menu", "hide_menu"):
            result = tools.TOOL_FUNCTIONS[name]()
            self.assertFalse(result["success"], name)
            self.assertIn("error", result, name)
        state = tools.TOOL_FUNCTIONS["get_ui_state"]()
        self.assertTrue(state["success"])
        self.assertFalse(state["ui_attached"])

    # ------------------------------------------------------------------
    # Avec interface : les commandes sont transmises au pont.
    # ------------------------------------------------------------------
    def test_show_blob_transmitted_to_bridge(self) -> None:
        visibility_bridge.VISIBILITY.report_state(
            ui_attached=True, blob_visible=False, menu_open=False, menu=None
        )
        result = tools.TOOL_FUNCTIONS["show_blob"]()
        self.assertTrue(result["success"])
        requests = visibility_bridge.VISIBILITY.consume_requests()
        self.assertEqual([r["action"] for r in requests], ["show_blob"])

    def test_show_menu_validates_names(self) -> None:
        visibility_bridge.VISIBILITY.report_state(
            ui_attached=True, blob_visible=True, menu_open=False, menu=None
        )
        # Nom valide (alias français).
        result = tools.TOOL_FUNCTIONS["show_menu"](menu="système")
        self.assertTrue(result["success"])
        requests = visibility_bridge.VISIBILITY.consume_requests()
        self.assertEqual(requests[0]["menu"], "System")
        # Nom inconnu : refus clair (pas d'invention de menu).
        result = tools.TOOL_FUNCTIONS["show_menu"](menu="cuisine")
        self.assertFalse(result["success"])
        self.assertEqual(visibility_bridge.VISIBILITY.consume_requests(), [])
        # Sans précision : le widget choisira le dernier/par défaut.
        result = tools.TOOL_FUNCTIONS["show_menu"]()
        self.assertTrue(result["success"])
        requests = visibility_bridge.VISIBILITY.consume_requests()
        self.assertIsNone(requests[0]["menu"])

    def test_get_ui_state_reports_real_state(self) -> None:
        visibility_bridge.VISIBILITY.report_state(
            ui_attached=True, blob_visible=True, menu_open=True, menu="Memory"
        )
        state = tools.TOOL_FUNCTIONS["get_ui_state"]()
        self.assertTrue(state["success"])
        self.assertTrue(state["blob_visible"])
        self.assertTrue(state["menu_open"])
        self.assertEqual(state["menu"], "Memory")

    # ------------------------------------------------------------------
    # Gardes-fous : les commandes de visibilité passent dans TOUS les modes.
    # ------------------------------------------------------------------
    def test_visibility_commands_allowed_during_game_mode(self) -> None:
        self.manager.activate_game_mode(close_background=False)
        # Le blob est masqué par la politique du mode — mais la commande
        # explicite doit pouvoir passer (le widget l'appliquera).
        for name in ("show_blob", "hide_blob", "show_menu", "hide_menu", "get_ui_state"):
            blocked = modes.get_default_mode_manager().block_for_tool(name, {})
            self.assertIsNone(blocked, name)
        # ...pendant que les actions écran restent bloquées.
        blocked = modes.get_default_mode_manager().block_for_tool("open_application", {"application": "notepad"})
        self.assertIsNotNone(blocked)

    def test_show_blob_works_while_blob_is_hidden_by_config(self) -> None:
        # Le pont ne filtre pas : même si l'interface n'est « pas visible »
        # (état rapporté), la demande part.
        visibility_bridge.VISIBILITY.report_state(
            ui_attached=True, blob_visible=False, menu_open=False, menu=None
        )
        result = tools.TOOL_FUNCTIONS["show_blob"]()
        self.assertTrue(result["success"])
        self.assertEqual(
            [r["action"] for r in visibility_bridge.VISIBILITY.consume_requests()],
            ["show_blob"],
        )

    def test_show_menu_allowed_during_focus_mode(self) -> None:
        self.manager.activate_focus_mode(close_distractions=False)
        self.assertIsNone(modes.get_default_mode_manager().block_for_tool("show_menu", {"menu": "voix"}))
        self.assertIsNone(modes.get_default_mode_manager().block_for_tool("show_blob", {}))


if __name__ == "__main__":
    unittest.main()
