"""Tests de la configuration des applications des modes jeu / focus.

Couvre :
* le catalogue générique (résolution de noms, multi-images, sur mesure) ;
* les valeurs par défaut = comportement historique v1.x (compatibilité) ;
* l'indépendance des deux listes (jeu ≠ focus) ;
* la persistance après « redémarrage » (nouvelle instance, même fichier) ;
* la migration des anciennes configurations (clés absentes / corrompues) ;
* la fermeture best effort : application absente → pas de crash, pas d'erreur ;
* les outils vocaux (list / set / toggle / reset) et le garde-fou de mode.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import mode_apps, modes, tools


def _manager(directory: Path) -> modes.JarvisModeManager:
    return modes.JarvisModeManager(directory / "mode.json")


class CatalogTests(unittest.TestCase):
    def test_legacy_game_behavior_is_the_default(self) -> None:
        # Les images fermées par défaut = exactement celles de la v1.x.
        default_images = set()
        for label in mode_apps.default_apps_for(modes.MODE_GAME):
            default_images.update(
                image.lower() for image in mode_apps.process_images_for(label)
            )
        self.assertTrue(mode_apps.LEGACY_GAME_IMAGES <= default_images)
        # Opera (opera.exe) est dans la liste historique ; Opera GX ne l'était
        # pas : la liste par défaut doit respecter cette distinction.
        self.assertIn("Opera", mode_apps.default_apps_for(modes.MODE_GAME))
        self.assertNotIn("Opera GX", mode_apps.default_apps_for(modes.MODE_GAME))

    def test_legacy_focus_behavior_is_the_default(self) -> None:
        default_images = set()
        for label in mode_apps.default_apps_for(modes.MODE_FOCUS):
            default_images.update(
                image.lower() for image in mode_apps.process_images_for(label)
            )
        self.assertTrue(mode_apps.LEGACY_FOCUS_IMAGES <= default_images)
        # Les navigateurs n'étaient pas fermés par le focus historique.
        for browser in ("Chrome", "Edge", "Firefox", "Opera", "Opera GX"):
            self.assertNotIn(browser, mode_apps.default_apps_for(modes.MODE_FOCUS))

    def test_name_resolution(self) -> None:
        cases = {
            "Opera GX": "opera_gx",
            "opera gx": "opera_gx",
            "OPERA GX": "opera_gx",
            "opera": "opera",
            "Discord": "discord",
            "discord": "discord",
            "Microsoft Teams": "teams",
            "teams": "teams",
            "VS Code": "vscode",
            "vscode": "vscode",
            "VLC": "vlc",
            "lecteur windows media": "windows_media",
        }
        for name, expected in cases.items():
            app = mode_apps.find_app(name)
            self.assertIsNotNone(app, name)
            self.assertEqual(app.id, expected, name)

    def test_ambiguous_names_prefer_shortest_match(self) -> None:
        # « opera » ne doit pas capturer « opera gx ».
        self.assertEqual(mode_apps.find_app("opera").id, "opera")
        self.assertEqual(mode_apps.find_app("opera gx").id, "opera_gx")

    def test_unknown_app_is_custom(self) -> None:
        self.assertIsNone(mode_apps.find_app("LogicielInconnu"))
        self.assertEqual(mode_apps.process_images_for("LogicielInconnu"), ["logicielinconnu.exe"])
        self.assertEqual(mode_apps.app_label("  Logiciel Inconnu "), "Logiciel Inconnu")

    def test_known_app_multi_window_single_process(self) -> None:
        # Plusieurs versions d'une même app = plusieurs images candidates,
        # mais fermer l'app = fermer le(s) processus (toutes les fenêtres).
        self.assertIn("discord.exe", mode_apps.process_images_for("Discord"))
        self.assertIn("discordptb.exe", mode_apps.process_images_for("Discord"))

    def test_mode_name_aliases(self) -> None:
        self.assertEqual(modes.mode_apps.valid_mode_name("jeu"), "game")
        self.assertEqual(modes.mode_apps.valid_mode_name("mode jeu"), "game")
        self.assertEqual(modes.mode_apps.valid_mode_name("GAME"), "game")
        self.assertEqual(modes.mode_apps.valid_mode_name("révision"), "focus")
        self.assertEqual(modes.mode_apps.valid_mode_name("focus"), "focus")
        self.assertIsNone(modes.mode_apps.valid_mode_name("inconnu"))


class ConfigIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.manager = _manager(self.directory)
        previous = modes._DEFAULT_MANAGER
        modes.set_default_mode_manager(self.manager)
        self.addCleanup(modes.set_default_mode_manager, previous)

    def test_default_lists_match_legacy(self) -> None:
        self.assertEqual(
            self.manager.get_mode_apps("game"),
            mode_apps.default_apps_for(modes.MODE_GAME),
        )
        self.assertEqual(
            self.manager.get_mode_apps("focus"),
            mode_apps.default_apps_for(modes.MODE_FOCUS),
        )

    def test_lists_are_independent(self) -> None:
        # Scénario du ticket : Mode Jeu sans Opera GX, Mode Focus avec.
        game_before = self.manager.get_mode_apps("game")
        focus_before = self.manager.get_mode_apps("focus")

        self.assertFalse("Opera GX" in game_before)
        result = self.manager.toggle_mode_app("focus", "opera gx", True)
        self.assertTrue(result["success"])
        self.assertIn("Opera GX", self.manager.get_mode_apps("focus"))
        # La liste du mode jeu est strictement intacte.
        self.assertEqual(self.manager.get_mode_apps("game"), game_before)

        result = self.manager.toggle_mode_app("game", "opera gx", True)
        self.assertIn("Opera GX", self.manager.get_mode_apps("game"))
        self.assertIn("Opera GX", self.manager.get_mode_apps("focus"))

        # Retirer du focus ne touche pas le jeu.
        self.manager.toggle_mode_app("focus", "opera gx", False)
        self.assertNotIn("Opera GX", self.manager.get_mode_apps("focus"))
        self.assertIn("Opera GX", self.manager.get_mode_apps("game"))
        # Le focus retrouve sa liste initiale.
        self.assertEqual(self.manager.get_mode_apps("focus"), focus_before)

    def test_coexistence_focus_opera_gx_game_discord(self) -> None:
        # Exemple du cahier des charges :
        #   Mode Jeu : Discord sélectionné, Opera GX non sélectionné ;
        #   Mode Focus : Opera GX sélectionné, Discord non sélectionné.
        self.assertTrue(
            self.manager.toggle_mode_app("game", "opera gx", False)["success"]
        )
        self.assertTrue(self.manager.toggle_mode_app("game", "discord", True)["success"])
        self.assertTrue(self.manager.toggle_mode_app("focus", "opera gx", True)["success"])
        self.assertTrue(self.manager.toggle_mode_app("focus", "discord", False)["success"])

        game = set(self.manager.get_mode_apps("game"))
        focus = set(self.manager.get_mode_apps("focus"))
        self.assertIn("Discord", game)
        self.assertNotIn("Opera GX", game)
        self.assertIn("Opera GX", focus)
        self.assertNotIn("Discord", focus)

    def test_custom_app_can_be_added_and_removed(self) -> None:
        result = self.manager.toggle_mode_app("game", "Mon Logiciel", True)
        self.assertTrue(result["success"])
        self.assertIn("Mon Logiciel", self.manager.get_mode_apps("game"))
        # Le doublon normalisé n'ajoute pas d'entrée.
        self.manager.toggle_mode_app("game", "mon logiciel", True)
        self.assertEqual(
            [e for e in self.manager.get_mode_apps("game") if "logiciel" in e.lower()],
            ["Mon Logiciel"],
        )
        self.manager.toggle_mode_app("game", "mon logiciel", False)
        self.assertNotIn("Mon Logiciel", self.manager.get_mode_apps("game"))

    def test_set_replaces_whole_list(self) -> None:
        result = self.manager.set_mode_apps("game", "Discord, Spotify, Opera GX")
        self.assertTrue(result["success"])
        self.assertEqual(self.manager.get_mode_apps("game"), ["Discord", "Spotify", "Opera GX"])
        result = self.manager.set_mode_apps("game", [])
        self.assertTrue(result["success"])
        self.assertEqual(self.manager.get_mode_apps("game"), [])

    def test_reset_restores_legacy_defaults(self) -> None:
        self.manager.set_mode_apps("focus", "Chrome")
        self.assertTrue(self.manager.reset_mode_apps("focus")["success"])
        self.assertEqual(
            self.manager.get_mode_apps("focus"),
            mode_apps.default_apps_for(modes.MODE_FOCUS),
        )
        # Le mode jeu est resté par défaut.
        self.assertEqual(
            self.manager.get_mode_apps("game"),
            mode_apps.default_apps_for(modes.MODE_GAME),
        )

    def test_unknown_mode_is_rejected_not_crashing(self) -> None:
        for fn in (
            lambda: self.manager.toggle_mode_app("meteo", "discord", True),
            lambda: self.manager.set_mode_apps("meteo", "discord"),
            lambda: self.manager.reset_mode_apps("meteo"),
            lambda: self.manager.list_mode_apps("meteo"),
        ):
            result = fn()
            self.assertFalse(result["success"])
            self.assertIn("error", result)


class PersistenceAndMigrationTests(unittest.TestCase):
    def _write(self, payload) -> Path:
        path = Path(tempfile.mkdtemp()) / "mode.json"
        self.addCleanup(lambda: _rm(path))
        with path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    def test_config_survives_restart(self) -> None:
        path = Path(tempfile.mkdtemp()) / "mode.json"
        self.addCleanup(lambda: _rm(path))
        manager = modes.JarvisModeManager(path)
        manager.toggle_mode_app("game", "opera gx", True)
        manager.toggle_mode_app("focus", "discord", False)
        # « Redémarrage » : nouvelle instance sur le même fichier.
        reloaded = modes.JarvisModeManager(path)
        self.assertIn("Opera GX", reloaded.get_mode_apps("game"))
        self.assertNotIn("Discord", reloaded.get_mode_apps("focus"))
        self.assertNotIn("Opera GX", reloaded.get_mode_apps("focus"))

    def test_old_config_without_keys_gets_defaults(self) -> None:
        # Configuration v1.x : pas de clés *_apps.
        path = self._write(
            {"version": 1, "active_mode": "game", "activated_at": "2020-01-01T00:00:00"}
        )
        manager = modes.JarvisModeManager(path)
        self.assertEqual(manager.current_mode(), "game")
        self.assertEqual(
            manager.get_mode_apps("game"),
            mode_apps.default_apps_for(modes.MODE_GAME),
        )
        self.assertEqual(
            manager.get_mode_apps("focus"),
            mode_apps.default_apps_for(modes.MODE_FOCUS),
        )

    def test_corrupt_config_never_crashes(self) -> None:
        path = self._write(
            {
                "active_mode": "focus",
                "game_apps": {"pas": "une liste"},
                "focus_apps": ["discord", 7, None, "Discord", "", 3.5, "opera gx"],
            }
        )
        manager = modes.JarvisModeManager(path)
        self.assertEqual(manager.current_mode(), "focus")
        self.assertEqual(manager.get_mode_apps("focus"), ["discord", "7", "3.5", "opera gx"])
        # Clé illisible → valeurs par défaut (pas d'exception, pas de vide).
        self.assertEqual(
            manager.get_mode_apps("game"),
            mode_apps.default_apps_for(modes.MODE_GAME),
        )

    def test_empty_lists_mean_close_nothing(self) -> None:
        # L'utilisateur a volontairement tout décoché : le mode ferme
        # strictement rien (et ne plante pas).
        path = self._write({"active_mode": "normal", "game_apps": [], "focus_apps": []})
        manager = modes.JarvisModeManager(path)
        self.assertEqual(manager.get_mode_apps("game"), [])
        result = manager.activate_game_mode(close_background=True)
        self.assertTrue(result["success"])
        self.assertEqual(result["applications_fermees"], [])

    def test_json_file_structure(self) -> None:
        path = Path(tempfile.mkdtemp()) / "mode.json"
        self.addCleanup(lambda: _rm(path))
        manager = modes.JarvisModeManager(path)
        manager.toggle_mode_app("game", "opera gx", True)
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], 2)
        self.assertIn("Opera GX", payload["game_apps"])
        self.assertIn("Discord", payload["focus_apps"])


class CloseProcessesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.manager = _manager(self.directory)
        previous = modes._DEFAULT_MANAGER
        modes.set_default_mode_manager(self.manager)
        self.addCleanup(modes.set_default_mode_manager, previous)

    @patch.object(modes.JarvisModeManager, "_close_by_window_title", return_value=False)
    def test_configured_app_absent_does_not_crash_or_error(self, _title) -> None:
        # Application configurée mais non ouverte : taskkill « not found »
        # → ignorée proprement, aucune erreur remontée.
        with patch.object(modes, "_IS_WINDOWS", True), patch("subprocess.run") as run:
            run.return_value = type(
                "R", (), {"returncode": 128, "stdout": "", "stderr": "ERREUR : processus introuvable"}
            )()
            result = self.manager._close_processes(["Discord", "Mon Logiciel"])
        self.assertTrue(result["success"])
        self.assertEqual(result["fermes"], [])
        self.assertEqual(sorted(result["ignores"]), ["Discord", "Mon Logiciel"])
        self.assertEqual(result["erreurs"], [])

    def test_operagx_not_selected_never_closed(self) -> None:
        # Cas concret du ticket : Opera GX NON sélectionné → le mode jeu ne
        # doit même pas essayer de le fermer.
        images_attempted = []

        def fake_run(command, **kwargs):
            image = command[2]
            images_attempted.append(image.lower())
            return type("R", (), {"returncode": 128, "stdout": "", "stderr": "not found"})()

        with patch.object(modes, "_IS_WINDOWS", True), patch(
            "subprocess.run", side_effect=fake_run
        ), patch.object(
            modes.JarvisModeManager, "_set_jarvis_low_priority", return_value={"success": True}
        ), patch.object(
            modes.JarvisModeManager, "_cancel_active_protocol",
            return_value={"success": True, "annule": False},
        ):
            self.manager.activate_game_mode(close_background=True)
        self.assertNotIn("operagx.exe", images_attempted)
        self.assertIn("discord.exe", images_attempted)
        self.assertIn("chrome.exe", images_attempted)

    def test_focus_closes_only_its_own_list(self) -> None:
        # Focus : Opera GX sélectionné, Discord non ; jeu : l'inverse.
        self.manager.toggle_mode_app("focus", "opera gx", True)
        self.manager.toggle_mode_app("focus", "discord", False)
        self.manager.toggle_mode_app("game", "opera gx", True)

        images = []

        def fake_run(command, **kwargs):
            images.append(command[2].lower())
            return type("R", (), {"returncode": 128, "stdout": "", "stderr": "not found"})()

        with patch.object(modes, "_IS_WINDOWS", True), patch(
            "subprocess.run", side_effect=fake_run
        ), patch.object(
            modes.JarvisModeManager, "_set_jarvis_low_priority", return_value={"success": True}
        ):
            self.manager.activate_focus_mode(close_distractions=True)
        self.assertIn("operagx.exe", images)
        self.assertNotIn("discord.exe", images)


class ModeAppsToolsTests(unittest.TestCase):
    """Les outils passent par le garde-fou réel (TOOL_FUNCTIONS)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.manager = _manager(self.directory)
        previous = modes._DEFAULT_MANAGER
        modes.set_default_mode_manager(self.manager)
        self.addCleanup(modes.set_default_mode_manager, previous)
        for name in ("_set_jarvis_low_priority", "_restore_jarvis_priority"):
            patcher = patch.object(
                modes.JarvisModeManager, name, return_value={"success": True, "applique": False}
            )
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_voice_commands_flow(self) -> None:
        # « quelles applications le mode jeu ferme-tu ? »
        result = tools.TOOL_FUNCTIONS["list_mode_applications"](mode="jeu")
        self.assertTrue(result["success"])
        self.assertEqual(result["mode"], "game")
        opera = next(a for a in result["applications"] if a["id"] == "opera")
        self.assertTrue(opera["selectionnee"])

        # « dans le mode jeu, ne ferme pas Opera GX »
        result = tools.TOOL_FUNCTIONS["toggle_mode_application"](
            mode="jeu", application="Opera GX", enabled=False
        )
        self.assertTrue(result["success"])
        self.assertFalse(result["selectionnee"])
        self.assertNotIn("Opera GX", self.manager.get_mode_apps("game"))
        self.assertEqual(
            tools.TOOL_FUNCTIONS["get_jarvis_mode"]()["applications_jeu"],
            self.manager.get_mode_apps("game"),
        )

        # « le mode focus doit fermer Opera GX mais pas Discord »
        self.assertTrue(
            tools.TOOL_FUNCTIONS["toggle_mode_application"](
                mode="focus", application="opera gx", enabled=True
            )["success"]
        )
        self.assertTrue(
            tools.TOOL_FUNCTIONS["toggle_mode_application"](
                mode="focus", application="discord", enabled=False
            )["success"]
        )
        # Le mode jeu n'a pas bougé.
        self.assertNotIn("Opera GX", self.manager.get_mode_apps("game"))

        # Remplacement complet + remise à zéro.
        result = tools.TOOL_FUNCTIONS["set_mode_applications"](
            mode="jeu", applications="Discord, Spotify"
        )
        self.assertTrue(result["success"])
        self.assertEqual(self.manager.get_mode_apps("game"), ["Discord", "Spotify"])
        self.assertTrue(
            tools.TOOL_FUNCTIONS["reset_mode_applications"](mode="jeu")["success"]
        )
        self.assertEqual(
            self.manager.get_mode_apps("game"),
            mode_apps.default_apps_for(modes.MODE_GAME),
        )

    def test_tools_remain_allowed_during_game_mode(self) -> None:
        # Le réglage d'un mode reste possible PENDANT ce mode : les outils
        # de configuration font partie des commandes de contrôle.
        self.assertTrue(self.manager.activate_game_mode(close_background=False)["success"])
        self.assertTrue(
            tools.TOOL_FUNCTIONS["toggle_mode_application"](
                mode="jeu", application="chrome", enabled=False
            )["success"]
        )
        self.assertTrue(tools.TOOL_FUNCTIONS["list_mode_applications"](mode="jeu")["success"])
        self.assertTrue(tools.TOOL_FUNCTIONS["get_jarvis_mode"]()["success"])

    def test_status_includes_both_lists(self) -> None:
        status = self.manager.status()
        self.assertEqual(status["applications_jeu"], self.manager.get_mode_apps("game"))
        self.assertEqual(status["applications_focus"], self.manager.get_mode_apps("focus"))


def _rm(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass
    try:
        path.parent.rmdir()
    except OSError:
        pass


if __name__ == "__main__":
    unittest.main()
