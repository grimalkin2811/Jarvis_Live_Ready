"""Tests du masquage de l'orbe (menu Appearance → « Blob Visible »).

Vérifie, sans aucun affichage réel (Qt offscreen) :

* l'item existe dans le menu Appearance et rend son état réel ;
* l'activation masque la fenêtre pendant la SESSION (comportement 1.3.1) ;
* la réactivation réaffiche la fenêtre ;
* ``tick()`` maintient la fenêtre masquée (pas de réapparition parasite) ;
* **l'état masqué n'est JAMAIS persisté comme démarrage** (1.3.2) : ni dans
  ``state_to_dict``, ni relu par ``apply_state_dict`` (même avec un ancien
  fichier portant ``blob_hidden: true``) ;
* masquer → fermeture → relancement = **visible**, tous chemins de fermeture ;
* Jarvis continue de fonctionner pendant que l'orbe est masqué.
"""

import json
import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtWidgets import QApplication  # noqa: E402

from UI import appearance_actions, jarvis_menu as jm  # noqa: E402
from src import paths  # noqa: E402


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _appearance_sector() -> int:
    for index, spec in enumerate(jm.MENU_SPECS):
        if spec.name == "Appearance":
            return index
    raise AssertionError("menu Appearance introuvable")


def _blob_item():
    spec = jm.MENU_SPECS[_appearance_sector()]
    for item in spec.items:
        if item.label == "Blob Visible":
            return spec, item
    raise AssertionError("item « Blob Visible » introuvable")


class BlobVisibilityMenuTests(unittest.TestCase):
    """Le réglage est bien intégré au menu Appearance existant."""

    def test_menu_contains_a_blob_visible_toggle(self) -> None:
        spec, item = _blob_item()
        self.assertEqual(spec.name, "Appearance")
        self.assertEqual(item.kind, "toggle")

    def test_item_is_the_last_of_the_menu(self) -> None:
        # Ajouté en fin de menu : aucune position existante n'est décalée.
        spec = jm.MENU_SPECS[_appearance_sector()]
        self.assertEqual(spec.items[-1].label, "Blob Visible")

    def test_toggle_value_follows_appearance_state(self) -> None:
        app = _qt_app()  # noqa: F841 — QApplication requis par QWidget
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["JARVIS_DATA_DIR"] = tmp
            try:
                widget = jm.MorphingOrbWidget()
                spec, item = _blob_item()
                widget.appearance_state.blob_hidden = False
                self.assertTrue(widget._menu_toggle_value(spec, item))
                widget.appearance_state.blob_hidden = True
                self.assertFalse(widget._menu_toggle_value(spec, item))
                widget.deleteLater()
            finally:
                os.environ.pop("JARVIS_DATA_DIR", None)

    def test_displayed_value_text(self) -> None:
        app = _qt_app()  # noqa: F841
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["JARVIS_DATA_DIR"] = tmp
            try:
                widget = jm.MorphingOrbWidget()
                widget.appearance_state.blob_hidden = False
                self.assertEqual(widget._appearance_value("Blob Visible"), "On")
                widget.appearance_state.blob_hidden = True
                self.assertEqual(widget._appearance_value("Blob Visible"), "Off")
                widget.deleteLater()
            finally:
                os.environ.pop("JARVIS_DATA_DIR", None)


