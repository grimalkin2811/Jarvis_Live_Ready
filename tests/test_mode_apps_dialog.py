"""Smoke tests Qt du dialogue « Applications des modes » (UI/mode_apps_dialog).

Couvre l'usage réel, sans toucher le profil utilisateur :

* instanciation avec un gestionnaire injecté (fichier temporaire) ;
* les cases du catalogue reflètent la configuration sauvegardée ;
* cocher/décocher met à jour le gestionnaire IMMÉDIATEMENT (persistance) ;
* le mode jeu et le mode focus sont indépendants dans la fenêtre ;
* ajout d'une application sur mesure ;
* réinitialisation aux valeurs d'origine ;
* résumé des compteurs ;
* la persistance survit à un « redémarrage » (nouvelle instance du manager) ;
* le singleton show_mode_apps_dialog : une seule fenêtre, recréée après
  fermeture (pas de référence morte).
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtWidgets import QApplication  # noqa: E402

import UI.mode_apps_dialog as mad  # noqa: E402
from src import mode_apps, modes  # noqa: E402
from src.modes import MODE_FOCUS, MODE_GAME  # noqa: E402


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class _IsolatedManager:
    def __init__(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis_dialog_"))
        self.path = self.tmp / "mode.json"

    def create(self) -> "modes.JarvisModeManager":
        return modes.JarvisModeManager(self.path)


class ModeAppsDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.iso = _IsolatedManager()
        self.addCleanup(lambda: _rm_tree(self.iso.tmp))
        self.manager = self.iso.create()
        self.dialog = mad.ModeAppsDialog(manager=self.manager)
        self.dialog.show()  # déclenche showEvent → refresh_all
        self.addCleanup(self.dialog.deleteLater)
        self.addCleanup(self.app.processEvents)

    def _checked(self, mode: str) -> set[str]:
        section = self.dialog.sections[mode]
        return {
            label
            for label, key in (
                (app.label, mode_apps.normalize_app_name(app.label))
                for app in mode_apps.KNOWN_APPS
            )
            if section.box[key].isChecked()
        }

    # ------------------------------------------------------------------
    def test_dialog_reflects_saved_configuration(self) -> None:
        game = self._checked(MODE_GAME)
        focus = self._checked(MODE_FOCUS)
        self.assertEqual(
            game, set(self.manager.get_mode_apps(MODE_GAME)),
            "les cases jeu ne reflètent pas la configuration",
        )
        self.assertEqual(
            focus, set(self.manager.get_mode_apps(MODE_FOCUS)),
            "les cases focus ne reflètent pas la configuration",
        )
        # Opéra GX n'est pas fermé par défaut par le mode jeu.
        self.assertNotIn("Opera GX", game)
        self.assertIn("Discord", focus)

    def test_uncheck_updates_manager_immediately(self) -> None:
        section = self.dialog.sections[MODE_FOCUS]
        box = section.box[mode_apps.normalize_app_name("Discord")]
        self.assertTrue(box.isChecked())

        box.setChecked(False)
        self.app.processEvents()
        self.assertNotIn(
            "Discord", self.manager.get_mode_apps(MODE_FOCUS),
            "décocher doit retirer l'app de la liste focus immédiatement",
        )
        # La case reflète toujours la réalité sauvegardée.
        section.refresh()
        self.assertFalse(box.isChecked())

        # Re-cocher : l'app revient.
        box.setChecked(True)
        self.app.processEvents()
        self.assertIn("Discord", self.manager.get_mode_apps(MODE_FOCUS))

    def test_check_unselected_catalog_app(self) -> None:
        section = self.dialog.sections[MODE_GAME]
        box = section.box[mode_apps.normalize_app_name("Opera GX")]
        self.assertFalse(box.isChecked())
        box.setChecked(True)
        self.app.processEvents()
        self.assertIn("Opera GX", self.manager.get_mode_apps(MODE_GAME))
        self.assertEqual(
            len(self.manager.get_mode_apps(MODE_GAME)),
            len(mode_apps.default_apps_for(MODE_GAME)) + 1,
        )

    def test_game_and_focus_sections_are_independent(self) -> None:
        before_focus = list(self.manager.get_mode_apps(MODE_FOCUS))
        game_section = self.dialog.sections[MODE_GAME]
        box = game_section.box[mode_apps.normalize_app_name("Opera GX")]
        box.setChecked(True)
        self.app.processEvents()
        self.assertNotIn("Opera GX", before_focus)
        self.assertEqual(
            before_focus, self.manager.get_mode_apps(MODE_FOCUS),
            "modifier le mode jeu ne doit jamais toucher au mode focus",
        )

    def test_add_custom_app(self) -> None:
        section = self.dialog.sections[MODE_FOCUS]
        section.edit.setText("Mon Logiciel Mystère")
        section._add_custom()
        self.app.processEvents()
        self.assertIn(
            "Mon Logiciel Mystère", self.manager.get_mode_apps(MODE_FOCUS)
        )
        self.assertEqual(section.edit.text(), "", "le champ doit être vidé")
        self.assertIn("Mon Logiciel Mystère", section.custom_label.text())
        self.assertIn("ajoutée", section.feedback.text())

    def test_reset_defaults_button(self) -> None:
        self.manager.set_mode_apps(MODE_GAME, ["Discord"])
        self.manager.set_mode_apps(MODE_FOCUS, [])
        self.dialog.refresh_all()
        self.app.processEvents()

        button = _find_button(self.dialog, "Rétablir les valeurs d'origine")
        self.assertIsNotNone(button, "le bouton de réinitialisation est absent")
        button.click()
        self.app.processEvents()

        self.assertEqual(
            self.manager.get_mode_apps(MODE_GAME),
            mode_apps.default_apps_for(MODE_GAME),
        )
        self.assertEqual(
            self.manager.get_mode_apps(MODE_FOCUS),
            mode_apps.default_apps_for(MODE_FOCUS),
        )
        # Les cases sont re-synchronisées sur les défauts.
        game = self._checked(MODE_GAME)
        self.assertEqual(game, set(mode_apps.default_apps_for(MODE_GAME)))

    def test_summary_counts(self) -> None:
        self.manager.set_mode_apps(MODE_GAME, ["Discord", "Opera GX"])
        self.manager.set_mode_apps(MODE_FOCUS, ["Spotify"])
        self.dialog.refresh_all()
        self.assertIn("Mode jeu : 2", self.dialog.summary.text())
        self.assertIn("Mode focus : 1", self.dialog.summary.text())

    def test_changes_survive_restart(self) -> None:
        section = self.dialog.sections[MODE_GAME]
        box = section.box[mode_apps.normalize_app_name("Opera GX")]
        box.setChecked(True)
        self.app.processEvents()

        # « Redémarrage » : nouvelle instance sur le même fichier.
        reloaded = self.iso.create()
        self.assertIn("Opera GX", reloaded.get_mode_apps(MODE_GAME))
        # Une nouvelle fenêtre lit la même configuration.
        second = mad.ModeAppsDialog(manager=reloaded)
        second.show()
        self.app.processEvents()
        second_box = second.sections[MODE_GAME].box[
            mode_apps.normalize_app_name("Opera GX")
        ]
        self.assertTrue(second_box.isChecked())
        second.deleteLater()
        self.app.processEvents()


class _SingletonDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self._saved_global = mad._DIALOG
        mad._DIALOG = None
        self.iso = _IsolatedManager()
        self.manager = self.iso.create()

    def tearDown(self) -> None:
        self.app.processEvents()
        if mad._DIALOG is not None:
            mad._DIALOG.deleteLater()
        self.app.processEvents()
        mad._DIALOG = self._saved_global
        _rm_tree(self.iso.tmp)

    def test_singleton_reuses_and_recreates(self) -> None:
        with patch.object(
            mad, "get_default_mode_manager", return_value=self.manager
        ):
            first = mad.show_mode_apps_dialog()
            self.assertTrue(first.isVisible())
            # Deuxième demande : la même fenêtre, pas un doublon.
            second = mad.show_mode_apps_dialog()
            self.assertIs(first, second)

            # Un changement fait par la voix est visible dans la fenêtre.
            self.manager.set_mode_apps(MODE_GAME, ["Discord"])
            mad.show_mode_apps_dialog()  # refresh_all
            self.app.processEvents()
            self.assertEqual(
                first.sections[MODE_GAME].box[
                    mode_apps.normalize_app_name("Discord")
                ].isChecked(),
                True,
            )

            # Fermeture : le singleton est libéré (pas de référence morte).
            first.close()
            self.app.processEvents()
            self.app.processEvents()
            self.assertIsNone(mad._DIALOG)

            # Rouvrir : une nouvelle instance fonctionnelle.
            third = mad.show_mode_apps_dialog()
            self.assertIsNot(first, third)
            self.assertTrue(third.isVisible())


def _find_button(widget, text: str):
    from PySide6.QtWidgets import QPushButton

    for child in widget.findChildren(QPushButton):
        if child.text() == text:
            return child
    return None


def _rm_tree(path: Path) -> None:
    import shutil

    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


if __name__ == "__main__":
    unittest.main()
