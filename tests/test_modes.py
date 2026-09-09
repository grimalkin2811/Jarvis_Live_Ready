"""Tests des modes focus / jeu de Jarvis.

Les tests passent par TOOL_FUNCTIONS quand il faut vérifier le garde-fou réel
utilisé par Gemini Live et les routines.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import modes, notifications, scheduler, tools


class ModePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.previous_mode = modes._DEFAULT_MANAGER
        self.manager = modes.JarvisModeManager(Path(self.tmp.name) / "mode.json")
        modes.set_default_mode_manager(self.manager)
        self.addCleanup(modes.set_default_mode_manager, self.previous_mode)
        priority_patch = patch.object(
            modes.JarvisModeManager,
            "_set_jarvis_low_priority",
            return_value={"success": True, "applique": False},
        )
        restore_priority_patch = patch.object(
            modes.JarvisModeManager,
            "_restore_jarvis_priority",
            return_value={"success": True, "applique": False},
        )
        priority_patch.start()
        restore_priority_patch.start()
        self.addCleanup(priority_patch.stop)
        self.addCleanup(restore_priority_patch.stop)

    def test_focus_blocks_distractions_but_allows_study_sites(self) -> None:
        result = self.manager.activate_focus_mode(close_distractions=False)
        self.assertTrue(result["success"])

        blocked = tools.TOOL_FUNCTIONS["open_website"](site="youtube")
        self.assertFalse(blocked["success"])
        self.assertTrue(blocked["blocked_by_mode"])
        self.assertEqual(blocked["mode"], modes.MODE_FOCUS)

        alias_blocked = tools.TOOL_FUNCTIONS["open_website"](site="yt")
        self.assertFalse(alias_blocked["success"])
        self.assertTrue(alias_blocked["blocked_by_mode"])

        blocked_search = tools.TOOL_FUNCTIONS["web_search"](
            query="fortnite gameplay", engine="google"
        )
        self.assertFalse(blocked_search["success"])
        self.assertTrue(blocked_search["blocked_by_mode"])

        with patch.object(tools.webbrowser, "open", return_value=True) as opened:
            allowed = tools.TOOL_FUNCTIONS["open_website"](site="wikipedia")
        self.assertTrue(allowed["success"])
        opened.assert_called_once()

    def test_game_mode_blocks_screen_actions_and_keeps_safe_controls(self) -> None:
        result = self.manager.activate_game_mode(close_background=False)
        self.assertTrue(result["success"])

        blocked_app = tools.TOOL_FUNCTIONS["open_application"](application="notepad")
        self.assertFalse(blocked_app["success"])
        self.assertTrue(blocked_app["blocked_by_mode"])
        self.assertEqual(blocked_app["mode"], modes.MODE_GAME)

        blocked_capture = tools.TOOL_FUNCTIONS["take_screenshot"]()
        self.assertFalse(blocked_capture["success"])
        self.assertTrue(blocked_capture["blocked_by_mode"])

        # Une lecture locale inoffensive reste autorisée même en mode jeu.
        self.assertTrue(tools.TOOL_FUNCTIONS["get_local_time"]()["success"])
        self.assertEqual(tools.TOOL_FUNCTIONS["get_jarvis_mode"]()["mode"], modes.MODE_GAME)
        self.assertTrue(tools.TOOL_FUNCTIONS["disable_jarvis_mode"]()["success"])
        self.assertEqual(self.manager.current_mode(), modes.MODE_NORMAL)

    def test_game_mode_suppresses_scheduler_notifications(self) -> None:
        self.manager.activate_game_mode(close_background=False)
        db = Path(self.tmp.name) / "schedule.db"
        sched = scheduler.Scheduler(db)
        self.addCleanup(sched.stop)
        with patch.object(notifications, "publish", return_value="test") as publish:
            result = sched.notify("Test", "Message")
        self.assertTrue(result["success"])
        self.assertFalse(result["notification"])
        self.assertEqual(result["canal"], "suppressed:game_mode")
        publish.assert_not_called()

    def test_mode_expires_automatically(self) -> None:
        self.manager.activate_focus_mode(duration_minutes=1, close_distractions=False)
        self.assertEqual(self.manager.current_mode(), modes.MODE_FOCUS)
        # Simule un fichier/état expiré sans dormir.
        with self.manager._lock:
            self.manager._state["expires_at"] = "2000-01-01T00:00:00"
        self.assertEqual(self.manager.current_mode(), modes.MODE_NORMAL)


if __name__ == "__main__":
    unittest.main()

