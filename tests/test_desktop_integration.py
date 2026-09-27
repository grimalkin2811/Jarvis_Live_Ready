"""Intégration du Desktop Mode 1.7.0 dans Jarvis, et non-régression du Blob.

Vérifie les points de couture : menu radial, bascule Blob ↔ Desktop, pont
d'évènements, compatibilité de l'API historique — et l'absence d'effet de
bord sur le Blob Mode.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    from PySide6.QtWidgets import QApplication  # noqa: E402

    from UI import appearance_actions, jarvis_menu as jm  # noqa: E402
    from UI.desktop import config as cfg  # noqa: E402
    from UI.desktop.state import DesktopEvent, DesktopState  # noqa: E402
    from src import settings  # noqa: E402
    from src.ui import InterfaceModeController, SessionVisibility  # noqa: E402

    HAS_QT = True
except Exception:  # pragma: no cover
    HAS_QT = False
    QApplication = None  # type: ignore[assignment]


def _qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _appearance_spec():
    for spec in jm.MENU_SPECS:
        if spec.name == "Appearance":
            return spec
    raise AssertionError("menu Appearance introuvable")


@unittest.skipUnless(HAS_QT, "PySide6 indisponible")
class AppearanceMenuTests(unittest.TestCase):
    """L'entrée du menu radial respecte les contrats de position existants."""

    def test_desktop_entry_exists(self) -> None:
        labels = [item.label for item in _appearance_spec().items]
        self.assertIn("Desktop HUD", labels)

    def test_blob_visible_is_still_the_last_item(self) -> None:
        # Contrat de position hérité de la 1.3.2 : ne jamais le casser.
        self.assertEqual(_appearance_spec().items[-1].label, "Blob Visible")

    def test_desktop_entry_sits_before_blob_visible(self) -> None:
        labels = [item.label for item in _appearance_spec().items]
        self.assertLess(labels.index("Desktop HUD"), labels.index("Blob Visible"))

    def test_item_bg_opacity_is_still_before_blob_visible(self) -> None:
        labels = [item.label for item in _appearance_spec().items]
        self.assertLess(labels.index("Item BG Opacity"), labels.index("Blob Visible"))


@unittest.skipUnless(HAS_QT, "PySide6 indisponible")
class BlobMenuBehaviourTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["JARVIS_DATA_DIR"] = self.tmp.name
        self.addCleanup(lambda: os.environ.pop("JARVIS_DATA_DIR", None))
        self.widget = jm.MorphingOrbWidget()
        self.addCleanup(self.widget.deleteLater)

    def _item(self, label: str):
        spec = _appearance_spec()
        return spec, next(item for item in spec.items if item.label == label)

    def test_entry_reports_the_widget_count(self) -> None:
        value = self.widget._appearance_value("Desktop HUD")
        self.assertIn("élém.", value)

    def test_entry_opens_the_editor(self) -> None:
        spec, item = self._item("Desktop HUD")
        opened: list[int] = []
        self.widget._open_desktop_appearance_dialog = lambda: opened.append(1)
        self.widget._menu_callback_for(spec, item)()
        self.assertEqual(opened, [1])

    def test_entry_is_not_a_toggle_nor_a_slider(self) -> None:
        spec, item = self._item("Desktop HUD")
        self.assertFalse(self.widget._menu_is_slider(spec, item))
        self.assertFalse(self.widget._menu_is_option(spec, item))
        self.assertEqual(item.kind, "buttonless")

    def test_blob_visible_toggle_still_works(self) -> None:
        spec, item = self._item("Blob Visible")
        self.widget.appearance_state.blob_hidden = False
        self.assertTrue(self.widget._menu_toggle_value(spec, item))
        self.widget.appearance_state.blob_hidden = True
        self.assertFalse(self.widget._menu_toggle_value(spec, item))

    def test_item_bg_opacity_slider_still_works(self) -> None:
        spec, item = self._item("Item BG Opacity")
        self.assertTrue(self.widget._menu_is_slider(spec, item))
        self.widget._menu_set_slider(spec, item, 33)
        self.assertAlmostEqual(self.widget.appearance_state.item_bg_opacity, 0.33, places=2)

    def test_blob_appearance_state_carries_the_desktop_block(self) -> None:
        config = appearance_actions.desktop_config(self.widget.appearance_state)
        self.assertIsInstance(config, cfg.DesktopAppearanceConfig)


