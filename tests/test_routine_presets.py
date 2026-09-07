"""Tests des routines préconfigurées de Jarvis.

Aucun fichier utilisateur n'est touché : tout se passe dans un dossier
temporaire. Les routines ne sont jamais réellement exécutées ici (ou
seulement en simulation) : on vérifie la validité des définitions,
l'installation respectueuse du fichier existant et la sémantique
activer/désactiver.

    python -m unittest tests.test_routine_presets
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import routines  # noqa: E402
from src import tools  # noqa: E402
from src.routine_presets import PRESET_ROUTINES  # noqa: E402
from src.timeparse import parse_schedule  # noqa: E402


class PresetIntegrityTests(unittest.TestCase):
    """Les définitions livrées doivent être saines, sans jamais être exécutées."""

    def test_count_is_in_requested_range(self):
        self.assertGreaterEqual(len(PRESET_ROUTINES), 5)
        self.assertLessEqual(len(PRESET_ROUTINES), 15)

    def test_names_are_unique_and_pronounceable(self):
        names = [str(p["name"]).strip() for p in PRESET_ROUTINES]
        self.assertTrue(all(names), "un préréglage n'a pas de nom")
        normalized = [routines.normalize(n) for n in names]
        self.assertEqual(len(normalized), len(set(normalized)), "noms en doublon")
        for name in names:
            self.assertLessEqual(len(name), 60)

    def test_steps_parse_without_any_error(self):
        for preset in PRESET_ROUTINES:
            steps, errors = routines.parse_steps(preset["steps"])
            self.assertEqual(errors, [], f"{preset['name']}: {errors}")
            self.assertTrue(steps, preset["name"])
            self.assertLessEqual(len(steps), routines.MAX_STEPS, preset["name"])

    def test_steps_only_use_allowed_tools(self):
        available = set(routines.available_tools())
        for preset in PRESET_ROUTINES:
            steps, _ = routines.parse_steps(preset["steps"])
            for step in steps:
                self.assertIn(step["tool"], available, f"{preset['name']}: {step['tool']}")
                self.assertNotIn(step["tool"], routines.FORBIDDEN_TOOLS)

    def test_schedules_are_parseable(self):
        for preset in PRESET_ROUTINES:
            schedule = str(preset.get("schedule") or "").strip()
            if schedule:
                parsed = parse_schedule(schedule)
                self.assertIsNotNone(parsed, preset["name"])
            else:
                self.assertIsNone(parse_schedule(None))

    def test_scheduled_presets_ship_disabled(self):
        """Rien ne doit se déclencher automatiquement sans accord explicite."""
        for preset in PRESET_ROUTINES:
            if preset.get("schedule"):
                self.assertFalse(bool(preset.get("enabled")), preset["name"])

    def test_manual_presets_ship_enabled(self):
        """Une routine manuelle est utilisable dès la première seconde."""
        for preset in PRESET_ROUTINES:
            if not preset.get("schedule"):
                self.assertTrue(bool(preset.get("enabled")), preset["name"])

    def test_descriptions_are_provided(self):
        for preset in PRESET_ROUTINES:
            self.assertTrue(str(preset.get("description") or "").strip(), preset["name"])


class PresetInstallTestCase(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self._dir.name, "routines.json")
        self.manager = routines.RoutineManager(self.path)
        routines.set_tool_registry(tools.TOOL_FUNCTIONS, tools.TOOL_DECLARATIONS)

    def tearDown(self):
        self._dir.cleanup()


class TestInstallPresets(PresetInstallTestCase):
    def test_install_on_fresh_file(self):
        result = self.manager.install_presets()
        self.assertTrue(result["success"], result)
        self.assertEqual(len(result["installees"]), len(PRESET_ROUTINES))

        listed = self.manager.list_routines()
        self.assertEqual(listed["count"], len(PRESET_ROUTINES))
        self.assertTrue(all(item["predefinie"] for item in listed["routines"]))

    def test_install_is_idempotent(self):
        self.manager.install_presets()
        again = self.manager.install_presets()
        self.assertTrue(again["success"])
        self.assertEqual(again["installees"], [])
        self.assertEqual(self.manager.list_routines()["count"], len(PRESET_ROUTINES))

    def test_existing_routine_is_never_overwritten(self):
        self.manager.create_routine("focus", "flip_coin()", description="ma variante")
        result = self.manager.install_presets()
        self.assertTrue(result["success"])
        self.assertNotIn("focus", result["installees"])

        described = self.manager.describe_routine("focus")
        self.assertTrue(described["success"])
        self.assertFalse(described["predefinie"])
        self.assertEqual(described["description"], "ma variante")
        self.assertIn("flip_coin", described["etapes"])

    def test_deleted_preset_stays_deleted(self):
        self.manager.install_presets()
        self.assertTrue(self.manager.delete_routine("focus", confirm=True)["success"])

        # Ni un redémarrage, ni une réinstallation normale ne le font revenir.
        self.manager.install_presets()
        names = [item["name"] for item in self.manager.list_routines()["routines"]]
        self.assertNotIn("focus", names)
        self.assertEqual(len(names), len(PRESET_ROUTINES) - 1)

        with open(self.path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertIn("focus", payload.get("presets_removed", []))

    def test_force_reinstalls_deleted_presets(self):
        self.manager.install_presets()
        self.manager.delete_routine("focus", confirm=True)

        restored = self.manager.install_presets(force=True)
        self.assertTrue(restored["success"], restored)
        self.assertIn("focus", restored["installees"])
        self.assertEqual(self.manager.list_routines()["count"], len(PRESET_ROUTINES))

    def test_corrupted_file_is_never_overwritten(self):
        garbage = "{ ceci n'est pas du JSON"
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write(garbage)

        result = self.manager.install_presets()
        self.assertFalse(result["success"])

        with open(self.path, "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), garbage)

    def test_preset_flag_survives_reload(self):
        self.manager.install_presets()
        fresh_manager = routines.RoutineManager(self.path)
        described = fresh_manager.describe_routine("mode travail")
        self.assertTrue(described["success"])
        self.assertTrue(described["predefinie"])

    def test_create_with_preset_name_is_refused(self):
        self.manager.install_presets()
        again = self.manager.create_routine("Focus", "flip_coin()")
        self.assertFalse(again["success"])
        self.assertTrue(again.get("existe"))


class TestPresetActivation(PresetInstallTestCase):
    def test_scheduled_presets_wait_for_activation(self):
        self.manager.install_presets()
        scheduled = {item["name"] for item in self.manager.scheduled_routines()}
        for preset in PRESET_ROUTINES:
            if preset.get("schedule") and not preset.get("enabled"):
                self.assertNotIn(preset["name"], scheduled, preset["name"])

        activated = self.manager.update_routine("réveil", enabled=True)
        self.assertTrue(activated["success"], activated)
        scheduled = {item["name"] for item in self.manager.scheduled_routines()}
        self.assertIn("réveil", scheduled)

    def test_disabled_preset_refuses_to_run(self):
        self.manager.install_presets()
        refused = self.manager.run_routine("réveil")
        self.assertFalse(refused["success"])
        self.assertTrue(refused.get("desactivee"))
        self.assertIn("désactivée", refused["error"])

        # La simulation reste possible : on peut toujours inspecter.
        dry = self.manager.run_routine("réveil", dry_run=True)
        self.assertTrue(dry["success"])
        self.assertTrue(dry["simulation"])

    def test_enabled_preset_runs(self):
        self.manager.install_presets()
        self.manager.update_routine("réveil", enabled=True)
        result = self.manager.run_routine("réveil")
        self.assertFalse(result.get("desactivee"))
        self.assertNotIn("désactivée", result.get("error") or "")
        # L'exécution a bien eu lieu (les étapes peuvent échouer hors Windows,
        # elles sont rapportées mais jamais bloquantes).
        self.assertEqual(result.get("etapes_totales"), 3)

    def test_disabled_user_routine_refuses_to_run(self):
        """La sémantique « désactivée = ne s'exécute plus » est générale."""
        self.manager.create_routine("essai", "flip_coin()")
        self.manager.update_routine("essai", enabled=False)

        refused = self.manager.run_routine("essai")
        self.assertFalse(refused["success"])
        self.assertTrue(refused.get("desactivee"))

        self.manager.update_routine("essai", enabled=True)
        self.assertTrue(self.manager.run_routine("essai")["success"])

    def test_enabled_manual_preset_runs_from_the_start(self):
        self.manager.install_presets()
        result = self.manager.run_routine("focus", dry_run=True)
        self.assertTrue(result["success"], result)
        self.assertIn("set_timer", result["etapes"])


