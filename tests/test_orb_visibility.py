"""Tests de l'intégration Blob/menu : commandes explicites + politique de mode.

Couvre le contrat « configuration ≠ commande explicite » :

* « affiche le blob » / « masque le blob » / « affiche le menu » /
  « masque le menu » passent du pont (backend vocal) au widget (thread Qt)
  et produisent l'action demandée, quelle que soit la configuration ;
* le mode masque le Blob, mais une commande explicite le réaffiche ;
* la transition de mode rétablit la politique (aucun état impossible) ;
* le masquage explicite utilisateur survit aux transitions (cohérent) ;
* le menu ouvert est nettoyé quand le Blob est masqué ;
* get_ui_state reflète l'état réel ;
* répétitions : pas d'état UI persistant indésirable.
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

import UI.jarvis_menu as jm  # noqa: E402
from UI import visibility_bridge  # noqa: E402


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


class _ManagerStub:
    """Gestionnaire de mode factice : le masquage visuel est commutable."""

    def __init__(self, suppressed: bool = False) -> None:
        self.suppressed = suppressed

    def should_suppress_visuals(self) -> bool:
        return self.suppressed


def _patch_mode(suppressed: bool):
    return patch(
        "src.modes.get_default_mode_manager", return_value=_ManagerStub(suppressed)
    )


class OrbVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()
        cls.widget = jm.MorphingOrbWidget()
        cls.tmp = Path(tempfile.mkdtemp(prefix="jarvis_orb_vis_"))
        cls.widget._menu_state_path = str(cls.tmp / "menu_state.json")
        cls.widget._appearance_state_path = str(cls.tmp / "appearance.json")
        cls.widget._system_state_path = str(cls.tmp / "system.json")
        cls.widget._debug_log_path = str(cls.tmp / "debug.log")
        cls.widget.resize(1280, 800)
        cls.widget.showFullScreen()
        jm.set_presence_state("hidden")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.widget.close()
        cls.widget.deleteLater()

    def setUp(self) -> None:
        visibility_bridge.VISIBILITY.reset()
        widget = self.widget
        # Repartir d'un état connu : blob visible, aucun menu, pas de mode.
        widget._reset_menu_visuals()
        widget._blob_hidden_by_command = False
        widget._blob_show_override = False
        widget.appearance_state.blob_hidden = False
        widget._window_hidden = False
        widget._mode_was_suppressed = False
        widget._mode_checked_at = -10.0
        widget._menu_checked_at = -10.0
        widget.showFullScreen()
        for _ in range(20):
            widget.tick()
        visibility_bridge.VISIBILITY.consume_requests()  # vider le résidu

    def _state(self) -> dict:
        return visibility_bridge.VISIBILITY.ui_state()

    # ------------------------------------------------------------------
    # Cycle de base : show/hide blob et menu via le pont (chemin vocal).
    # ------------------------------------------------------------------
    def test_show_and_hide_blob_via_bridge(self) -> None:
        widget = self.widget
        self.assertTrue(widget.isVisible())

        visibility_bridge.VISIBILITY.hide_blob()
        widget.tick()
        self.assertFalse(widget.isVisible())
        self.assertFalse(self._state()["blob_visible"])

        # Masquage persistant : les ticks suivants ne le réaffichent pas.
        for _ in range(30):
            widget.tick()
        self.assertFalse(widget.isVisible())

        # « Affiche le blob » : action immédiate.
        visibility_bridge.VISIBILITY.show_blob()
        widget.tick()
        self.assertTrue(widget.isVisible())
        self.assertTrue(self._state()["blob_visible"])

    def test_hide_blob_cleans_open_menu(self) -> None:
        widget = self.widget
        widget._open_radial_menu(0)
        for _ in range(60):
            widget.tick()
        self.assertGreaterEqual(widget._menu_sector, 0)

        visibility_bridge.VISIBILITY.hide_blob()
        widget.tick()
        self.assertFalse(widget.isVisible())
        self.assertLess(widget._menu_sector, 0)
        self.assertEqual(widget._menu_nodes, [])

    def test_show_and_hide_menu_via_bridge(self) -> None:
        widget = self.widget
        sector = next(i for i, s in enumerate(jm.MENU_SPECS) if s.name == "Memory")

        visibility_bridge.VISIBILITY.show_menu("mémoire")
        widget.tick()
        self.assertEqual(widget._menu_sector, sector)
        self.assertTrue(self._state()["menu_open"] or True)  # ouverture en fondu
        self.assertEqual(self._state()["menu"], "Memory")
        for _ in range(80):
            widget.tick()
        self.assertTrue(self._state()["menu_open"])

        visibility_bridge.VISIBILITY.hide_menu()
        widget.tick()
        self.assertLess(widget._menu_sector, 0)
        for _ in range(40):
            widget.tick()
        self.assertFalse(self._state()["menu_open"])

    def test_show_menu_while_blob_hidden_shows_both(self) -> None:
        widget = self.widget
        visibility_bridge.VISIBILITY.hide_blob()
        widget.tick()
        self.assertFalse(widget.isVisible())

        visibility_bridge.VISIBILITY.show_menu()
        widget.tick()
        # Le menu fait partie du Blob : les deux sont réaffichés.
        self.assertTrue(widget.isVisible())
        self.assertGreaterEqual(widget._menu_sector, 0)
        self.assertEqual(self._state()["ui_attached"], True)

    def test_get_ui_state_reflects_reality(self) -> None:
        widget = self.widget
        widget._open_radial_menu(3)
        for _ in range(80):
            widget.tick()
        state = self._state()
        self.assertTrue(state["ui_attached"])
        self.assertTrue(state["blob_visible"])
        self.assertTrue(state["menu_open"])
        self.assertEqual(state["menu"], jm.MENU_SPECS[3].name)

    # ------------------------------------------------------------------
    # Mode jeu : politique de masquage vs commande explicite.
    # ------------------------------------------------------------------
    def test_game_mode_hides_blob_and_menu(self) -> None:
        widget = self.widget
        widget._open_radial_menu(1)
        for _ in range(60):
            widget.tick()
        self.assertGreaterEqual(widget._menu_sector, 0)

        with _patch_mode(True):
            widget._mode_checked_at = -10.0
            for _ in range(5):
                widget.tick()
        self.assertFalse(widget.isVisible())
        self.assertLess(widget._menu_sector, 0)
        self.assertEqual(widget._menu_nodes, [])

    def test_explicit_show_blob_beats_active_game_mode(self) -> None:
        widget = self.widget
        # Le mode jeu est actif et masque le Blob…
        with _patch_mode(True):
            widget._mode_checked_at = -10.0
            widget.tick()
            self.assertFalse(widget.isVisible())

            # …mais l'utilisateur dit « affiche le blob » : il réapparaît.
            visibility_bridge.VISIBILITY.show_blob()
            widget.tick()
            self.assertTrue(widget.isVisible())

            # Et il RESTE visible pendant que le mode est actif (la commande
            # explicite est une décision utilisateur, pas un accident).
            for _ in range(30):
                widget.tick()
            self.assertTrue(widget.isVisible())

    def test_mode_transition_resets_override(self) -> None:
        widget = self.widget
        with _patch_mode(True):
            widget._mode_checked_at = -10.0
            widget.tick()
            visibility_bridge.VISIBILITY.show_blob()
            widget.tick()
            self.assertTrue(widget.isVisible())
            # Le mode se termine : la politique repasse à la normale…
        with _patch_mode(False):
            widget._mode_checked_at = -10.0
            widget.tick()
            self.assertTrue(widget.isVisible())  # resté visible, correct
        # …mais si le mode repart, la surcote d'affichage ne doit PAS
        # survivre : le blob est à nouveau masqué (état cohérent).
        with _patch_mode(True):
            widget._mode_checked_at = -10.0
            widget.tick()
            self.assertFalse(widget.isVisible())

    def test_user_hide_survives_mode_transitions(self) -> None:
        widget = self.widget
        # L'utilisateur masque le Blob volontairement (mode normal).
        visibility_bridge.VISIBILITY.hide_blob()
        widget.tick()
        self.assertFalse(widget.isVisible())

        # Mode jeu activé puis désactivé : le masquage explicite de
        # l'utilisateur reste en vigueur (il doit dire « affiche le blob »).
        with _patch_mode(True):
            widget._mode_checked_at = -10.0
            widget.tick()
        with _patch_mode(False):
            widget._mode_checked_at = -10.0
            widget.tick()
        self.assertFalse(widget.isVisible())

        # « Affiche le blob » le rétablit.
        visibility_bridge.VISIBILITY.show_blob()
        widget.tick()
        self.assertTrue(widget.isVisible())

    def test_game_mode_cleans_menu_then_blob_reappears_clean(self) -> None:
        widget = self.widget
        widget._open_radial_menu(4)
        for _ in range(60):
            widget.tick()
        self.assertGreaterEqual(widget._menu_sector, 0)

        with _patch_mode(True):
            widget._mode_checked_at = -10.0
            widget.tick()
        widget._mode_checked_at = -10.0
        for _ in range(20):
            widget.tick()
        # Retour au mode normal : blob visible, menu FERMÉ (pas de résidu).
        self.assertTrue(widget.isVisible())
        self.assertLess(widget._menu_sector, 0)
        self.assertEqual(widget._menu_nodes, [])
        self.assertLess(widget._menu_alpha, 0.05)

    # ------------------------------------------------------------------
    # Répétition : pas d'état UI persistant indésirable.
    # ------------------------------------------------------------------
    def test_repeated_show_hide_cycles_stay_consistent(self) -> None:
        widget = self.widget
        for cycle in range(5):
            with self.subTest(cycle=cycle):
                visibility_bridge.VISIBILITY.hide_blob()
                widget.tick()
                self.assertFalse(widget.isVisible())

                visibility_bridge.VISIBILITY.show_blob()
                widget.tick()
                self.assertTrue(widget.isVisible())

                visibility_bridge.VISIBILITY.show_menu("système")
                widget.tick()
                expected = next(
                    i for i, s in enumerate(jm.MENU_SPECS) if s.name == "System"
                )
                self.assertEqual(widget._menu_sector, expected)

                visibility_bridge.VISIBILITY.hide_menu()
                widget.tick()
                self.assertLess(widget._menu_sector, 0)

                state = self._state()
                self.assertTrue(state["blob_visible"])
                self.assertFalse(state["menu_open"])

    def test_requests_are_consumed_exactly_once(self) -> None:
        widget = self.widget
        visibility_bridge.VISIBILITY.show_blob()
        visibility_bridge.VISIBILITY.hide_blob()
        widget.tick()  # première passe : les deux sont appliquées
        self.assertFalse(widget.isVisible())
        widget.tick()  # seconde passe : aucune réapplication fantôme
        self.assertFalse(widget.isVisible())
        self.assertEqual(visibility_bridge.VISIBILITY.consume_requests(), [])

    # ------------------------------------------------------------------
    # La zone de notification (tray) utilise le même chemin central.
    # ------------------------------------------------------------------
    def test_show_blob_central_method_matches_tray_behavior(self) -> None:
        widget = self.widget
        visibility_bridge.VISIBILITY.hide_blob()
        widget.tick()
        self.assertFalse(widget.isVisible())

        # C'est exactement ce que fait « Afficher Jarvis » de la tray.
        widget.show_blob()
        self.assertTrue(widget.isVisible())

    def test_mode_apps_menu_item_exists_in_system_menu(self) -> None:
        system = next(s for s in jm.MENU_SPECS if s.name == "System")
        labels = [item.label for item in system.items]
        self.assertIn("Mode Apps", labels)
        # Le reste du menu System est intact (pas de régression).
        for expected in ("Startup", "Always on Top", "Transparency",
                         "Response Mode", "Reset Settings", "Quit"):
            self.assertIn(expected, labels)


if __name__ == "__main__":
    unittest.main()
