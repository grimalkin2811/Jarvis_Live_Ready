"""Tests des routines de Jarvis.

Les routines sont écrites dans un fichier temporaire : aucun fichier utilisateur
n'est touché, et les étapes exécutées sont des outils inoffensifs
(``get_local_time``, ``flip_coin``…) qui n'agissent pas sur le PC.

    python -m unittest discover tests
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import routines  # noqa: E402
from src import tools  # noqa: E402


class RoutineTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "routines.json")
        self.manager = routines.RoutineManager(self.path)
        routines.set_tool_registry(tools.TOOL_FUNCTIONS, tools.TOOL_DECLARATIONS)

    def tearDown(self):
        self._dir.cleanup()


class TestStepParsing(RoutineTestCase):
    def test_dsl_positional_and_named(self):
        steps, errors = routines.parse_steps("set_volume(30); get_local_time()")
        self.assertEqual(errors, [])
        self.assertEqual(steps[0], {"tool": "set_volume", "args": {"volume": 30}})
        self.assertEqual(steps[1], {"tool": "get_local_time", "args": {}})

        steps, _ = routines.parse_steps("web_search(query=chat, engine=qwant)")
        self.assertEqual(steps[0]["args"], {"query": "chat", "engine": "qwant"})

    def test_json_input(self):
        payload = json.dumps([{"tool": "set_volume", "args": {"volume": 20}}])
        steps, errors = routines.parse_steps(payload)
        self.assertEqual(errors, [])
        self.assertEqual(steps[0]["args"], {"volume": 20})

    def test_wait_is_clamped(self):
        steps, _ = routines.parse_steps("wait(9999)")
        self.assertEqual(steps[0]["args"]["seconds"], routines.MAX_WAIT_SECONDS)

    def test_forbidden_and_unknown_tools(self):
        _, errors = routines.parse_steps("shutdown_pc()")
        self.assertTrue(any("interdit" in error for error in errors))
        _, errors = routines.parse_steps("fais_le_cafe()")
        self.assertTrue(any("inconnu" in error for error in errors))

    def test_no_recursion(self):
        self.assertIn("run_routine", routines.FORBIDDEN_TOOLS)

    def test_quoted_argument_keeps_separator(self):
        steps, _ = routines.parse_steps("take_note('acheter du pain; du lait')")
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0]["args"]["text"], "acheter du pain; du lait")


class TestRoutineLifecycle(RoutineTestCase):
    def test_create_list_describe(self):
        created = self.manager.create_routine(
            "mode travail",
            "get_local_time(); flip_coin()",
            description="Session de code",
            schedule="en semaine à 9h",
        )
        self.assertTrue(created["success"], created)
        self.assertEqual(created["nombre_etapes"], 2)

        listed = self.manager.list_routines()
        self.assertEqual(listed["count"], 1)
        self.assertEqual(listed["routines"][0]["planification"], "en semaine à 09:00")

        described = self.manager.describe_routine("mode travail")
        self.assertTrue(described["success"])
        self.assertEqual(described["description"], "Session de code")

    def test_name_matching_is_tolerant(self):
        self.manager.create_routine("mode travail", "flip_coin()")
        for spoken in ("Mode Travail", "mode  travail", "le mode travail"):
            self.assertTrue(self.manager.describe_routine(spoken)["success"], spoken)

    def test_duplicate_is_rejected(self):
        self.manager.create_routine("cinema", "flip_coin()")
        again = self.manager.create_routine("Cinema", "flip_coin()")
        self.assertFalse(again["success"])
        self.assertTrue(again.get("existe"))

    def test_forbidden_tool_rejects_creation(self):
        """Une étape interdite doit faire échouer, pas être retirée en silence."""
        result = self.manager.create_routine("danger", "shutdown_pc(); flip_coin()")
        self.assertFalse(result["success"])
        self.assertIn("shutdown_pc", result["error"])
        self.assertEqual(self.manager.list_routines()["count"], 0)

    def test_update(self):
        self.manager.create_routine("nuit", "flip_coin()")
        updated = self.manager.update_routine("nuit", steps="get_local_time()", enabled=False)
        self.assertTrue(updated["success"])
        self.assertFalse(updated["active"])
        self.assertIn("get_local_time", updated["etapes"])

    def test_schedule_can_be_removed(self):
        self.manager.create_routine("nuit", "flip_coin()", schedule="tous les jours à 22h")
        cleared = self.manager.update_routine("nuit", schedule="aucune")
        self.assertEqual(cleared["planification"], "aucune")

    def test_delete_requires_confirmation(self):
        self.manager.create_routine("temp", "flip_coin()")
        refused = self.manager.delete_routine("temp")
        self.assertFalse(refused["success"])
        self.assertTrue(refused["confirmation_requise"])
        self.assertEqual(self.manager.list_routines()["count"], 1)

        confirmed = self.manager.delete_routine("temp", confirm=True)
        self.assertTrue(confirmed["success"])
        self.assertEqual(self.manager.list_routines()["count"], 0)

    def test_missing_routine(self):
        result = self.manager.run_routine("inexistante")
        self.assertFalse(result["success"])
        self.assertIn("introuvable", result["error"])


class TestRoutineExecution(RoutineTestCase):
    def test_run_counts_steps(self):
        self.manager.create_routine("demo", "get_local_time(); wait(0); flip_coin()")
        result = self.manager.run_routine("demo")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["etapes_reussies"], 3)
        self.assertEqual(result["echecs"], [])

    def test_run_updates_statistics(self):
        self.manager.create_routine("demo", "flip_coin()")
        self.manager.run_routine("demo")
        described = self.manager.describe_routine("demo")
        self.assertEqual(described["executions"], 1)
        self.assertIsNotNone(described["derniere_execution"])

    def test_dry_run_changes_nothing(self):
        self.manager.create_routine("demo", "flip_coin()")
        result = self.manager.run_routine("demo", dry_run=True)
        self.assertTrue(result["simulation"])
        self.assertEqual(self.manager.describe_routine("demo")["executions"], 0)

    def test_failing_step_does_not_raise(self):
        """Un outil en échec est rapporté, il n'interrompt jamais Jarvis."""
        self.manager.create_routine("mixte", "open_application(application_inexistante); flip_coin()")
        result = self.manager.run_routine("mixte")
        self.assertFalse(result["success"])
        self.assertEqual(len(result["echecs"]), 1)
        self.assertEqual(result["etapes_reussies"], 1)

    def test_run_hook_is_called(self):
        seen = []
        self.manager.set_run_hook(lambda name, result: seen.append(name))
        self.manager.create_routine("demo", "flip_coin()")
        self.manager.run_routine("demo")
        self.assertEqual(seen, ["demo"])


