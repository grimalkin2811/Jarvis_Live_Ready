"""Catalogue prêt à l'emploi : migration, activation, vrais effets et horaires.

Aucun matériel, aucune API, aucun dossier utilisateur modifié. Même les
notifications et les mesures système passent par des doubles de test.
"""

import datetime as dt
import inspect
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import modes, notifications, routine_actions, routines, scheduler, tools
from src.routine_presets import builtin_routines
from src.timeparse import describe_schedule, parse_schedule


class FrozenClock(dt.datetime):
    current = dt.datetime(2026, 9, 7, 9, 0, 1)  # Lundi, heure locale fictive.

    @classmethod
    def now(cls, tz=None):
        return cls.current


class PresetTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "routines.json"
        self.database = Path(self.directory.name) / "schedule.db"
        previous_mode = modes._DEFAULT_MANAGER
        self.mode_manager = modes.JarvisModeManager(
            Path(self.directory.name) / "mode.json"
        )
        modes.set_default_mode_manager(self.mode_manager)
        self.addCleanup(modes.set_default_mode_manager, previous_mode)
        cleanup_patch = patch.object(
            modes.JarvisModeManager,
            "_close_processes",
            return_value={"success": True, "tente": False, "fermes": [], "ignores": []},
        )
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
        cleanup_patch.start()
        priority_patch.start()
        restore_priority_patch.start()
        self.addCleanup(cleanup_patch.stop)
        self.addCleanup(priority_patch.stop)
        self.addCleanup(restore_priority_patch.stop)
        routines.set_tool_registry(tools.TOOL_FUNCTIONS, tools.TOOL_DECLARATIONS)
        self.manager = routines.RoutineManager(self.path)
        self.scheduler = scheduler.Scheduler(
            self.database, routine_manager=self.manager
        )
        self.addCleanup(self.scheduler.stop)
        previous = scheduler._DEFAULT_SCHEDULER
        scheduler.set_default_scheduler(self.scheduler)
        self.addCleanup(scheduler.set_default_scheduler, previous)
        publish_patch = patch.object(notifications, "publish", return_value="test")
        self.publish = publish_patch.start()
        self.addCleanup(publish_patch.stop)
        FrozenClock.current = dt.datetime(2026, 9, 7, 9, 0, 1)

    def at(self, value):
        FrozenClock.current = dt.datetime.fromisoformat(value)
        with patch.object(scheduler.dt, "datetime", FrozenClock):
            self.scheduler._check_scheduled_routines()

    def executions(self, name):
        return self.manager.describe_routine(name)["executions"]


