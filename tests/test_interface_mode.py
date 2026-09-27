"""Tests v1.5.3 du switch rapide Blob Mode / Desktop Mode."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src import modes, settings, tools  # noqa: E402
from UI.interface_mode_bridge import INTERFACE_MODE  # noqa: E402


class InterfaceModeSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "config.json"

    def test_default_is_blob_when_no_choice_exists(self) -> None:
        self.assertEqual(settings.get_interface_mode(self.path), settings.BLOB_MODE)

    def test_select_blob_and_desktop(self) -> None:
        self.assertEqual(settings.set_interface_mode("blob", self.path), "blob")
        self.assertEqual(settings.get_interface_mode(self.path), "blob")
        self.assertEqual(settings.set_interface_mode("desktop", self.path), "desktop")
        self.assertEqual(settings.get_interface_mode(self.path), "desktop")

    def test_blob_persistence_after_reload(self) -> None:
        settings.set_interface_mode("blob", self.path)
        self.assertEqual(settings.load_app_config(self.path).interface_mode, "blob")

    def test_desktop_persistence_after_reload(self) -> None:
        settings.set_interface_mode("desktop", self.path)
        self.assertEqual(settings.load_app_config(self.path).interface_mode, "desktop")

    def test_all_required_transitions(self) -> None:
        for sequence in (
            ("blob", "desktop"),
            ("desktop", "blob"),
            ("blob", "desktop", "blob"),
        ):
            with self.subTest(sequence=sequence):
                for mode in sequence:
                    settings.set_interface_mode(mode, self.path)
                    self.assertEqual(settings.get_interface_mode(self.path), mode)

    def test_mode_update_preserves_existing_settings(self) -> None:
        payload = {"user": "Marius", "api_key": "secret", "custom_future_key": 42}
        settings.save_file_config(payload, self.path)
        settings.set_interface_mode("desktop", self.path)
        saved = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["user"], "Marius")
        self.assertEqual(saved["api_key"], "secret")
        self.assertEqual(saved["custom_future_key"], 42)
        self.assertEqual(saved["interface_mode"], "desktop")

    def test_historical_ui_alias_maps_to_blob(self) -> None:
        self.assertEqual(settings.set_interface_mode("ui", self.path), "blob")

    def test_invalid_mode_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            settings.set_interface_mode("turbo", self.path)


try:
    from PySide6.QtWidgets import QApplication

    from UI import appearance_actions, jarvis_menu as jm
    from src.ui import InterfaceModeController, SessionVisibility, _build_tray_icon

    HAS_QT = True
except Exception:
    QApplication = None  # type: ignore[assignment]
    HAS_QT = False


def _qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


@unittest.skipUnless(HAS_QT, "PySide6 indisponible")
class InterfaceModeMenuTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"JARVIS_DATA_DIR": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_system_control_displays_active_mode_and_switches_once(self) -> None:
        current = ["blob"]
        transitions: list[str] = []

        def select(mode: str) -> str:
            current[0] = mode
            transitions.append(mode)
            return mode

        widget = jm.MorphingOrbWidget(lambda: current[0], select)
        self.addCleanup(widget.deleteLater)
        system = next(spec for spec in jm.MENU_SPECS if spec.name == "System")
        item = next(item for item in system.items if item.label == "Interface Mode")
        self.assertEqual(item.kind, "chips")
        self.assertEqual(widget._system_value("Interface Mode"), "Blob")

        widget._menu_cycle_option(system, item)
        self.assertEqual(transitions, ["desktop"])
        self.assertEqual(widget._system_value("Interface Mode"), "Desktop")

        widget._menu_cycle_option(system, item)
        self.assertEqual(transitions, ["desktop", "blob"])
        self.assertEqual(widget._system_value("Interface Mode"), "Blob")


@unittest.skipUnless(HAS_QT, "PySide6 indisponible")
class InterfaceModeRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"JARVIS_DATA_DIR": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.config_path = Path(self.tmp.name) / "config.json"
        settings.save_app_config(
            settings.AppConfig(
                user="Test",
                api_key="x" * 24,
                interface_mode="blob",
            ),
            self.config_path,
        )
        self.controller = InterfaceModeController(
            appearance_actions.AppearanceState(),
            SessionVisibility(),
            initial_mode="blob",
            config_path=self.config_path,
        )
        self.addCleanup(self.controller.close)

    def test_hot_transitions_reuse_existing_blob_and_desktop_mechanisms(self) -> None:
        from UI.screen_halo_overlay import ScreenHaloOverlay

        self.controller.start()
        blob = self.controller.blob
        self.assertIsInstance(blob, jm.MorphingOrbWidget)
        self.assertTrue(blob.isVisible())
        self.assertTrue(blob.timer.isActive())

        self.controller.set_mode("desktop")
        QApplication.processEvents()
        self.assertEqual(self.controller.current_mode(), "desktop")
        self.assertFalse(blob.isVisible())
        self.assertFalse(blob.timer.isActive())
        self.assertIsInstance(self.controller.overlay, ScreenHaloOverlay)
        self.assertEqual(settings.get_interface_mode(self.config_path), "desktop")

        self.controller.handle_presence("speaking")
        QApplication.processEvents()
        self.assertEqual(self.controller.overlay.current_presence(), "speaking")

        self.controller.set_mode("blob")
        QApplication.processEvents()
        self.assertEqual(self.controller.current_mode(), "blob")
        self.assertIs(self.controller.blob, blob)
        self.assertTrue(blob.isVisible())
        self.assertTrue(blob.timer.isActive())
        self.assertEqual(self.controller.overlay.current_presence(), "hidden")
        self.assertEqual(settings.get_interface_mode(self.config_path), "blob")

    def test_blob_desktop_blob_sequence_stays_consistent(self) -> None:
        self.controller.start()
        for mode in ("desktop", "blob", "desktop", "blob"):
            self.controller.set_mode(mode)
            QApplication.processEvents()
            self.assertEqual(self.controller.current_mode(), mode)
            self.assertEqual(settings.get_interface_mode(self.config_path), mode)
            self.assertEqual(bool(self.controller.blob.isVisible()), mode == "blob")

    def test_tray_indicates_active_mode_and_switches(self) -> None:
        self.controller.start()
        with mock.patch(
            "PySide6.QtWidgets.QSystemTrayIcon.isSystemTrayAvailable",
            return_value=True,
        ):
            tray = _build_tray_icon(
                self.controller.activate_current,
                lambda: None,
                on_hide=self.controller.hide_current,
                mode_getter=self.controller.current_mode,
                on_mode_change=self.controller.set_mode,
            )
        self.assertIsNotNone(tray)
        self.addCleanup(tray.deleteLater)
        self.assertTrue(tray._interface_mode_actions["blob"].isChecked())
        tray._interface_mode_actions["desktop"].trigger()
        QApplication.processEvents()
        self.assertEqual(self.controller.current_mode(), "desktop")
        self.assertTrue(tray._interface_mode_actions["desktop"].isChecked())


class InterfaceModeVoiceCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"JARVIS_DATA_DIR": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        INTERFACE_MODE.set_handler(None)
        self.addCleanup(INTERFACE_MODE.set_handler, None)

    def test_voice_tool_persists_and_notifies_runtime(self) -> None:
        requested: list[str] = []
        INTERFACE_MODE.set_handler(requested.append)
        result = tools.TOOL_FUNCTIONS["set_interface_mode"](mode="desktop")
        self.assertTrue(result["success"])
        self.assertEqual(result["mode"], "desktop")
        self.assertEqual(settings.get_interface_mode(), "desktop")
        self.assertEqual(requested, ["desktop"])

        result = tools.TOOL_FUNCTIONS["set_interface_mode"](mode="blob")
        self.assertTrue(result["success"])
        self.assertEqual(settings.get_interface_mode(), "blob")
        self.assertEqual(requested, ["desktop", "blob"])

    def test_voice_tool_rejects_unknown_mode(self) -> None:
        result = tools.TOOL_FUNCTIONS["set_interface_mode"](mode="turbo")
        self.assertFalse(result["success"])

    def test_voice_tool_remains_available_in_focus_and_game_modes(self) -> None:
        self.assertIn("set_interface_mode", modes.MODE_CONTROL_TOOLS)


if __name__ == "__main__":
    unittest.main()