class TestPersistence(RoutineTestCase):
    def test_file_is_human_readable(self):
        self.manager.create_routine("demo", "set_volume(30)", schedule="tous les jours à 9h")
        with open(self.path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["version"], 1)
        routine = payload["routines"][0]
        self.assertEqual(routine["name"], "demo")
        self.assertEqual(routine["steps"], [{"tool": "set_volume", "args": {"volume": 30}}])
        self.assertEqual(routine["schedule"], {"time": "09:00", "days": [0, 1, 2, 3, 4, 5, 6]})

    def test_hand_edited_file_is_reloaded(self):
        """Le fichier doit rester modifiable à la main."""
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": 1,
                    "routines": [
                        {
                            "name": "manuelle",
                            "steps": ["flip_coin()", {"tool": "get_local_time"}],
                            "schedule": "tous les jours à 7h",
                        }
                    ],
                },
                handle,
            )
        described = self.manager.describe_routine("manuelle")
        self.assertTrue(described["success"])
        self.assertEqual(described["nombre_etapes"], 2)
        self.assertEqual(described["planification"], "tous les jours à 07:00")

    def test_corrupted_file_does_not_crash(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{ ceci n'est pas du JSON")
        result = self.manager.list_routines()
        self.assertTrue(result["success"])
        self.assertEqual(result["count"], 0)

    def test_scheduled_routines_filtering(self):
        self.manager.create_routine("planifiee", "flip_coin()", schedule="tous les jours à 9h")
        self.manager.create_routine("manuelle", "flip_coin()")
        self.manager.create_routine("inactive", "flip_coin()", schedule="tous les jours à 9h")
        self.manager.update_routine("inactive", enabled=False)
        names = [item["name"] for item in self.manager.scheduled_routines()]
        self.assertEqual(names, ["planifiee"])


class TestToolRegistration(unittest.TestCase):
    def test_tools_are_declared(self):
        for name in (
            "create_routine",
            "run_routine",
            "list_routines",
            "describe_routine",
            "update_routine",
            "delete_routine",
            "list_routine_tools",
        ):
            self.assertIn(name, tools.TOOL_FUNCTIONS)

    def test_forbidden_tools_exist(self):
        """Les outils interdits doivent exister, sinon la liste est obsolète."""
        for name in routines.FORBIDDEN_TOOLS:
            self.assertIn(name, tools.TOOL_FUNCTIONS, name)

    def test_available_tools_excludes_forbidden(self):
        available = set(routines.available_tools())
        self.assertFalse(available & routines.FORBIDDEN_TOOLS)
        self.assertIn("open_application", available)
        self.assertIn(routines.WAIT_TOOL, available)


if __name__ == "__main__":
    unittest.main()