@unittest.skipUnless(HAS_QT, "PySide6 indisponible")
class InterfaceModeIntegrationTests(unittest.TestCase):
    """Bascule Blob ↔ Desktop : rien ne se perd, rien ne redémarre."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["JARVIS_DATA_DIR"] = self.tmp.name
        self.addCleanup(lambda: os.environ.pop("JARVIS_DATA_DIR", None))
        self.config_path = os.path.join(self.tmp.name, "config.json")
        self.state = appearance_actions.AppearanceState()
        self.visibility = SessionVisibility()
        self.controller = InterfaceModeController(
            self.state,
            self.visibility,
            initial_mode=settings.DESKTOP_MODE,
            config_path=self.config_path,
        )
        self.addCleanup(self.controller.close)

    def test_presence_router_is_the_new_controller(self) -> None:
        from UI.desktop.overlay import DesktopOverlayController
        from src.ui import PresenceRouter

        self.assertIs(PresenceRouter, DesktopOverlayController)

    def test_desktop_events_are_ignored_in_blob_mode(self) -> None:
        self.controller.set_mode(settings.BLOB_MODE)
        self.controller.handle_desktop_event(DesktopEvent.HOTWORD, {})
        # Aucun overlay ne doit être créé pour rien.
        self.assertIsNone(self.controller.overlay)

    def test_desktop_events_drive_the_overlay_in_desktop_mode(self) -> None:
        self.controller.set_mode(settings.DESKTOP_MODE)
        self.controller.handle_desktop_event(DesktopEvent.HOTWORD, {})
        self.assertIsNotNone(self.controller.overlay)
        self.assertEqual(
            self.controller.overlay.current_presence(), DesktopState.LISTENING
        )

    def test_switching_to_blob_resets_the_overlay_state(self) -> None:
        self.controller.set_mode(settings.DESKTOP_MODE)
        self.controller.handle_desktop_event(
            DesktopEvent.TRANSCRIPT, {"text": "bonjour Jarvis"}
        )
        overlay = self.controller.overlay
        self.assertTrue(overlay.transcript.text())
        self.controller.set_mode(settings.BLOB_MODE)
        self.assertEqual(overlay.current_presence(), DesktopState.HIDDEN)
        self.assertEqual(overlay.transcript.text(), "")

    def test_round_trip_keeps_the_same_widgets(self) -> None:
        self.controller.set_mode(settings.DESKTOP_MODE)
        overlay = self.controller.overlay
        blob_before = None
        self.controller.set_mode(settings.BLOB_MODE)
        blob_before = self.controller.blob
        self.controller.set_mode(settings.DESKTOP_MODE)
        self.assertIs(self.controller.overlay, overlay)
        self.controller.set_mode(settings.BLOB_MODE)
        self.assertIs(self.controller.blob, blob_before)

    def test_desktop_settings_survive_a_mode_round_trip(self) -> None:
        self.controller.set_mode(settings.DESKTOP_MODE)
        config = appearance_actions.desktop_config(self.state)
        config.set_slot(cfg.CONTROLS, enabled=True)
        config.interaction = cfg.INTERACTION_ALWAYS
        appearance_actions.save_state(self.state, self._appearance_path())
        self.controller.set_mode(settings.BLOB_MODE)
        self.controller.set_mode(settings.DESKTOP_MODE)
        reloaded = self.controller.overlay.config
        self.assertTrue(reloaded.slot(cfg.CONTROLS).enabled)
        self.assertEqual(reloaded.interaction, cfg.INTERACTION_ALWAYS)

    def _appearance_path(self) -> str:
        from src import paths

        return str(paths.appearance_state_file())

    def test_voice_energy_reaches_the_overlay(self) -> None:
        self.controller.set_mode(settings.DESKTOP_MODE)
        self.controller.handle_desktop_event(DesktopEvent.HOTWORD, {})
        self.controller.handle_voice_energy(0.9)
        self.assertGreater(self.controller.overlay._level_target, 0.5)

    def test_tray_show_gives_a_temporary_presence(self) -> None:
        """« Afficher Jarvis » n'allume pas le halo indéfiniment."""
        self.controller.set_mode(settings.DESKTOP_MODE)
        self.controller.activate_current()
        overlay = self.controller.overlay
        self.assertEqual(overlay.current_presence(), DesktopState.FOLLOW_UP)
        router = self.controller._presence_router
        self.assertTrue(router._listen_hide_timer.isActive())
        # …et la fenêtre se referme bien toute seule.
        router.machine.timeout()
        self.assertEqual(overlay.current_presence(), DesktopState.HIDDEN)

    def test_refresh_appearance_is_safe_without_overlay(self) -> None:
        self.controller.set_mode(settings.BLOB_MODE)
        self.controller.refresh_overlay_appearance()  # ne doit pas lever

    def test_presence_hook_compatibility(self) -> None:
        from UI.screen_halo_overlay import ScreenHaloOverlay, build_presence_hook

        overlay = ScreenHaloOverlay()
        self.addCleanup(overlay.deleteLater)
        self.addCleanup(overlay.close)
        hook = build_presence_hook(overlay)
        hook("listening")
        self.assertEqual(overlay.current_presence(), DesktopState.LISTENING)
        hook("speaking")
        self.assertEqual(overlay.current_presence(), DesktopState.SPEAKING)
        hook("hidden")
        self.assertEqual(overlay.current_presence(), DesktopState.HIDDEN)
        hook("valeur inconnue")  # ignorée, jamais d'exception
        self.assertEqual(overlay.current_presence(), DesktopState.HIDDEN)


@unittest.skipUnless(HAS_QT, "PySide6 indisponible")
class LazyImportTests(unittest.TestCase):
    """Le démarrage doit rester léger : les imports Qt restent paresseux."""

    def test_state_and_config_have_no_qt_dependency(self) -> None:
        import subprocess

        code = (
            "import sys;"
            "sys.path.insert(0, '.');"
            "import UI.desktop.state, UI.desktop.config, UI.desktop.events;"
            "print('PySide6' in sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."),
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.stdout.strip(), "False", result.stderr)

    def test_ui_package_exports_stay_lazy(self) -> None:
        import UI

        self.assertIn("DesktopOverlay", dir(UI))
        self.assertIn("ScreenHaloOverlay", dir(UI))


if __name__ == "__main__":
    unittest.main()
