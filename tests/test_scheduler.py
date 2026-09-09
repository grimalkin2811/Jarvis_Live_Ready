"""Tests des rappels persistants et du déclenchement horaire des routines.

Chaque test utilise une base SQLite temporaire ; aucun thread de fond n'est
laissé actif et aucune action réelle n'est déclenchée sur le PC.

    python -m unittest discover tests
"""

import datetime as dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _ci_diag  # noqa: E402,F401  (diagnostic CI temporaire)

from src import routines  # noqa: E402
from src import scheduler as scheduler_module  # noqa: E402
from src import tools  # noqa: E402


def in_seconds(delta: int) -> str:
    return (dt.datetime.now() + dt.timedelta(seconds=delta)).strftime("%Y-%m-%dT%H:%M:%S")


class SchedulerTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        routines.set_tool_registry(tools.TOOL_FUNCTIONS, tools.TOOL_DECLARATIONS)
        self.routines = routines.RoutineManager(os.path.join(self._dir.name, "routines.json"), include_presets=False)
        self.scheduler = scheduler_module.Scheduler(
            os.path.join(self._dir.name, "schedule.db"),
            routine_manager=self.routines,
        )
        self.notifications = []
        self.scheduler.add_notify_hook(lambda title, message: self.notifications.append((title, message)))

    def tearDown(self):
        self.scheduler.stop()
        self._dir.cleanup()


class TestReminders(SchedulerTestCase):
    def test_add_and_list(self):
        added = self.scheduler.add_reminder("appeler Paul", when="demain à 9h")
        self.assertTrue(added["success"], added)
        self.assertEqual(added["texte"], "appeler Paul")

        listed = self.scheduler.list_reminders()
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["rappels"][0]["statut"], "pending")

    def test_deadline_extracted_from_sentence(self):
        added = self.scheduler.add_reminder("rappelle-moi d'arroser les plantes demain à 9h")
        self.assertTrue(added["success"], added)
        self.assertEqual(added["texte"], "arroser les plantes")

    def test_past_deadline_is_refused(self):
        result = self.scheduler.add_reminder("trop tard", when="2020-01-01T09:00")
        self.assertFalse(result["success"])
        self.assertIn("passée", result["error"])

    def test_unparseable_deadline_is_refused(self):
        result = self.scheduler.add_reminder("un truc", when="quand tu veux")
        self.assertFalse(result["success"])
        self.assertIn("incomprise", result["error"])

    def test_cancel_one(self):
        added = self.scheduler.add_reminder("appeler Paul", when="demain à 9h")
        cancelled = self.scheduler.cancel_reminder(added["id"])
        self.assertTrue(cancelled["success"])
        self.assertEqual(self.scheduler.list_reminders()["count"], 0)

    def test_cancel_all_requires_confirmation(self):
        self.scheduler.add_reminder("un", when="demain à 9h")
        self.scheduler.add_reminder("deux", when="demain à 10h")
        refused = self.scheduler.cancel_reminder()
        self.assertFalse(refused["success"])
        self.assertTrue(refused["confirmation_requise"])
        self.assertEqual(self.scheduler.list_reminders()["count"], 2)

        confirmed = self.scheduler.cancel_reminder(confirm=True)
        self.assertEqual(confirmed["annules"], 2)
        self.assertEqual(self.scheduler.list_reminders()["count"], 0)


class TestFiring(SchedulerTestCase):
    def test_due_reminder_fires_once(self):
        self.scheduler.add_reminder("sortir la poubelle", when=in_seconds(1))
        row = self._wait_due()
        self.scheduler._fire_reminder(row)

        self.assertEqual(self.notifications[-1][0], "Rappel")
        self.assertIn("sortir la poubelle", self.notifications[-1][1])
        # Le rappel ponctuel est terminé : il ne doit plus être en attente.
        self.assertEqual(self.scheduler.list_reminders()["count"], 0)

    def test_recurring_reminder_is_rescheduled(self):
        self.scheduler.add_reminder("réunion", when=in_seconds(1), recurrence="tous les jours")
        row = self._wait_due()
        self.scheduler._fire_reminder(row)

        pending = self.scheduler.list_reminders()
        self.assertEqual(pending["count"], 1)
        due = dt.datetime.strptime(pending["rappels"][0]["echeance_iso"], "%Y-%m-%dT%H:%M:%S")
        self.assertGreater(due, dt.datetime.now())

    def test_reminder_can_run_a_routine(self):
        self.routines.create_routine("demo", "flip_coin()")
        self.scheduler.add_reminder("", when=in_seconds(1), routine="demo")
        row = self._wait_due()
        self.scheduler._fire_reminder(row)

        self.assertEqual(self.notifications[-1][0], "Routine planifiée")
        self.assertEqual(self.routines.describe_routine("demo")["executions"], 1)

    def _wait_due(self):
        import time

        for _ in range(40):
            rows = self.scheduler._due_reminders()
            if rows:
                return rows[0]
            time.sleep(0.1)
        self.fail("Aucun rappel arrivé à échéance")


class TestScheduledRoutines(SchedulerTestCase):
    def test_routine_fires_within_grace_window(self):
        now = dt.datetime.now()
        self.routines.create_routine(
            "auto", "flip_coin()", schedule=f"tous les jours à {now.hour}h{now.minute:02d}"
        )
        self.scheduler._check_scheduled_routines()
        self.assertEqual(self.routines.describe_routine("auto")["executions"], 1)

        # Un second passage ne doit pas relancer la routine dans la même minute.
        self.scheduler._check_scheduled_routines()
        self.assertEqual(self.routines.describe_routine("auto")["executions"], 1)

    def test_routine_does_not_fire_outside_window(self):
        target = dt.datetime.now() - dt.timedelta(hours=3)
        self.routines.create_routine(
            "matin", "flip_coin()", schedule=f"tous les jours à {target.hour}h{target.minute:02d}"
        )
        self.scheduler._check_scheduled_routines()
        self.assertEqual(self.routines.describe_routine("matin")["executions"], 0)

    def test_disabled_routine_is_ignored(self):
        now = dt.datetime.now()
        self.routines.create_routine(
            "off", "flip_coin()", schedule=f"tous les jours à {now.hour}h{now.minute:02d}"
        )
        self.routines.update_routine("off", enabled=False)
        self.scheduler._check_scheduled_routines()
        self.assertEqual(self.routines.describe_routine("off")["executions"], 0)


class TestResilience(SchedulerTestCase):
    def test_disabled_scheduler_reports_clearly(self):
        disabled = scheduler_module.Scheduler(
            os.path.join(self._dir.name, "unused.db"), enabled=False
        )
        result = disabled.add_reminder("test", when="demain à 9h")
        self.assertFalse(result["success"])
        self.assertFalse(disabled.start())

    def test_start_is_idempotent(self):
        self.assertTrue(self.scheduler.start())
        self.assertTrue(self.scheduler.start())
        self.scheduler.stop()

    def test_status(self):
        self.scheduler.add_reminder("appeler Paul", when="demain à 9h")
        status = self.scheduler.status()
        self.assertTrue(status["success"])
        self.assertEqual(status["rappels_en_attente"], 1)


class TestToolRegistration(unittest.TestCase):
    def test_tools_are_declared(self):
        for name in ("set_reminder", "list_reminders", "cancel_reminder"):
            self.assertIn(name, tools.TOOL_FUNCTIONS)
            self.assertIn(name, {decl["name"] for decl in tools.TOOL_DECLARATIONS})


if __name__ == "__main__":
    unittest.main()
