"""Tests UI de l'interruption (menu radial, rendu offscreen).

Aucune action réelle : le backend vocal est remplacé par une poignée
factice enregistrée dans ``menu_state.LIVE``.
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _ci_diag  # noqa: E402,F401  (diagnostic CI temporaire)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import UI.jarvis_menu as jm  # noqa: E402
from UI import menu_state  # noqa: E402


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _voice_spec() -> jm.MenuSpec:
    for spec in jm.MENU_SPECS:
        if spec.name == "Voice":
            return spec
    raise AssertionError("menu Voice introuvable")


def _item(spec: jm.MenuSpec, label: str) -> jm.MenuItemSpec:
    for item in spec.items:
        if item.label == label:
            return item
    raise AssertionError(f"item {label} introuvable")


class _KeyEvent:
    """Événement clavier minimal (comme dans tests/test_menu_ux.py)."""

    def __init__(self, key, text=""):
        self._key = key
        self._text = text

    def key(self):
        return self._key

    def text(self):
        return self._text

    def modifiers(self):
        return Qt.NoModifier

    def accept(self):
        pass


class InterruptMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()
        cls.widget = jm.MorphingOrbWidget()
        cls.widget.resize(1280, 800)
        cls.widget.show()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.widget.close()
        cls.widget.deleteLater()
        menu_state.LIVE.set_stop_speaking_handler(None)

    def setUp(self) -> None:
        self.widget._close_radial_menu()
        self.widget.menu_state = jm.menu_state.MenuState()
        self.widget._menu_action_flash = ""
        menu_state.LIVE.set_stop_speaking_handler(None)
        menu_state.LIVE.set_barge_in(True)

    # ------------------------------------------------------------------
    # Présence des contrôles
    # ------------------------------------------------------------------
    def test_voice_menu_exposes_interrupt_controls(self) -> None:
        labels = [item.label for item in _voice_spec().items]
        self.assertIn("Interrupt Word", labels)
        self.assertIn("Stop Speaking", labels)

    def test_menu_renders_with_new_items(self) -> None:
        sector = jm.MENU_SPECS.index(_voice_spec())
        self.widget._open_radial_menu(sector)
        self.widget._menu_reveal = _voice_spec().reveal_scale
        self.widget._menu_alpha = 1.0
        for _ in range(60):
            self.widget.time += 1.0 / 60.0
            self.widget._update_menu_nodes()
        self.assertEqual(len(self.widget._menu_nodes), len(_voice_spec().items))
        self.assertFalse(self.widget.grab().isNull())
        self.widget._close_radial_menu()

    # ------------------------------------------------------------------
    # Toggle « Interrupt Word »
    # ------------------------------------------------------------------
    def test_toggle_updates_state_and_live_bridge(self) -> None:
        spec = _voice_spec()
        item = _item(spec, "Interrupt Word")
        self.assertTrue(self.widget._menu_toggle_value(spec, item))

        self.widget._menu_set_toggle(spec, item, False)
        self.assertFalse(self.widget.menu_state.barge_in)
        self.assertFalse(menu_state.LIVE.get_barge_in())

        self.widget._menu_set_toggle(spec, item, True)
        self.assertTrue(self.widget.menu_state.barge_in)
        self.assertTrue(menu_state.LIVE.get_barge_in())

    def test_toggle_callback_flashes_explicit_message(self) -> None:
        spec = _voice_spec()
        item = _item(spec, "Interrupt Word")
        self.widget._menu_callback_for(spec, item)()
        self.assertIn("Interruption vocale", self.widget._menu_action_flash)

    def test_value_label(self) -> None:
        self.widget.menu_state.barge_in = True
        self.assertEqual(self.widget._voice_value("Interrupt Word"), "On")
        self.widget.menu_state.barge_in = False
        self.assertEqual(self.widget._voice_value("Interrupt Word"), "Off")

    # ------------------------------------------------------------------
    # Bouton « Stop Speaking » et raccourci clavier
    # ------------------------------------------------------------------
    def test_stop_button_calls_backend(self) -> None:
        calls = []
        menu_state.LIVE.set_stop_speaking_handler(lambda: calls.append(1))
        spec = _voice_spec()
        self.widget._menu_callback_for(spec, _item(spec, "Stop Speaking"))()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.widget._menu_action_flash, "Réponse interrompue")

    def test_stop_button_without_backend_is_honest(self) -> None:
        spec = _voice_spec()
        self.widget._menu_callback_for(spec, _item(spec, "Stop Speaking"))()
        self.assertEqual(
            self.widget._menu_action_flash, "Aucune réponse à interrompre"
        )

    def test_stop_button_survives_backend_error(self) -> None:
        def _boom():
            raise RuntimeError("backend mort")

        menu_state.LIVE.set_stop_speaking_handler(_boom)
        spec = _voice_spec()
        self.widget._menu_callback_for(spec, _item(spec, "Stop Speaking"))()
        self.assertEqual(
            self.widget._menu_action_flash, "Aucune réponse à interrompre"
        )

    def test_s_shortcut_stops_speech(self) -> None:
        calls = []
        menu_state.LIVE.set_stop_speaking_handler(lambda: calls.append(1))
        self.widget.keyPressEvent(_KeyEvent(Qt.Key_S, "s"))
        self.assertEqual(len(calls), 1)

    def test_s_shortcut_does_not_break_other_keys(self) -> None:
        calls = []
        menu_state.LIVE.set_stop_speaking_handler(lambda: calls.append(1))
        # « M » reste le raccourci micro, les chiffres ouvrent les menus.
        self.widget.keyPressEvent(_KeyEvent(Qt.Key_M, "m"))
        self.assertEqual(len(calls), 0)
        self.widget.keyPressEvent(_KeyEvent(Qt.Key_1, "1"))
        self.assertEqual(len(calls), 0)
        self.assertEqual(self.widget._menu_sector, 0)
        self.widget._close_radial_menu()


class StopBridgeTests(unittest.TestCase):
    def test_handler_registration_roundtrip(self) -> None:
        live = menu_state.LiveControls()
        self.assertFalse(live.request_stop_speaking())
        calls = []
        live.set_stop_speaking_handler(lambda: calls.append(1))
        self.assertTrue(live.request_stop_speaking())
        self.assertEqual(len(calls), 1)
        live.set_stop_speaking_handler(None)
        self.assertFalse(live.request_stop_speaking())
        self.assertEqual(len(calls), 1)

    def test_barge_in_is_persisted_and_synced(self) -> None:
        import json
        import tempfile

        state = menu_state.MenuState()
        self.assertTrue(state.barge_in)  # activé par défaut
        state.barge_in = False
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "menu_state.json")
            menu_state.save_state(state, path)
            with open(path, "r", encoding="utf-8") as handle:
                self.assertIn("barge_in", json.load(handle))
            reloaded = menu_state.load_state(path)
        self.assertFalse(reloaded.barge_in)
        self.assertFalse(menu_state.LIVE.get_barge_in())
        # Remise à l'état par défaut pour les autres tests.
        menu_state.LIVE.set_barge_in(True)

    def test_legacy_state_file_enables_interruption(self) -> None:
        """Un menu_state.json d'avant la fonctionnalité reste valide."""
        import json
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "menu_state.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"tts_volume": 42, "mic_enabled": True}, handle)
            state = menu_state.load_state(path)
        self.assertTrue(state.barge_in)
        self.assertEqual(state.tts_volume, 42)


if __name__ == "__main__":
    unittest.main()
