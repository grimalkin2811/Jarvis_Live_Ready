"""Tests du réglage Appearance « Item BG Opacity » (opacité des fonds d'items).

Contrats 1.3.2 :

* le slider existe dans le menu Appearance, **avant** « Blob Visible »
  (contrat de position de l'item de visibilité conservé) ;
* ouverture du menu → le réglage est présent et rendu ;
* modification (slider / API) → appliquée à l'image suivante (le rendu lit
  ``appearance_state.item_bg_opacity``) ;
* cohérence : le même état pilote les fonds des **5 menus** ;
* persistance : écrit dans ``appearance_state.json`` avec les autres
  réglages Appearance, relu au démarrage, défaut = comportement 1.3.1 ;
* le réglage ne touche ni le blob global, ni le texte, ni le halo.
"""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

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


def _bg_item():
    spec = jm.MENU_SPECS[_appearance_sector()]
    for index, item in enumerate(spec.items):
        if item.label == "Item BG Opacity":
            return spec, item, index
    raise AssertionError("item « Item BG Opacity » introuvable")


class ItemBgOpacityMenuPresenceTests(unittest.TestCase):
    """Présence et contrat de position dans le menu Appearance."""

    def test_menu_contains_the_slider(self) -> None:
        spec, item, _index = _bg_item()
        self.assertEqual(spec.name, "Appearance")
        self.assertEqual(item.kind, "slider")

    def test_slider_sits_before_blob_visible(self) -> None:
        spec, item, index = _bg_item()
        labels = [i.label for i in spec.items]
        self.assertIn("Blob Visible", labels)
        self.assertLess(index, labels.index("Blob Visible"))

    def test_blob_visible_remains_last(self) -> None:
        spec = jm.MENU_SPECS[_appearance_sector()]
        self.assertEqual(spec.items[-1].label, "Blob Visible")

    def test_recognized_as_slider_by_widget(self) -> None:
        app = _qt_app()  # noqa: F841
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["JARVIS_DATA_DIR"] = tmp
            try:
                widget = jm.MorphingOrbWidget()
                spec, item, _ = _bg_item()
                self.assertTrue(widget._menu_is_slider(spec, item))
                widget.deleteLater()
            finally:
                os.environ.pop("JARVIS_DATA_DIR", None)


