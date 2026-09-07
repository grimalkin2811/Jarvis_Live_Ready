"""Tests des routines préconfigurées (presets).

Objectif : garantir qu'un preset ajouté au catalogue est **réellement
exécutable** — outils connus, arguments valides, applications/sites/dossiers
autorisés — et que l'installation ne piétine jamais le travail de
l'utilisateur.

    python -m unittest discover tests
"""

import datetime
import inspect
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import routine_presets  # noqa: E402
from src import routines  # noqa: E402
from src import scheduler  # noqa: E402
from src import tools  # noqa: E402
from src.timeparse import parse_schedule  # noqa: E402


class PresetCatalogTestCase(unittest.TestCase):
    """Le catalogue lui-même : forme, sécurité et validité des étapes."""

    def test_catalog_size_is_reasonable(self):
        names = routine_presets.preset_names()
        self.assertTrue(5 <= len(names) <= 15, f"catalogue de {len(names)} routines")
        self.assertEqual(len(names), len(set(names)), "noms de presets dupliqués")

    def test_presets_are_well_formed(self):
        for preset in routine_presets.PRESETS:
            with self.subTest(preset=preset["name"]):
                self.assertTrue(preset["name"].strip())
                self.assertLessEqual(len(preset["name"]), 60)
                self.assertTrue(preset["description"].strip())
                # Rien ne doit se déclencher sans l'accord de l'utilisateur.
                self.assertFalse(preset["enabled"])
                self.assertTrue(preset["steps"])
                self.assertLessEqual(len(preset["steps"]), routines.MAX_STEPS)

    def test_every_preset_has_a_usable_schedule(self):
        """Sans planification, activer/désactiver n'aurait aucun effet."""
        for preset in routine_presets.PRESETS:
            with self.subTest(preset=preset["name"]):
                schedule = parse_schedule(preset["schedule"])
                self.assertIsNotNone(schedule)
                self.assertEqual(schedule["time"], preset["schedule"]["time"])
                self.assertEqual(schedule["days"], sorted(preset["schedule"]["days"]))
                self.assertTrue(set(schedule["days"]).issubset(set(range(7))))

    def test_steps_only_use_declared_tools(self):
        declarations = {decl["name"]: decl for decl in tools.TOOL_DECLARATIONS}
        for preset in routine_presets.PRESETS:
            for step in preset["steps"]:
                with self.subTest(preset=preset["name"], outil=step["tool"]):
                    tool = step["tool"]
                    self.assertNotIn(tool, routines.FORBIDDEN_TOOLS)
                    self.assertTrue(tool == routines.WAIT_TOOL or tool in declarations)

    def test_step_arguments_match_the_declarations(self):
        """Chaque argument existe, est du bon nom, et les requis sont fournis."""
        declarations = {decl["name"]: decl for decl in tools.TOOL_DECLARATIONS}
        for preset in routine_presets.PRESETS:
            for step in preset["steps"]:
                tool = step["tool"]
                if tool == routines.WAIT_TOOL:
                    seconds = step["args"].get("seconds", 0)
                    self.assertGreater(seconds, 0)
                    self.assertLessEqual(seconds, routines.MAX_WAIT_SECONDS)
                    continue
                with self.subTest(preset=preset["name"], outil=tool):
                    params = declarations[tool]["parameters"]
                    properties = set(params["properties"])
                    required = set(params["required"])
                    provided = set(step["args"])
                    self.assertTrue(required.issubset(provided), f"{tool} : argument requis manquant")
                    self.assertTrue(provided.issubset(properties), f"{tool} : argument inconnu")
                    # Les noms d'arguments doivent être ceux de la fonction Python.
                    signature = inspect.signature(tools.TOOL_FUNCTIONS[tool])
                    signature.bind(**step["args"])

    def test_whitelisted_targets_resolve(self):
        """Applications, sites et dossiers cités existent dans les listes blanches."""
        checks = {
            "open_application": lambda value: tools._lookup(tools.APPS, value)[1],
            "close_application": lambda value: tools._lookup(tools.APPS, value)[1],
            "open_website": lambda value: tools._lookup(tools.SITES, value, tools.SITE_ALIASES)[1],
            "open_folder": lambda value: tools._resolve_folder(value)[1],
        }
        for preset in routine_presets.PRESETS:
            for step in preset["steps"]:
                check = checks.get(step["tool"])
                if check is None:
                    continue
                with self.subTest(preset=preset["name"], outil=step["tool"]):
                    value = next(iter(step["args"].values()), None)
                    self.assertTrue(check(value), f"{step['tool']}({value!r}) hors liste blanche")

    def test_preset_steps_parse_without_error(self):
        """Le parseur de routines (celui de la voix) accepte chaque étape."""
        routines.set_tool_registry(tools.TOOL_FUNCTIONS, tools.TOOL_DECLARATIONS)
        for preset in routine_presets.PRESETS:
            with self.subTest(preset=preset["name"]):
                steps, errors = routines.parse_steps(preset["steps"])
                self.assertEqual(routines.fatal_errors(errors), [])
                self.assertEqual(len(steps), len(preset["steps"]))


