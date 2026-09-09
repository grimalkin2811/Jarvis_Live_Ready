"""Commandes Qt et notifications testées offscreen, sans matériel ni réseau."""

import os
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from src import notifications, routines, scheduler
from UI import jarvis_menu
from UI.notification_bridge import NotificationBridge
from UI.routines_dialog import RoutinesDialog


class RoutinesDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "routines.json"
        self.manager = routines.RoutineManager(self.path)
        self.scheduler = scheduler.Scheduler(
            Path(self.directory.name) / "schedule.db", routine_manager=self.manager
        )
        self.addCleanup(self.scheduler.stop)
        self.dialog = RoutinesDialog(manager=self.manager, scheduler=self.scheduler)
        self.dialog.show()
        self.app.processEvents()
        self.addCleanup(self.close_dialog)

    def close_dialog(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()

    def test_all_twelve_presets_are_visible_in_scrollable_catalogue(self):
        self.assertEqual(len(self.dialog.rows), 12)
        self.assertFalse(self.dialog.grab().isNull())
        self.assertGreater(self.dialog.scroll.verticalScrollBar().maximum(), 0)
        last = self.dialog.rows["Espace disque"]["toggle"]
        self.dialog.scroll.ensureWidgetVisible(last)
        self.app.processEvents()
        self.assertTrue(last.isVisibleTo(self.dialog.scroll))
        self.assertFalse(last.isChecked())
        self.assertIn("0 / 12", self.dialog.summary.text())

    def test_toggle_by_keyboard_persists_without_running_routine(self):
        toggle = self.dialog.rows["Hydratation"]["toggle"]
        self.dialog.scroll.ensureWidgetVisible(toggle)
        toggle.setFocus()
        self.app.processEvents()
        with patch.object(self.manager, "run_routine") as run:
            QTest.keyClick(toggle, Qt.Key_Space)
            self.assertTrue(toggle.isChecked())
            reopened = routines.RoutineManager(self.path)
            self.assertTrue(reopened.describe_routine("Hydratation")["active"])
            self.assertIn("1 / 12", self.dialog.summary.text())
            QTest.keyClick(toggle, Qt.Key_Space)
            self.assertFalse(toggle.isChecked())
            self.assertFalse(reopened.describe_routine("Hydratation")["active"])
            run.assert_not_called()

    def test_voice_changes_refresh_toggle_without_triggering_an_update(self):
        self.manager.update_routine("Batterie faible", enabled=True)
        with patch.object(self.manager, "update_routine") as update:
            self.dialog.refresh()
            self.assertTrue(self.dialog.rows["Batterie faible"]["toggle"].isChecked())
            update.assert_not_called()

    def test_failed_write_rolls_toggle_back_and_reports_failure(self):
        toggle = self.dialog.rows["Hydratation"]["toggle"]
        with patch.object(self.manager, "_save", return_value=False):
            toggle.click()
        self.assertFalse(toggle.isChecked())
        self.assertFalse(self.manager.describe_routine("Hydratation")["active"])
        self.assertIn("non enregistrée", self.dialog.feedback.text())

    def test_global_disable_and_inactive_scheduler_are_explained(self):
        self.assertIn("planificateur", self.dialog.warning.text())
        self.manager.enabled = False
        self.dialog.refresh(force=True)
        self.assertFalse(self.dialog.rows)
        self.assertIn("désactivées globalement", self.dialog.warning.text())

    def test_custom_routines_beyond_six_remain_accessible(self):
        for index in range(8):
            self.manager.create_routine(f"Personnelle {index}", "flip_coin()")
        self.dialog.refresh(force=True)
        self.assertEqual(len(self.dialog.rows), 20)
        self.dialog.rows["Personnelle 7"]["toggle"].click()
        self.assertFalse(self.manager.describe_routine("Personnelle 7")["active"])

    def test_tray_menu_keeps_catalogue_action_alive(self):
        fake_audio = sys.modules.get("sounddevice", types.ModuleType("sounddevice"))
        with patch.dict(sys.modules, {"sounddevice": fake_audio}):
            from src.ui import _build_tray_icon
        with (
            patch.object(QSystemTrayIcon, "isSystemTrayAvailable", return_value=True),
            patch.object(QSystemTrayIcon, "show"),
            patch("UI.routines_dialog.show_routines_dialog") as show,
        ):
            tray = _build_tray_icon(lambda: None, lambda: None)
            self.assertIsNotNone(tray)
            action = next(
                action
                for action in tray._jarvis_menu.actions()
                if action.text() == "Routines…"
            )
            action.trigger()
            show.assert_called_once_with()
            tray.deleteLater()

    def test_orb_catalogue_entry_opens_the_panel_and_long_names_are_kept(self):
        long_name = "Ma routine personnelle avec un nom très long"
        self.manager.create_routine(long_name, "flip_coin()")
        previous = routines._DEFAULT_MANAGER
        routines.set_default_routine_manager(self.manager)
        self.addCleanup(routines.set_default_routine_manager, previous)
        spec = jarvis_menu.build_routines_spec()
        self.assertEqual(spec.items[0].label, "Catalogue")
        custom = next(item for item in spec.items if item.routine_name)
        self.assertEqual(custom.routine_name, long_name)
        self.assertEqual(len(custom.label), 22)
        widget = jarvis_menu.MorphingOrbWidget()
        widget._menu_state_path = str(Path(self.directory.name) / "menu.json")
        widget._appearance_state_path = str(Path(self.directory.name) / "appearance.json")
        widget._system_state_path = str(Path(self.directory.name) / "system.json")
        try:
            with patch("UI.routines_dialog.show_routines_dialog") as show:
                widget._menu_callback_for(spec, spec.items[0])()
                show.assert_called_once_with(widget)
            with patch.object(widget, "_run_routine_async") as run:
                widget._menu_callback_for(spec, custom)()
                run.assert_called_once_with(long_name)
        finally:
            widget.close()
            widget.deleteLater()


class NotificationBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tray = Mock()
        self.bridge = NotificationBridge(self.tray)
        self.addCleanup(self.bridge.close)
        self.addCleanup(self.bridge.deleteLater)

    def test_backend_notification_is_dispatched_to_gui_thread(self):
        seen_threads = []
        self.tray.showMessage.side_effect = lambda *args: seen_threads.append(threading.get_ident())
        main_thread = threading.get_ident()
        with patch.object(QSystemTrayIcon, "supportsMessages", return_value=True):
            thread = threading.Thread(
                target=lambda: notifications.publish("Hydratation", "Un verre d'eau ?")
            )
            thread.start()
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
            self.tray.showMessage.assert_not_called()
            self.app.processEvents()
        self.assertEqual(seen_threads, [main_thread])
        self.assertEqual(
            self.tray.showMessage.call_args.args[:2],
            ("Hydratation", "Un verre d'eau ?"),
        )

    def test_simultaneous_notifications_are_queued(self):
        with patch.object(QSystemTrayIcon, "supportsMessages", return_value=True):
            self.bridge.received.emit("Première", "Un")
            self.bridge.received.emit("Deuxième", "Deux")
            self.app.processEvents()
            self.assertEqual(self.tray.showMessage.call_count, 1)
            self.assertEqual(len(self.bridge.pending), 1)
            self.bridge._timer.stop()
            self.bridge._show_next()
            self.assertEqual(self.tray.showMessage.call_count, 2)
            self.assertEqual(self.tray.showMessage.call_args.args[0], "Deuxième")

    def test_popup_fallback_is_plain_text_and_does_not_take_focus(self):
        self.bridge.tray = None
        self.bridge.received.emit("Jarvis", "Un texte <b>non interprété</b>")
        self.app.processEvents()
        popup = self.bridge.popup
        self.assertIsNotNone(popup)
        self.assertEqual(popup.textFormat(), Qt.PlainText)
        self.assertTrue(popup.testAttribute(Qt.WA_ShowWithoutActivating))
        self.assertIn("<b>", popup.text())
        self.assertFalse(popup.grab().isNull())

    def test_close_unregisters_hook_and_ignores_already_queued_signals(self):
        self.bridge.received.emit("Jarvis", "En attente")
        hook = self.bridge._hook
        self.bridge.close()
        self.app.processEvents()
        self.assertNotIn(hook, notifications._HOOKS)
        self.tray.showMessage.assert_not_called()
        self.assertIsNone(self.bridge.popup)


if __name__ == "__main__":
    unittest.main()


import _ci_diag  # noqa: E402,F401  (diagnostic CI temporaire)