class TestInstallDefaultPresets(unittest.TestCase):
    """Le point d'entrée utilisé au démarrage de Jarvis."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self._path = os.path.join(self._dir.name, "routines.json")
        self._env = os.environ.copy()
        os.environ["JARVIS_ROUTINES_PATH"] = self._path
        routines.set_default_routine_manager(None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        routines.set_default_routine_manager(None)
        self._dir.cleanup()

    def test_installs_into_env_path(self):
        result = routines.install_default_presets()
        self.assertTrue(result["success"], result)
        self.assertEqual(len(result["installees"]), len(PRESET_ROUTINES))
        self.assertTrue(os.path.exists(self._path))

    def test_env_kill_switch(self):
        os.environ["JARVIS_PRESET_ROUTINES"] = "0"
        result = routines.install_default_presets()
        self.assertTrue(result["success"])
        self.assertTrue(result.get("desactivees"))
        self.assertFalse(os.path.exists(self._path))


class TestPresetToolRegistration(unittest.TestCase):
    def test_restore_tool_is_declared(self):
        self.assertIn("restore_preset_routines", tools.TOOL_FUNCTIONS)
        declared = {d["name"] for d in tools.TOOL_DECLARATIONS}
        self.assertIn("restore_preset_routines", declared)

    def test_restore_tool_is_forbidden_as_routine_step(self):
        self.assertIn("restore_preset_routines", routines.FORBIDDEN_TOOLS)


if __name__ == "__main__":
    unittest.main()