class PresetInstallationTests(PresetTestCase):
    def test_twelve_complete_disabled_presets_are_installed(self):
        result = self.manager.list_routines()
        self.assertEqual(result["count"], 12)
        self.assertTrue(all(not item["enabled"] for item in result["routines"]))
        self.assertEqual(self.manager.scheduled_routines(), [])
        self.publish.assert_not_called()
        self.assertEqual(len({item["preset_id"] for item in result["routines"]}), 12)

    def test_every_preset_uses_valid_tools_arguments_and_schedules(self):
        for preset in builtin_routines():
            with self.subTest(preset=preset["name"]):
                steps, errors = routines.parse_steps(preset["steps"])
                self.assertFalse(errors)
                self.assertTrue(steps)
                for step in steps:
                    self.assertNotIn(step["tool"], routines.FORBIDDEN_TOOLS)
                    inspect.signature(tools.TOOL_FUNCTIONS[step["tool"]]).bind(
                        **step["args"]
                    )
                self.assertEqual(parse_schedule(preset["schedule"]), preset["schedule"])

    def test_installation_is_idempotent_and_does_not_rewrite(self):
        before = self.path.read_bytes()
        self.manager.install_presets()
        reopened = routines.RoutineManager(self.path)
        self.assertEqual(reopened.list_routines()["count"], 12)
        self.assertEqual(self.path.read_bytes(), before)

    def test_activation_and_personal_changes_survive_restart(self):
        self.manager.update_routine("Hydratation", enabled=True)
        self.manager.update_routine(
            "Fin de journée", description="Mon texte", schedule="en semaine à 19h"
        )
        reopened = routines.RoutineManager(self.path)
        self.assertTrue(reopened.describe_routine("Hydratation")["active"])
        self.assertEqual(
            reopened.describe_routine("Fin de journée")["description"], "Mon texte"
        )
        self.assertEqual(
            reopened.describe_routine("Fin de journée")["planification"],
            "en semaine à 19:00",
        )
        reopened.update_routine("Hydratation", enabled=False)
        again = routines.RoutineManager(self.path)
        self.assertFalse(again.describe_routine("Hydratation")["active"])

    def test_deleted_preset_does_not_reappear(self):
        self.manager.delete_routine("Hydratation", confirm=True)
        reopened = routines.RoutineManager(self.path)
        self.assertEqual(reopened.list_routines()["count"], 11)
        self.assertFalse(reopened.describe_routine("Hydratation")["success"])

    def test_existing_custom_routines_and_homonyms_are_preserved(self):
        path = Path(self.directory.name) / "existing.json"
        old = routines.RoutineManager(path, include_presets=False)
        old.create_routine("Hydratation", "flip_coin()", description="Ne pas remplacer")
        old.run_routine("Hydratation")
        before = old.describe_routine("Hydratation")
        migrated = routines.RoutineManager(path)
        self.assertEqual(migrated.describe_routine("Hydratation"), before)
        self.assertEqual(migrated.list_routines()["count"], 13)
        preset = migrated.describe_routine("hydration")
        self.assertEqual(preset["nom"], "Hydratation (Jarvis)")
        self.assertFalse(preset["active"])

    def test_migration_keeps_raw_custom_fields_even_with_an_unavailable_tool(self):
        path = Path(self.directory.name) / "raw.json"
        custom = {
            "name": "Extension perso",
            "steps": [{"tool": "mon_extension", "args": {}}],
            "description": "À conserver",
            "schedule": "horaire personnalisé",
            "extra": {"couleur": "bleu"},
            "run_count": 7,
            "enabled": False,
        }
        path.write_text(
            json.dumps({"version": 1, "routines": [custom], "extra_root": "ok"}),
            encoding="utf-8",
        )
        routines.RoutineManager(path)
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(stored["routines"][0], custom)
        self.assertEqual(stored["extra_root"], "ok")

    def test_full_custom_store_still_gets_all_presets(self):
        path = Path(self.directory.name) / "full.json"
        old = routines.RoutineManager(path, include_presets=False)
        for index in range(routines.MAX_ROUTINES):
            self.assertTrue(
                old.create_routine(f"custom {index}", "flip_coin()")["success"]
            )
        migrated = routines.RoutineManager(path)
        self.assertEqual(migrated.list_routines()["count"], routines.MAX_ROUTINES + 12)
        self.assertFalse(
            migrated.create_routine("une de trop", "flip_coin()")["success"]
        )

    def test_disabled_manager_does_not_install_or_write(self):
        path = Path(self.directory.name) / "disabled.json"
        manager = routines.RoutineManager(path, enabled=False)
        self.assertFalse(manager.install_presets()["success"])
        self.assertFalse(path.exists())

    def test_corrupt_file_is_never_overwritten_by_migration(self):
        self.path.write_text("{non valide", encoding="utf-8")
        manager = routines.RoutineManager(self.path)
        self.assertIsNotNone(manager.last_error)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{non valide")

    def test_read_only_installation_reports_error(self):
        path = Path(self.directory.name) / "readonly.json"
        with patch.object(
            routines.os, "replace", side_effect=PermissionError("lecture seule")
        ):
            manager = routines.RoutineManager(path)
        self.assertIn("lecture seule", manager.last_error)
        self.assertFalse(path.exists())

    def test_ambiguous_name_never_activates_a_random_break(self):
        result = self.manager.update_routine("pause", enabled=True)
        self.assertFalse(result["success"])
        self.assertEqual(self.manager.scheduled_routines(), [])


class PresetExecutionTests(PresetTestCase):
    def test_all_twelve_execute_successfully_with_no_user_parameters(self):
        mode_presets = {"focus_mode", "game_mode"}
        with (
            patch.object(
                tools,
                "get_battery_status",
                return_value={
                    "success": True,
                    "pourcentage": 15,
                    "branche": False,
                },
            ),
            patch.object(
                tools,
                "get_disk_usage",
                return_value={
                    "success": True,
                    "libre_pourcent": 5,
                    "libre_go": 2.5,
                },
            ),
        ):
            for preset in builtin_routines():
                with self.subTest(preset=preset["name"]):
                    self.manager.update_routine(preset["name"], enabled=True)
                    result = self.manager.run_routine(preset["name"])
                    self.assertTrue(result["success"], result)
                    if preset["preset_id"] in mode_presets:
                        self.assertFalse(result["notification_handled"])
                        self.mode_manager.disable_mode()
                    else:
                        self.assertTrue(result["notification_handled"])
                    self.assertEqual(self.executions(preset["name"]), 1)
        self.assertEqual(self.publish.call_count, 10)
        for call in self.publish.call_args_list:
            self.assertTrue(call.args[0])
            self.assertTrue(call.args[1])
        self.assertEqual(self.scheduler.list_reminders()["count"], 0)

    def test_disabled_means_no_effect_even_for_manual_or_reminder_runs(self):
        result = self.manager.run_routine("Hydratation")
        self.assertFalse(result["success"])
        self.assertTrue(result["disabled"])
        self.assertEqual(self.executions("Hydratation"), 0)
        self.scheduler.add_reminder(
            "",
            when=(dt.datetime.now() + dt.timedelta(hours=1)).isoformat(),
            routine="Hydratation",
        )
        with self.scheduler._connect() as conn:
            row = conn.execute("SELECT * FROM reminders").fetchone()
        self.scheduler._fire_reminder(row)
        self.publish.assert_not_called()
        self.assertEqual(self.executions("Hydratation"), 0)

    def test_activation_alone_has_no_side_effect_and_dry_run_is_safe(self):
        self.assertTrue(
            self.manager.run_routine("Hydratation", dry_run=True)["success"]
        )
        self.manager.update_routine("Hydratation", enabled=True)
        self.publish.assert_not_called()
        self.assertEqual(self.executions("Hydratation"), 0)

    def test_scheduled_briefing_shows_real_message_not_generic_success(self):
        self.manager.update_routine("Briefing du matin", enabled=True)
        self.at("2026-09-07T09:00:01")
        self.publish.assert_called_once()
        self.assertEqual(self.publish.call_args.args[0], "Briefing du matin")
        self.assertIn("Aucun rappel", self.publish.call_args.args[1])
        self.at("2026-09-07T09:01:00")
        reopened = scheduler.Scheduler(self.database, routine_manager=self.manager)
        with patch.object(scheduler.dt, "datetime", FrozenClock):
            reopened._check_scheduled_routines()
        self.assertEqual(self.executions("Briefing du matin"), 1)

    def test_interval_window_days_and_no_catchup_burst(self):
        self.manager.update_routine("Pause visuelle", enabled=True)
        for date, count in [
            ("2026-09-07T09:19:59", 0),
            ("2026-09-07T09:20:00", 1),
            ("2026-09-07T09:24:00", 1),
            ("2026-09-07T09:40:00", 2),
            ("2026-09-07T16:10:00", 2),  # trop tard pour 16 h
            ("2026-09-07T16:21:00", 3),  # seulement la dernière échéance
            ("2026-09-07T17:40:00", 4),
            ("2026-09-07T17:46:00", 4),
            ("2026-09-12T10:00:00", 4),  # samedi
        ]:
            self.at(date)
            self.assertEqual(self.executions("Pause visuelle"), count, date)
        self.manager.update_routine("Pause visuelle", enabled=False)
        self.at("2026-09-14T10:00:00")
        self.assertEqual(self.executions("Pause visuelle"), 4)
        self.assertEqual(self.publish.call_count, 4)

    def test_disable_after_scheduler_snapshot_is_still_respected(self):
        self.manager.update_routine("Briefing du matin", enabled=True)
        snapshot = self.manager.scheduled_routines()
        self.manager.update_routine("Briefing du matin", enabled=False)
        with patch.object(self.manager, "scheduled_routines", return_value=snapshot):
            self.at("2026-09-07T09:00:01")
        self.publish.assert_not_called()

    def test_no_generic_notification_for_healthy_system(self):
        self.manager.update_routine("Espace disque", enabled=True)
        with patch.object(
            tools,
            "get_disk_usage",
            return_value={
                "success": True,
                "libre_pourcent": 80,
                "libre_go": 200,
            },
        ):
            self.at("2026-09-07T09:00:01")
        self.assertEqual(self.executions("Espace disque"), 1)
        self.publish.assert_not_called()


class LocalActionTests(PresetTestCase):
    def test_battery_missing_charging_and_healthy_are_silent(self):
        for status in [
            {"success": False, "error": "Aucune batterie"},
            {"success": True, "pourcentage": 5},
            {"success": True, "pourcentage": 20, "branche": True},
            {"success": True, "pourcentage": 21, "branche": False},
        ]:
            with patch.object(tools, "get_battery_status", return_value=status):
                result = routine_actions.check_battery_alert()
                self.assertTrue(result["success"])
                self.assertFalse(result["notification"])
        self.publish.assert_not_called()

    def test_battery_alert_cooldown_survives_restart(self):
        with (
            patch.object(
                tools,
                "get_battery_status",
                return_value={
                    "success": True,
                    "pourcentage": 20,
                    "branche": False,
                },
            ),
            patch.object(scheduler.dt, "datetime", FrozenClock),
        ):
            self.assertTrue(routine_actions.check_battery_alert()["notification"])
            self.assertFalse(routine_actions.check_battery_alert()["notification"])
            reopened = scheduler.Scheduler(self.database, routine_manager=self.manager)
            scheduler.set_default_scheduler(reopened)
            self.assertFalse(routine_actions.check_battery_alert()["notification"])
            FrozenClock.current += dt.timedelta(hours=1)
            self.assertTrue(routine_actions.check_battery_alert()["notification"])
        self.assertEqual(self.publish.call_count, 2)

    def test_disk_threshold_detects_user_drive_and_daily_throttling(self):
        with (
            patch.object(
                tools,
                "get_disk_usage",
                return_value={
                    "success": True,
                    "libre_pourcent": 10,
                    "libre_go": 5,
                },
            ) as disk,
            patch.object(scheduler.dt, "datetime", FrozenClock),
        ):
            self.assertFalse(routine_actions.check_disk_alert()["notification"])
            disk.assert_called_with(drive=os.path.expanduser("~"))
            disk.return_value["libre_pourcent"] = 9.9
            self.assertTrue(routine_actions.check_disk_alert()["notification"])
            FrozenClock.current += dt.timedelta(hours=23)
            self.assertFalse(routine_actions.check_disk_alert()["notification"])
            FrozenClock.current += dt.timedelta(hours=1)
            self.assertTrue(routine_actions.check_disk_alert()["notification"])

    def test_briefings_include_recurring_occurrences_and_ignore_cancelled(self):
        with patch.object(scheduler.dt, "datetime", FrozenClock):
            self.scheduler.add_reminder(
                "réunion", when="2026-09-07T10:00", recurrence="daily"
            )
            self.scheduler.add_reminder("dentiste", when="2026-09-08T11:00")
            cancelled = self.scheduler.add_reminder("annulé", when="2026-09-08T12:00")
            self.scheduler.cancel_reminder(cancelled["id"])
            result = routine_actions.show_reminder_briefing("tomorrow")
            self.assertEqual(result["nombre_rappels"], 2)
            self.assertIn("réunion", result["message"])
            self.assertIn("dentiste", result["message"])
            self.assertNotIn("annulé", result["message"])
            weekly = routine_actions.show_reminder_briefing("week")
            self.assertEqual(weekly["nombre_rappels"], 8)
        self.assertEqual(self.scheduler.list_reminders()["count"], 2)

    def test_disabled_reminders_are_not_reported_as_an_empty_agenda(self):
        self.scheduler.enabled = False
        result = routine_actions.show_reminder_briefing()
        self.assertFalse(result["success"])
        self.publish.assert_not_called()

    def test_unknown_briefing_and_empty_notification_are_refused(self):
        self.assertFalse(routine_actions.show_reminder_briefing("year")["success"])
        self.assertFalse(routine_actions.notify_user("  ")["success"])
        self.publish.assert_not_called()


class IntervalParsingTests(unittest.TestCase):
    def test_repeated_schedule_roundtrip_and_readable_description(self):
        schedule = {
            "time": "09:20",
            "end_time": "17:40",
            "interval_minutes": 20,
            "days": list(range(5)),
        }
        self.assertEqual(parse_schedule(schedule), schedule)
        self.assertEqual(
            describe_schedule(schedule),
            "en semaine, toutes les 20 min de 09:20 à 17:40",
        )

    def test_invalid_intervals_are_not_silently_downgraded(self):
        for changes in [
            {"interval_minutes": 0},
            {"interval_minutes": -10},
            {"interval_minutes": True},
            {"interval_minutes": "20"},
            {"interval_minutes": 1.5},
            {"interval_minutes": 1441},
            {"end_time": "08:00"},
            {"end_time": "25:00"},
            {"end_time": None},
        ]:
            schedule = {
                "time": "09:00",
                "end_time": "17:00",
                "interval_minutes": 20,
                **changes,
            }
            self.assertIsNone(parse_schedule(schedule), changes)
        self.assertIsNone(parse_schedule({"time": "09:00", "end_time": "17:00"}))
        self.assertIsNone(parse_schedule({"time": "09:00", "interval_minutes": 20}))


if __name__ == "__main__":
    unittest.main()