class BlobVisibilityBehaviourTests(unittest.TestCase):
    """Masquer / réafficher réellement la fenêtre, sans casser le reste."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()
        cls._tmp = tempfile.TemporaryDirectory()
        os.environ["JARVIS_DATA_DIR"] = cls._tmp.name
        cls.widget = jm.MorphingOrbWidget()
        cls.widget.resize(1280, 800)
        cls.widget.show()
        QApplication.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.widget.close()
        cls.widget.deleteLater()
        os.environ.pop("JARVIS_DATA_DIR", None)
        cls._tmp.cleanup()
        # Ces globaux pilotent le halo. Les laisser à « listening » / 0.7
        # fait échouer les tests de pixels des menus suivants (alpha du
        # halo pris pour une fuite de fond).
        jm.set_voice_energy(0.0)
        jm.set_presence_state("hidden")

    def setUp(self) -> None:
        # Chaque test repart d'un orbe visible et d'un menu fermé.
        self.widget.set_blob_visible(True)
        QApplication.processEvents()
        self.widget._close_radial_menu()
        self.assertTrue(self.widget.isVisible())

    def _click_blob_visible(self) -> None:
        sector = _appearance_sector()
        self.widget._open_radial_menu(sector)
        node = next(n for n in self.widget._menu_nodes if n.label == "Blob Visible")
        self.assertIsNotNone(node.callback)
        node.callback()
        QApplication.processEvents()

    # ------------------------------------------------------------------
    def test_menu_item_hides_the_blob(self) -> None:
        self._click_blob_visible()
        self.assertFalse(self.widget.isVisible())
        self.assertTrue(self.widget.appearance_state.blob_hidden)

    def test_hiding_closes_the_radial_menu(self) -> None:
        self._click_blob_visible()
        self.assertLess(self.widget._menu_sector, 0)

    def test_hidden_state_is_not_written_as_startup_state(self) -> None:
        """Masquer ne doit PAS écrire ``blob_hidden`` dans le fichier."""
        self._click_blob_visible()
        path = paths.appearance_state_file()
        self.assertTrue(path.exists())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertNotIn("blob_hidden", payload)

    def test_tick_keeps_the_blob_hidden(self) -> None:
        self._click_blob_visible()
        for _ in range(5):
            self.widget.tick()
        self.assertFalse(self.widget.isVisible())
        self.assertTrue(self.widget._window_hidden)

    def test_tick_restores_the_blob_when_setting_is_reverted(self) -> None:
        self._click_blob_visible()
        self.assertFalse(self.widget.isVisible())
        # Réactivation depuis l'extérieur (icône de notification).
        self.widget.set_blob_visible(True)
        self.assertTrue(self.widget.isVisible())
        self.assertFalse(self.widget.appearance_state.blob_hidden)
        self.widget.tick()
        self.assertTrue(self.widget.isVisible())
        self.assertFalse(self.widget._window_hidden)

    def test_second_click_shows_the_blob_again(self) -> None:
        self._click_blob_visible()
        self.assertFalse(self.widget.isVisible())
        # Un second appui sur le même item réaffiche l'orbe (bascule).
        self.widget._open_radial_menu(_appearance_sector())
        node = next(n for n in self.widget._menu_nodes if n.label == "Blob Visible")
        node.callback()
        self.assertTrue(self.widget.isVisible())
        self.assertFalse(self.widget.appearance_state.blob_hidden)
        payload = json.loads(paths.appearance_state_file().read_text(encoding="utf-8"))
        self.assertNotIn("blob_hidden", payload)

    def test_backend_keeps_running_while_hidden(self) -> None:
        """L'orbe masqué ne coupe rien : le rendu et l'état vocal continuent."""
        self._click_blob_visible()
        self.assertFalse(self.widget.isVisible())
        jm.set_presence_state("listening")
        jm.set_voice_energy(0.7)
        time_before = self.widget.time
        # tick() doit continuer de fonctionner (et rester masqué) sans lever.
        for _ in range(10):
            self.widget.tick()
        self.assertGreater(self.widget.time, time_before)
        self.assertEqual(jm.presence_state, "listening")
        self.assertFalse(self.widget.isVisible())

    def test_reload_after_hide_starts_visible(self) -> None:
        """Masquer → recharger l'état (équivalent relancement) = visible."""
        self._click_blob_visible()
        self.assertTrue(self.widget.appearance_state.blob_hidden)
        reloaded = appearance_actions.load_state(str(paths.appearance_state_file()))
        self.assertFalse(reloaded.blob_hidden)

    def test_legacy_state_file_defaults_to_visible(self) -> None:
        # Un fichier écrit avant la 1.2.0 ne contient pas « blob_hidden ».
        path = paths.appearance_state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"theme_name": "green", "glow_intensity": 1.0}),
            encoding="utf-8",
        )
        state = appearance_actions.load_state(str(path))
        self.assertFalse(state.blob_hidden)
        self.assertEqual(state.theme_name, "green")

    def test_legacy_payload_with_blob_hidden_true_starts_visible(self) -> None:
        """Ancien payload 1.2.0–1.3.1 ``blob_hidden: true`` → visible."""
        path = paths.appearance_state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "theme_name": "green",
                    "glow_intensity": 1.0,
                    "blob_hidden": True,
                }
            ),
            encoding="utf-8",
        )
        state = appearance_actions.load_state(str(path))
        self.assertFalse(state.blob_hidden)
        self.assertEqual(state.theme_name, "green")