class PresetInstallTestCase(unittest.TestCase):
    """Installation dans le fichier de routines de l'utilisateur."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "routines.json")
        self.manager = routines.RoutineManager(self.path)
        routines.set_tool_registry(tools.TOOL_FUNCTIONS, tools.TOOL_DECLARATIONS)

    def tearDown(self):
        self._dir.cleanup()

    def _names(self):
        result = self.manager.list_routines()
        self.assertTrue(result["success"], result)
        return [item["name"] for item in result["routines"]]

    def test_install_adds_every_preset_disabled(self):
        result = self.manager.install_presets()
        self.assertTrue(result["success"], result)
        self.assertEqual(result["nombre"], len(routine_presets.PRESETS))
        self.assertEqual(self._names(), routine_presets.preset_names())

        # Aucune routine ne se déclenche tant qu'elle n'est pas activée.
        self.assertEqual(self.manager.scheduled_routines(), [])

    def test_install_is_idempotent(self):
        self.manager.install_presets()
        second = self.manager.install_presets()
        self.assertTrue(second["success"], second)
        self.assertEqual(second["nombre"], 0)
        self.assertTrue(second["deja_en_place"])
        self.assertEqual(len(self._names()), len(routine_presets.PRESETS))

    def test_enabled_preset_becomes_scheduled(self):
        self.manager.install_presets()
        name = routine_presets.preset_names()[0]
        self.assertTrue(self.manager.update_routine(name, enabled=True)["success"])
        scheduled = [item["name"] for item in self.manager.scheduled_routines()]
        self.assertIn(name, scheduled)

        self.assertTrue(self.manager.update_routine(name, enabled=False)["success"])
        self.assertEqual(self.manager.scheduled_routines(), [])

    def test_install_does_not_overwrite_a_customised_preset(self):
        self.manager.install_presets()
        name = routine_presets.preset_names()[0]
        self.manager.update_routine(
            name,
            steps="get_local_time()",
            description="ma version",
            schedule={"time": "06:15", "days": [2]},
            enabled=True,
        )

        self.manager.install_presets()  # ne doit rien écraser

        described = self.manager.describe_routine(name)
        self.assertTrue(described["success"], described)
        self.assertEqual(described["etapes"], "get_local_time()")
        self.assertEqual(described["description"], "ma version")
        self.assertIn("06:15", described["planification"])
        self.assertTrue(described["active"])

    def test_deleted_preset_is_not_reinstalled(self):
        self.manager.install_presets()
        name = routine_presets.preset_names()[0]
        self.assertTrue(self.manager.delete_routine(name, confirm=True)["success"])

        result = self.manager.install_presets()
        self.assertTrue(result["success"], result)
        self.assertNotIn(name, self._names())

    def test_user_routines_are_never_touched(self):
        self.manager.create_routine("ma routine", "get_local_time()", description="perso")
        self.manager.install_presets()
        self.assertIn("ma routine", self._names())
        self.assertEqual(
            self.manager.describe_routine("ma routine")["etapes"], "get_local_time()"
        )

    def test_restore_reinstalls_deleted_presets(self):
        self.manager.install_presets()
        name = routine_presets.preset_names()[0]
        self.manager.delete_routine(name, confirm=True)

        result = self.manager.install_presets(restore=True)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["ajoutees"], [name])
        # Un preset restauré est réajouté en fin de fichier : l'ordre importe
        # peu, la présence de tout le catalogue oui.
        self.assertEqual(sorted(self._names()), sorted(routine_presets.preset_names()))

    def test_every_preset_is_dry_runnable(self):
        self.manager.install_presets()
        for name in routine_presets.preset_names():
            with self.subTest(preset=name):
                result = self.manager.run_routine(name, dry_run=True)
                self.assertTrue(result["success"], result)
                self.assertTrue(result["simulation"])

    def test_file_is_human_readable(self):
        self.manager.install_presets()
        with open(self.path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertEqual(payload["version"], 1)
        self.assertEqual(len(payload["routines"]), len(routine_presets.PRESETS))
        # La section « presets » mémorise ce qui a déjà été installé.
        self.assertEqual(len(payload["presets"]["installed"]), len(routine_presets.PRESETS))
        self.assertEqual(payload["presets"]["dismissed"], [])

    def test_installation_is_refused_when_routines_are_disabled(self):
        manager = routines.RoutineManager(self.path, enabled=False)
        result = manager.install_presets()
        self.assertFalse(result["success"])


class PresetExecutionTestCase(unittest.TestCase):
    """Les presets s'exécutent réellement, de bout en bout."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "routines.json")
        self.manager = routines.RoutineManager(self.path)
        routines.set_tool_registry(tools.TOOL_FUNCTIONS, tools.TOOL_DECLARATIONS)
        self.addCleanup(
            lambda: routines.set_tool_registry(tools.TOOL_FUNCTIONS, tools.TOOL_DECLARATIONS)
        )

    def tearDown(self):
        self._dir.cleanup()

    def _stub_tools(self):
        """Remplace les outils par des doublures : aucun effet réel sur le PC."""
        calls: list[str] = []

        def _make(tool: str):
            def _fake(**kwargs):
                calls.append(tool)
                return {"success": True}

            return _fake

        stubs = {name: _make(name) for name in tools.TOOL_FUNCTIONS}
        routines.set_tool_registry(stubs, tools.TOOL_DECLARATIONS)
        return calls

    def test_every_preset_runs_all_its_steps(self):
        self.manager.install_presets()
        for preset in routine_presets.PRESETS:
            with self.subTest(preset=preset["name"]):
                calls = self._stub_tools()
                with mock.patch("src.routines.time.sleep"):
                    result = self.manager.run_routine(preset["name"])
                self.assertTrue(result["success"], result)
                self.assertEqual(result["echecs"], [])
                expected = [step["tool"] for step in preset["steps"]]
                self.assertEqual(calls, [tool for tool in expected if tool != routines.WAIT_TOOL])
                self.assertEqual(result["etapes_reussies"], len(expected))

    def test_step_arguments_bind_at_runtime(self):
        """Les arguments doivent être acceptés par la fonction Python réelle."""
        self.manager.install_presets()
        for preset in routine_presets.PRESETS:
            for step in preset["steps"]:
                if step["tool"] == routines.WAIT_TOOL:
                    continue
                with self.subTest(preset=preset["name"], outil=step["tool"]):
                    signature = inspect.signature(tools.TOOL_FUNCTIONS[step["tool"]])
                    signature.bind(**step["args"])


