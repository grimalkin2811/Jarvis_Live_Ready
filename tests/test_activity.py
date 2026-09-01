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
        self.assertIn("Site non autorise", entry["erreur"])

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
        self.assertIn("site=youtube", summary)

    def test_log_never_raises(self):
        broken = ActivityLog(os.path.join(self.directory.name, "nope", "x.db"), enabled=True)
        broken.available = False
        broken.log("open_application", {"application": "vscode"})  # ne doit pas lever


class TestBackup(unittest.TestCase):
    """La sauvegarde est testée avec des gestionnaires temporaires (aucun accès à ~/.jarvis)."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()

        from src.memory import MemoryManager, set_default_memory_manager
        from src.routines import RoutineManager, set_default_routine_manager
        from src.scheduler import Scheduler, set_default_scheduler
        from src.todo import TodoManager, set_default_todo_manager

        def path(name):
            return os.path.join(self.directory.name, name)

        self._resetters = [
            set_default_memory_manager,
            set_default_routine_manager,
            set_default_scheduler,
            set_default_todo_manager,
            set_default_activity_log,
        ]
        set_default_memory_manager(MemoryManager(path("memory.db")))
        set_default_routine_manager(RoutineManager(path("routines.json")))
        set_default_scheduler(Scheduler(path("schedule.db")))
        set_default_todo_manager(TodoManager(path("todo.db")))
        set_default_activity_log(ActivityLog(path("activity.db")))

    def tearDown(self):
        for reset in self._resetters:
            reset(None)
        self.directory.cleanup()

    def test_backup_creates_readable_json(self):
        from src import backup

        result = backup.create_backup(self.directory.name)
        self.assertTrue(result["success"], result)
        self.assertTrue(os.path.isfile(result["fichier"]))

        with open(result["fichier"], "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        for key in ("version", "genere_le", "memoire", "routines", "taches", "notes"):
            self.assertIn(key, payload)

        listing = backup.list_backups(self.directory.name)
        self.assertTrue(listing["success"])
        self.assertGreaterEqual(listing["count"], 1)

    def test_list_backups_on_empty_folder(self):
        from src import backup

        empty = os.path.join(self.directory.name, "vide")
        result = backup.list_backups(empty)
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)


if __name__ == "__main__":
    unittest.main()