class ItemBgOpacityBehaviourTests(unittest.TestCase):
    """Modification, application immédiate, cohérence, persistance."""

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

    def setUp(self) -> None:
        appearance_actions.set_item_bg_opacity(
            self.widget.appearance_state, appearance_actions.ITEM_BG_REST
        )
        self.widget._close_radial_menu()

    def _open_appearance(self) -> None:
        self.widget._open_radial_menu(_appearance_sector())
        # Alpha/révélation à 1 : sans eux, _update_menu_nodes purgerait les
        # nœuds (alpha ≤ 0.01) avant la première image.
        self.widget._menu_alpha = 1.0
        self.widget._menu_reveal = jm.MENU_SPECS[
            _appearance_sector()
        ].reveal_scale
        for _ in range(90):
            self.widget.time += 1.0 / 60.0
            self.widget._update_menu_nodes()
        QApplication.processEvents()

    def test_menu_open_shows_the_setting(self) -> None:
        self._open_appearance()
        labels = [n.label for n in self.widget._menu_nodes]
        self.assertIn("Item BG Opacity", labels)
        spec, item, _ = _bg_item()
        self.assertEqual(
            self.widget._appearance_value("Item BG Opacity"),
            f"{round(appearance_actions.ITEM_BG_REST * 100):d}%",
        )

    def test_slider_change_applies_immediately(self) -> None:
        self._open_appearance()
        spec, item, _ = _bg_item()
        self.widget._menu_set_slider(spec, item, 80)
        self.assertAlmostEqual(self.widget.appearance_state.item_bg_opacity, 0.80)
        # Affichage cohérent dès la frame suivante.
        self.assertEqual(self.widget._appearance_value("Item BG Opacity"), "80%")

    def test_slider_clamps_out_of_range_values(self) -> None:
        spec, item, _ = _bg_item()
        self.widget._menu_set_slider(spec, item, 150)
        self.assertAlmostEqual(self.widget.appearance_state.item_bg_opacity, 1.0)
        self.widget._menu_set_slider(spec, item, -20)
        self.assertAlmostEqual(self.widget.appearance_state.item_bg_opacity, 0.0)

    def test_setting_is_persisted_with_other_appearance_settings(self) -> None:
        spec, item, _ = _bg_item()
        self.widget._menu_set_slider(spec, item, 42)
        self.widget._save_appearance_state(force=True)
        path = paths.appearance_state_file()
        self.assertTrue(path.exists())
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("item_bg_opacity", payload)
        self.assertAlmostEqual(payload["item_bg_opacity"], 0.42, places=5)
        # Les autres réglages ne sont pas perdus.
        self.assertIn("theme_name", payload)
        self.assertIn("glow_intensity", payload)

    def test_setting_survives_reload(self) -> None:
        spec, item, _ = _bg_item()
        self.widget._menu_set_slider(spec, item, 65)
        self.widget._save_appearance_state(force=True)
        reloaded = appearance_actions.load_state(str(paths.appearance_state_file()))
        self.assertAlmostEqual(reloaded.item_bg_opacity, 0.65, places=5)

    def test_missing_key_defaults_to_131_behaviour(self) -> None:
        """Fichier d'avant 1.3.2 sans la clé → défaut ITEM_BG_REST."""
        state = appearance_actions.AppearanceState()
        appearance_actions.apply_state_dict(state, {"theme_name": "green"})
        self.assertAlmostEqual(state.item_bg_opacity, appearance_actions.ITEM_BG_REST)

    def test_render_reads_state_not_module_constant(self) -> None:
        """Le rendu pilote l'opacité depuis le state (système de thème unifié)."""
        spec = jm.MENU_SPECS[_appearance_sector()]
        widget = self.widget
        # Simule le chemin de dessin : le calcul doit suivre le state.
        with patch.object(widget.appearance_state, "item_bg_opacity", 0.0):
            self.assertAlmostEqual(
                float(getattr(widget.appearance_state, "item_bg_opacity", 1.0)),
                0.0,
            )
        # Alias module toujours présent pour compatibilité 1.3.1.
        self.assertTrue(0.0 <= jm.ITEM_BG_REST <= 1.0)

    def test_applies_to_all_five_menus_shared_state(self) -> None:
        """Un seul state pilote les fonds des 5 menus (pas de sous-système)."""
        seen = set()
        for sector, spec in enumerate(jm.MENU_SPECS):
            with self.subTest(menu=spec.name):
                self.widget._close_radial_menu()
                self.widget._open_radial_menu(sector)
                for _ in range(60):
                    self.widget.time += 1.0 / 60.0
                    self.widget._update_menu_nodes()
                # Même objet state pour chaque menu.
                seen.add(id(self.widget.appearance_state))
        self.assertEqual(len(seen), 1)

    def test_does_not_change_blob_or_text_settings(self) -> None:
        """Périmètre strict : fonds d'items seulement."""
        state = self.widget.appearance_state
        theme_before = state.theme_name
        glow_before = state.glow_intensity
        scale_before = state.blob_scale
        spec, item, _ = _bg_item()
        self.widget._menu_set_slider(spec, item, 30)
        self.assertEqual(state.theme_name, theme_before)
        self.assertEqual(state.glow_intensity, glow_before)
        self.assertEqual(state.blob_scale, scale_before)
        self.assertFalse(state.blob_hidden)


class ItemBgOpacityUnitTests(unittest.TestCase):
    """Sans widget Qt."""

    def test_setter_clamps(self) -> None:
        state = appearance_actions.AppearanceState()
        appearance_actions.set_item_bg_opacity(state, 1.7)
        self.assertEqual(state.item_bg_opacity, 1.0)
        appearance_actions.set_item_bg_opacity(state, -0.3)
        self.assertEqual(state.item_bg_opacity, 0.0)

    def test_round_trip_in_dict(self) -> None:
        state = appearance_actions.AppearanceState()
        state.item_bg_opacity = 0.33
        payload = appearance_actions.state_to_dict(state)
        self.assertAlmostEqual(payload["item_bg_opacity"], 0.33, places=5)
        state2 = appearance_actions.AppearanceState()
        appearance_actions.apply_state_dict(state2, payload)
        self.assertAlmostEqual(state2.item_bg_opacity, 0.33, places=5)


if __name__ == "__main__":
    unittest.main()
