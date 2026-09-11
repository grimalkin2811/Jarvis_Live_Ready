"""Tests de l'interface graphique du launcher (launcher/gui.py, PySide6).

Exécutés en plateforme ``offscreen`` (aucun affichage requis) et ignorés si
PySide6 est indisponible. Les appels réseau/processus sont simulés : seuls
l'affichage, l'orchestration des tâches de fond et les dialogues sont testés.
"""

import os
import sys
import time
import unittest
from unittest import mock

# Plateforme sans affichage AVANT tout import Qt.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    from PySide6.QtWidgets import QApplication, QMessageBox

    HAS_PYSIDE6 = True
except Exception:
    HAS_PYSIDE6 = False
    QApplication = None  # type: ignore[assignment]
    QMessageBox = None  # type: ignore[assignment]

if HAS_PYSIDE6:
    from launcher import core  # noqa: E402
    from launcher import gui  # noqa: E402


def _wait_until(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise TimeoutError("condition non remplie à temps")
        QApplication.processEvents()
        time.sleep(0.01)
    QApplication.processEvents()


@unittest.skipUnless(HAS_PYSIDE6, "PySide6 non installé")
class LauncherGuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = None

    def tearDown(self):
        if self.window is not None:
            try:
                self.window._stop_worker()
            except Exception:
                pass
            self.window.close()
            self.window.deleteLater()
            self.window = None
        QApplication.processEvents()

    def _make_window(self, **kwargs):
        status = core.LauncherStatus(
            local_version="1.1.1",
            install_ok=True,
            install_message="Installation OK",
            jarvis_running=False,
            install_path="/fake/install",
            app_path="/fake/install/app",
        )
        patcher = mock.patch("launcher.gui.core.get_status", return_value=status)
        patcher.start()
        self.addCleanup(patcher.stop)
        kwargs.setdefault("auto_check", False)
        self.window = gui.LauncherWindow(**kwargs)
        self.window.show()
        QApplication.processEvents()
        return self.window

    def test_window_shows_version_and_status(self):
        window = self._make_window()
        self.assertIn("1.1.1", window._version_badge.text())
        self.assertIn("1.1.1", window._status_text.text())
        self.assertTrue(window._launch_button.isEnabled())
        self.assertFalse(window._update_button.isEnabled())
        self.assertIn("Orbe", window._mode_combo.currentText())

    def test_invalid_install_disables_launch(self):
        status = core.LauncherStatus(
            local_version="1.1.1",
            install_ok=False,
            install_message="Installation corrompue",
            install_path="/fake",
            app_path="/fake/app",
        )
        with mock.patch("launcher.gui.core.get_status", return_value=status):
            window = gui.LauncherWindow(auto_check=False)
            self.window = window
            window.show()
            QApplication.processEvents()
        self.assertFalse(window._launch_button.isEnabled())
        self.assertIn("invalide", window._status_text.text().lower())

    def test_check_update_available_enables_update_button(self):
        window = self._make_window()
        plan = {"current": "1.1.1", "latest": "1.2.0", "update_available": True,
                "asset": {"name": "x"}}
        check = core.UpdateCheck(True, "Mise à jour disponible : 1.1.1 -> 1.2.0", plan=plan)
        with mock.patch("launcher.gui.core.check_update", return_value=check):
            window.check_updates()
            _wait_until(lambda: not window._busy)
        self.assertEqual(window._latest_label.text(), "1.2.0")
        self.assertTrue(window._update_button.isEnabled())
        self.assertIn("1.2.0", window._update_button.text())
        self.assertIn("disponible", window._status_text.text().lower())

    def test_check_up_to_date(self):
        window = self._make_window()
        check = core.UpdateCheck(True, "Vous avez déjà la dernière version (1.1.1).",
                                 plan={"update_available": False, "latest": "1.1.1"})
        with mock.patch("launcher.gui.core.check_update", return_value=check):
            window.check_updates()
            _wait_until(lambda: not window._busy)
        self.assertFalse(window._update_button.isEnabled())
        self.assertIn("dernière version", window._status_text.text().lower())

    def test_install_update_success(self):
        window = self._make_window()
        window._pending_plan = {"latest": "1.2.0"}
        window._update_button.setEnabled(True)
        result = core.UpdateResult(True, "Mise à jour vers 1.2.0 terminée.")
        new_status = core.LauncherStatus(
            local_version="1.2.0", install_ok=True, install_message="OK",
            install_path="/fake/install", app_path="/fake/install/app",
        )
        with mock.patch("launcher.gui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            with mock.patch("launcher.gui.QMessageBox.information") as info:
                with mock.patch("launcher.gui.core.install_update", return_value=result) as installer:
                    with mock.patch("launcher.gui.core.get_status", return_value=new_status):
                        window.install_update()
                        _wait_until(lambda: not window._busy)
        installer.assert_called_once()
        self.assertIn("1.2.0", window._version_badge.text())
        self.assertFalse(window._update_button.isEnabled())
        info.assert_called_once()

    def test_install_update_cancelled(self):
        window = self._make_window()
        window._pending_plan = {"latest": "1.2.0"}
        window._update_button.setEnabled(True)
        with mock.patch("launcher.gui.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
            with mock.patch("launcher.gui.core.install_update") as installer:
                window.install_update()
                QApplication.processEvents()
        installer.assert_not_called()
        self.assertIsNotNone(window._pending_plan)

    def test_install_update_failure_shows_error(self):
        window = self._make_window()
        window._pending_plan = {"latest": "1.2.0"}
        window._update_button.setEnabled(True)
        result = core.UpdateResult(False, "Échec de la mise à jour : réseau coupé")
        with mock.patch("launcher.gui.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
            with mock.patch("launcher.gui.QMessageBox.critical") as critical:
                with mock.patch("launcher.gui.core.install_update", return_value=result):
                    window.install_update()
                    _wait_until(lambda: not window._busy)
        critical.assert_called_once()
        self.assertIn("journal", window._status_text.text().lower())

    def test_launch_success_auto_quits(self):
        window = self._make_window()
        result = core.LaunchResult(True, "Jarvis lancé.")
        calls: list[tuple[int, object]] = []
        with mock.patch("launcher.gui.core.launch_jarvis", return_value=result):
            with mock.patch(
                "launcher.gui.QTimer.singleShot",
                side_effect=lambda msec, fn: calls.append((msec, fn)),
            ):
                window.launch_jarvis()
                _wait_until(lambda: not window._busy)
        self.assertIn("lancé", window._status_text.text().lower())
        self.assertEqual(len(calls), 1)
        msec, callback = calls[0]
        self.assertEqual(msec, 1500)
        # Le rappel programmé ferme le launcher.
        callback()
        QApplication.processEvents()
        self.assertTrue(window.isHidden() or not window.isVisible())

    def test_launch_failure_shows_error(self):
        window = self._make_window()
        result = core.LaunchResult(False, "Exécutable introuvable")
        with mock.patch("launcher.gui.core.launch_jarvis", return_value=result):
            with mock.patch("launcher.gui.QMessageBox.critical") as critical:
                window.launch_jarvis()
                _wait_until(lambda: not window._busy)
        critical.assert_called_once()
        args, _kwargs = critical.call_args
        self.assertIn("introuvable", args[2].lower())

    def test_close_blocked_while_busy(self):
        import threading

        window = self._make_window()
        release = threading.Event()

        def slow():
            release.wait(timeout=10)
            return core.UpdateCheck(True, "fini", plan={"update_available": False})

        with mock.patch("launcher.gui.core.check_update", side_effect=slow):
            window.check_updates()
            _wait_until(lambda: window._busy)
            with mock.patch("launcher.gui.QMessageBox.information") as info:
                window.close()
                QApplication.processEvents()
            info.assert_called_once()
            self.assertTrue(window.isVisible())
            release.set()
            _wait_until(lambda: not window._busy)
        window.close()
        QApplication.processEvents()
        self.assertTrue(window.isHidden() or not window.isVisible())

    def test_download_progress_updates_bar(self):
        window = self._make_window()
        window._set_busy(True)
        try:
            window._on_download_progress(50, 100)
            self.assertEqual(window._progress.value(), 50)
            window._on_download_progress(3 * 1024 * 1024, None)
            self.assertEqual(window._progress.minimum(), 0)
            self.assertEqual(window._progress.maximum(), 0)
        finally:
            window._set_busy(False)


if __name__ == "__main__":
    unittest.main()
