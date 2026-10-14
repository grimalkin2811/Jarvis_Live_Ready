"""Tests du journal d'activité local et de la sauvegarde JSON.

    python -m unittest discover tests
"""

import datetime as dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import activity as activity_module  # noqa: E402
from src.activity import ActivityLog, set_default_activity_log  # noqa: E402


class TestActivityLog(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.log = ActivityLog(os.path.join(self.directory.name, "activity.db"))

    def tearDown(self):
        set_default_activity_log(None)
        self.directory.cleanup()

    def test_log_and_query(self):
        self.log.log("open_application", {"application": "vscode"})
        self.log.log("set_volume", {"volume": 30})

        result = self.log.query("aujourd'hui")
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 2)
        outils = [entry["outil"] for entry in result["actions"]]
        self.assertIn("open_application", outils)
        self.assertEqual(result["actions"][-1]["action"], "ouvert l'application")

    def test_ignored_tools_are_not_logged(self):
        self.log.log("get_local_time", {})
        self.log.log("get_activity_log", {})
        self.assertEqual(self.log.query("aujourd'hui")["count"], 0)

    def test_failures_are_recorded(self):
        self.log.log("open_website", {"site": "inconnu"}, success=False, error="Site non autorise")
        entry = self.log.query("aujourd'hui")["actions"][0]
        self.assertFalse(entry["reussi"])
        self.assertNotIn("erreur", entry)

    def test_yesterday_is_empty(self):
        self.log.log("take_screenshot", {})
        self.assertEqual(self.log.query("hier")["count"], 0)

    def test_unknown_day(self):
        self.assertFalse(self.log.query("un jour quelconque")["success"])

    def test_resolve_day_accepts_dates(self):
        today = dt.date.today()
        self.assertEqual(ActivityLog._resolve_day("aujourd'hui"), today)
        self.assertEqual(ActivityLog._resolve_day(today.strftime("%d/%m/%Y")), today)

    def test_clear_requires_confirmation(self):
        self.log.log("open_application", {"application": "chrome"})
        self.assertFalse(self.log.clear()["success"])
        self.assertTrue(self.log.clear(confirm=True)["success"])
        self.assertEqual(self.log.query("aujourd'hui")["count"], 0)

    def test_summarize_arguments_hides_confirmations(self):
        summary = activity_module.summarize_arguments({"confirm": True, "site": "youtube"})
        self.assertNotIn("confirm", summary)
        self.assertEqual(summary, "")

    def test_log_never_raises(self):
        broken = ActivityLog(os.path.join(self.directory.name, "nope", "x.db"), enabled=True)
        broken.available = False
        broken.log("open_application", {"application": "vscode"})  # ne doit pas lever


def test_no_private_data_persistence_retention(tmp_path):
    log = ActivityLog(tmp_path / "activity.db")
    secret = "private-token-do-not-store"
    log.log("write_to_active_field", {"text": secret}, False, secret)
    assert secret not in json.dumps(log.export())
    with log._connect() as conn:
        conn.execute("UPDATE activity SET at='2000-01-01T00:00:00'")
    log.log("set_volume", {})
    assert len(log.export()) == 1
    for value in ("true", 1, False):
        assert not log.clear(value)["success"]


def test_guard_and_routine_policy(tmp_path, monkeypatch):
    from unittest.mock import Mock
    from src import tools, routines, todo
    manager = todo.TodoManager(tmp_path / "todo.db")
    log = ActivityLog(tmp_path / "activity.db")
    monkeypatch.setattr(tools, "get_default_todo_manager", lambda: manager)
    monkeypatch.setattr(activity_module, "get_default_activity_log", lambda: log)
    mode = Mock()
    mode.block_for_tool.return_value = {"success": False, "error": "blocked"}
    monkeypatch.setattr(tools, "get_default_mode_manager", lambda: mode)
    assert not tools.TOOL_FUNCTIONS["add_todo"]("private text")["success"]
    assert manager.list_tasks()["count"] == 0
    assert log.query()["count"] == 0
    mode.block_for_tool.return_value = None
    assert tools.TOOL_FUNCTIONS["add_todo"]("private text")["success"]
    assert log.query()["count"] == 1
    assert "private text" not in json.dumps(log.export())
    for name in ("clear_todos", "delete_todo", "clear_activity_log"):
        assert name in routines.FORBIDDEN_TOOLS


def test_default_activity_is_opt_in(monkeypatch):
    monkeypatch.delenv("JARVIS_ACTIVITY_ENABLED", raising=False)
    monkeypatch.setattr(activity_module, "_DEFAULT_LOG", None)
    assert not activity_module.get_default_activity_log().enabled


def test_mode_policy_failure_denies_execution(monkeypatch):
    from unittest.mock import Mock
    from src import tools
    callback = Mock()
    monkeypatch.setattr(tools, "get_default_mode_manager", Mock(side_effect=RuntimeError("unavailable")))
    result = tools._guard_tool_function("test", callback)()
    assert not result["success"]
    callback.assert_not_called()
