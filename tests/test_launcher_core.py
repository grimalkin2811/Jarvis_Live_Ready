"""Tests de la logique du launcher (launcher/core.py) et du dispatch CLI/GUI.

Sans Qt : seule la logique métier et le parsing d'arguments sont testés ici
(voir test_launcher_gui.py pour l'interface graphique).
"""

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from launcher import core  # noqa: E402
from launcher import main as launcher_main  # noqa: E402


class InstallDirsTests(unittest.TestCase):
    def test_dev_dirs_point_to_repo(self):
        self.assertFalse(core.is_frozen())
        repo = Path(__file__).resolve().parents[1]
        self.assertEqual(core.install_dir(), repo)
        self.assertEqual(core.app_dir(), repo)

    def test_dev_validation_ok(self):
        result = core.validate_installation()
        self.assertTrue(result.ok)


class StatusTests(unittest.TestCase):
    def test_get_status_dev(self):
        status = core.get_status()
        self.assertTrue(status.install_ok)
        self.assertTrue(status.local_version)
        self.assertIn(".", status.local_version)
        self.assertFalse(status.jarvis_running)  # toujours faux hors Windows

    def test_local_version_fallback_bundled(self):
        with mock.patch("launcher.core.updater.local_version", return_value="9.9.9") as m:
            self.assertEqual(core.local_version(), "9.9.9")
            m.assert_called_once()


class CheckUpdateTests(unittest.TestCase):
    def test_offline_returns_clear_message(self):
        with mock.patch("launcher.core.updater.is_connected", return_value=False):
            check = core.check_update()
        self.assertFalse(check.ok)
        self.assertIn("connexion", check.message.lower())
        self.assertIsNone(check.plan)

    def test_update_available(self):
        plan = {
            "current": "1.1.1",
            "latest": "1.2.0",
            "update_available": True,
            "asset": {"name": "x"},
        }
        with mock.patch("launcher.core.updater.is_connected", return_value=True):
            with mock.patch("launcher.core.updater.build_update_plan", return_value=plan):
                with mock.patch("launcher.core.local_version", return_value="1.1.1"):
                    check = core.check_update()
        self.assertTrue(check.ok)
        self.assertTrue(check.update_available)
        self.assertEqual(check.latest, "1.2.0")

    def test_already_latest(self):
        plan = {"current": "1.1.1", "latest": "1.1.1", "update_available": False}
        with mock.patch("launcher.core.updater.is_connected", return_value=True):
            with mock.patch("launcher.core.updater.build_update_plan", return_value=plan):
                check = core.check_update()
        self.assertTrue(check.ok)
        self.assertFalse(check.update_available)

    def test_updater_error_wrapped(self):
        from src.updater import UpdateError

        with mock.patch("launcher.core.updater.is_connected", return_value=True):
            with mock.patch(
                "launcher.core.updater.build_update_plan", side_effect=UpdateError("boom")
            ):
                check = core.check_update()
        self.assertFalse(check.ok)
        self.assertIn("boom", check.message)

    def test_force_builds_reinstall_plan(self):
        release = {"assets": [{"name": "Jarvis-v1.1.1-portable.zip"}]}
        plan = {
            "current": "1.1.1",
            "latest": "1.1.1",
            "update_available": False,
            "release": release,
            "asset": None,
        }
        asset = {"name": "Jarvis-v1.1.1-portable.zip"}
        with mock.patch("launcher.core.updater.is_connected", return_value=True):
            with mock.patch("launcher.core.updater.build_update_plan", return_value=plan):
                with mock.patch("launcher.core.local_version", return_value="1.1.1"):
                    with mock.patch("launcher.core.updater.find_asset", return_value=asset):
                        with mock.patch(
                            "launcher.core.updater.find_asset_sha256", return_value=None
                        ):
                            check = core.check_update(force=True)
        self.assertTrue(check.ok)
        self.assertTrue(check.update_available)
        self.assertEqual(check.plan["asset"], asset)

    def test_force_without_asset_fails_clearly(self):
        plan = {"current": "1.1.1", "latest": "1.1.1", "update_available": False, "release": {}}
        with mock.patch("launcher.core.updater.is_connected", return_value=True):
            with mock.patch("launcher.core.updater.build_update_plan", return_value=plan):
                with mock.patch("launcher.core.local_version", return_value="1.1.1"):
                    with mock.patch("launcher.core.updater.find_asset", return_value=None):
                        check = core.check_update(force=True)
        self.assertFalse(check.ok)
        self.assertIn("artefact", check.message.lower())