class PresetSchedulingTestCase(unittest.TestCase):
    """Un preset activé est bien déclenché par le planificateur."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "routines.json")
        self.manager = routines.RoutineManager(self.path)
        routines.set_tool_registry(tools.TOOL_FUNCTIONS, tools.TOOL_DECLARATIONS)
        self.addCleanup(
            lambda: routines.set_tool_registry(tools.TOOL_FUNCTIONS, tools.TOOL_DECLARATIONS)
        )
        self.scheduler = scheduler.Scheduler(
            os.path.join(self._dir.name, "schedule.db"), routine_manager=self.manager
        )

    def tearDown(self):
        self._dir.cleanup()

    def test_enabled_preset_is_triggered_at_its_time(self):
        self.manager.install_presets()
        name = routine_presets.preset_names()[0]

        # On cale la planification sur l'instant présent, comme le ferait
        # l'horloge au moment où la routine doit partir.
        now = datetime.datetime.now()
        self.assertTrue(
            self.manager.update_routine(
                name,
                enabled=True,
                schedule={"time": now.strftime("%H:%M"), "days": [now.weekday()]},
            )["success"]
        )

        # Outils neutralisés : rien n'est réellement ouvert pendant le test.
        routines.set_tool_registry(
            {tool: (lambda **kwargs: {"success": True}) for tool in tools.TOOL_FUNCTIONS},
            tools.TOOL_DECLARATIONS,
        )
        with mock.patch("src.routines.time.sleep"):
            self.scheduler._check_scheduled_routines()

        described = self.manager.describe_routine(name)
        self.assertEqual(described["executions"], 1)

        # Et un second passage ne la redéclenche pas dans la même minute.
        self.scheduler._check_scheduled_routines()
        self.assertEqual(self.manager.describe_routine(name)["executions"], 1)

    def test_disabled_preset_is_never_triggered(self):
        self.manager.install_presets()
        name = routine_presets.preset_names()[0]
        now = datetime.datetime.now()
        self.manager.update_routine(
            name,
            enabled=False,
            schedule={"time": now.strftime("%H:%M"), "days": [now.weekday()]},
        )
        self.scheduler._check_scheduled_routines()
        self.assertEqual(self.manager.describe_routine(name)["executions"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