class CloseWhileHiddenRestartTests(unittest.TestCase):
    """Masquer → quitter → relancer = visible (chaque chemin de fermeture)."""

    def _fresh_widget(self):
        widget = jm.MorphingOrbWidget()
        widget.resize(1024, 768)
        widget.show()
        QApplication.processEvents()
        return widget

    def _hide_via_menu(self, widget) -> None:
        widget._open_radial_menu(_appearance_sector())
        node = next(n for n in widget._menu_nodes if n.label == "Blob Visible")
        node.callback()
        QApplication.processEvents()
        self.assertFalse(widget.isVisible())
        self.assertTrue(widget.appearance_state.blob_hidden)

    def test_close_event_while_hidden_then_restart_visible(self) -> None:
        app = _qt_app()
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["JARVIS_DATA_DIR"] = tmp
            try:
                widget = self._fresh_widget()
                self._hide_via_menu(widget)
                # Chemin de fermeture normal (Échap → close(), aboutToQuit…).
                widget.close()
                QApplication.processEvents()
                # « Relancement » : nouvel orbe sur le même répertoire de données.
                widget2 = self._fresh_widget()
                self.assertTrue(widget2.isVisible())
                self.assertFalse(widget2.appearance_state.blob_hidden)
                # Le fichier sur disque ne porte aucune trace du masquage.
                payload = json.loads(
                    paths.appearance_state_file().read_text(encoding="utf-8")
                )
                self.assertNotIn("blob_hidden", payload)
                widget2.close()
                widget2.deleteLater()
                widget.deleteLater()
            finally:
                os.environ.pop("JARVIS_DATA_DIR", None)

    def test_close_while_hidden_never_persists_hidden_flag(self) -> None:
        """Même en forçant save_state en caché, blob_hidden n'est pas écrit."""
        app = _qt_app()  # noqa: F841
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["JARVIS_DATA_DIR"] = tmp
            try:
                widget = self._fresh_widget()
                self._hide_via_menu(widget)
                # Tous les chemins de fermeture passent par save_state via
                # state_to_dict — on force l'écriture pour prouver le contrat.
                appearance_actions.save_state(
                    widget.appearance_state,
                    str(paths.appearance_state_file()),
                )
                payload = json.loads(
                    paths.appearance_state_file().read_text(encoding="utf-8")
                )
                self.assertNotIn("blob_hidden", payload)
                reloaded = appearance_actions.load_state(
                    str(paths.appearance_state_file())
                )
                self.assertFalse(reloaded.blob_hidden)
                widget.close()
                widget.deleteLater()
            finally:
                os.environ.pop("JARVIS_DATA_DIR", None)


class AppearanceStatePersistenceTests(unittest.TestCase):
    """Persistance de la clé d'apparence (sans Qt widget)."""

    def test_state_round_trip(self) -> None:
        state = appearance_actions.AppearanceState()
        self.assertFalse(state.blob_hidden)
        appearance_actions.toggle_blob_visibility(state)
        self.assertTrue(state.blob_hidden)
        appearance_actions.toggle_blob_visibility(state)
        self.assertFalse(state.blob_hidden)

    def test_dict_does_not_contain_blob_hidden(self) -> None:
        state = appearance_actions.AppearanceState()
        state.blob_hidden = True
        self.assertNotIn("blob_hidden", appearance_actions.state_to_dict(state))

    def test_apply_state_dict_forces_visible(self) -> None:
        state = appearance_actions.AppearanceState()
        appearance_actions.apply_state_dict(
            state, {"theme_name": "blue", "blob_hidden": True}
        )
        self.assertFalse(state.blob_hidden)

    def test_set_blob_hidden_is_idempotent(self) -> None:
        state = appearance_actions.AppearanceState()
        appearance_actions.set_blob_hidden(state, True)
        appearance_actions.set_blob_hidden(state, True)
        self.assertTrue(state.blob_hidden)
        appearance_actions.set_blob_hidden(state, False)
        self.assertFalse(state.blob_hidden)

    def test_other_settings_are_preserved(self) -> None:
        # Masquer l'orbe ne doit toucher à aucun autre réglage d'apparence.
        state = appearance_actions.AppearanceState()
        appearance_actions.increase_glow(state)
        appearance_actions.toggle_minimal_mode(state)
        glow_before = state.glow_intensity
        appearance_actions.toggle_blob_visibility(state)
        self.assertTrue(state.blob_hidden)
        self.assertEqual(state.theme_name, "blue")
        self.assertEqual(state.blob_scale, 1.0)
        self.assertEqual(state.glow_intensity, glow_before)
        self.assertTrue(state.minimal_mode)

    def test_restore_hint_exists(self) -> None:
        # L'icône de notification reste le chemin de retour documenté.
        hint = appearance_actions.BLOB_RESTORE_HINT
        self.assertTrue(hint)
        self.assertIsInstance(hint, str)


if __name__ == "__main__":
    unittest.main()