class InstallUpdateTests(unittest.TestCase):
    def test_refuses_when_app_running(self):
        with mock.patch("launcher.core.updater.is_app_running", return_value=True):
            result = core.install_update({"latest": "1.2.0"})
        self.assertFalse(result.ok)
        self.assertIn("fermez", result.message.lower())

    def test_success_dev(self):
        plan = {"latest": "1.2.0"}
        with mock.patch("launcher.core.updater.is_app_running", return_value=False):
            with mock.patch("launcher.core.updater.perform_update", return_value=None) as perf:
                result = core.install_update(plan, progress=lambda r, t: None)
        self.assertTrue(result.ok)
        self.assertIn("1.2.0", result.message)
        perf.assert_called_once()
        # Le rappel de progression est transmis à perform_update.
        self.assertIsNotNone(perf.call_args.kwargs.get("progress"))

    def test_updater_error_wrapped(self):
        from src.updater import UpdateError

        with mock.patch("launcher.core.updater.is_app_running", return_value=False):
            with mock.patch(
                "launcher.core.updater.perform_update", side_effect=UpdateError("dl hs")
            ):
                result = core.install_update({"latest": "1.2.0"})
        self.assertFalse(result.ok)
        self.assertIn("dl hs", result.message)


class LaunchTests(unittest.TestCase):
    def test_unknown_mode_rejected(self):
        result = core.launch_jarvis("turbo")
        self.assertFalse(result.ok)

    def test_dev_launch_spawns_process(self):
        with mock.patch("launcher.core.subprocess.Popen") as popen:
            result = core.launch_jarvis("ui")
        self.assertTrue(result.ok)
        popen.assert_called_once()
        cmd = popen.call_args.args[0]
        self.assertIn("--ui", cmd)

    def test_dev_launch_desktop(self):
        with mock.patch("launcher.core.subprocess.Popen") as popen:
            result = core.launch_jarvis("desktop")
        self.assertTrue(result.ok)
        self.assertIn("--desktop", popen.call_args.args[0])

    def test_wait_mode_returns_code(self):
        completed = mock.Mock()
        completed.returncode = 3
        with mock.patch("launcher.core.subprocess.run", return_value=completed):
            result = core.launch_jarvis("console", wait=True)
        self.assertFalse(result.ok)
        self.assertEqual(result.returncode, 3)


class DispatchTests(unittest.TestCase):
    def _args(self, argv):
        return launcher_main.build_parser().parse_args(argv)

    def test_no_args_wants_gui(self):
        args = self._args([])
        self.assertTrue(launcher_main._wants_gui(args, []))

    def test_gui_flag_forces_gui(self):
        args = self._args(["--gui"])
        self.assertTrue(launcher_main._wants_gui(args, ["--gui"]))

    def test_action_flags_force_cli(self):
        for argv in (
            ["--check"],
            ["--validate"],
            ["--no-update"],
            ["--update-only"],
            ["--force"],
            ["--console"],
            ["--desktop"],
            ["--no-gui"],
            ["--prerelease"],
        ):
            with self.subTest(argv=argv):
                args = self._args(argv)
                self.assertFalse(launcher_main._wants_gui(args, argv), argv)

    def test_gui_wins_over_no_update(self):
        args = self._args(["--gui", "--no-update"])
        self.assertTrue(launcher_main._wants_gui(args, ["--gui", "--no-update"]))

    def test_cli_validate_dev(self):
        args = self._args(["--validate"])
        self.assertEqual(launcher_main._run_cli(args), 0)

    def test_cli_check_short_circuits(self):
        args = self._args(["--check"])
        check = core.UpdateCheck(True, "déjà à jour", plan={"update_available": False})
        with mock.patch("launcher.main.core.check_update", return_value=check):
            with mock.patch("launcher.main.core.launch_jarvis") as launch:
                rc = launcher_main._run_cli(args)
        self.assertEqual(rc, 0)
        launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
