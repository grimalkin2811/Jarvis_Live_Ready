"""Tests de non-régression du layout des menus radiaux (1.3.2).

Contrats vérifiés, pour CHAQUE des 5 menus (Voice, System, Memory,
Appearance, Routines) et sans redessin de l'UI :

* **zéro chevauchement** libellé ∩ libellé, libellé ∩ pastille, pastille ∩
  pastille (pire cas survol/clic) à l'issue de la stabilisation ;
* les nœuds restent dans la fenêtre (lisibles / atteignables) ;
* chaque item possède un callback non nœud orphelin (clics possibles) ;
* le solveur ne déplace pas un menu sain (Design 1.3.1 conservé) ;
* mode grille ET mode ligne sont couverts (orientation du secteur).

Le rapport vient de ``MorphingOrbWidget._menu_layout_overlaps()``, source
unique partagée avec ``scripts/diag_menu_overlap.py``.
"""

import os
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from UI import jarvis_menu as jm  # noqa: E402


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _settle(widget, frames: int = 150) -> None:
    for _ in range(frames):
        widget.time += 1.0 / 60.0
        widget._update_menu_nodes()


class MenuLayoutOverlapTests(unittest.TestCase):
    """Aucun chevauchement dans les 5 menus, tous secteurs."""

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

    def test_all_menus_have_zero_overlap(self) -> None:
        widget = self.widget
        for sector, spec in enumerate(jm.MENU_SPECS):
            with self.subTest(menu=spec.name):
                widget._close_radial_menu()
                widget._open_radial_menu(sector)
                widget._menu_alpha = 1.0
                widget._menu_reveal = 1.0
                _settle(widget)
                for node in widget._menu_nodes:
                    node.visible_amount = 1.0
                report = widget._menu_layout_overlaps()
                self.assertEqual(
                    report,
                    [],
                    f"{spec.name} : chevauchements détectés :\n"
                    + "\n".join(report),
                )
                self.assertEqual(len(widget._menu_nodes), len(spec.items))

    def test_all_menus_zero_overlap_in_both_orientations(self) -> None:
        """Force grille et ligne en patchant le vecteur de secteur.

        ``_update_menu_nodes`` déduit le mode de ``|sy| vs |sx|`` : forcer
        ``_menu_layout_mode`` serait écrasé à la frame suivante. On simule
        donc l'orientation réelle via ``_sector_vector``.
        """
        widget = self.widget
        orientations = {
            "grid": (0.0, -1.0),   # sy dominant → grille
            "line": (1.0, 0.0),    # sx dominant → ligne
        }
        for sector, spec in enumerate(jm.MENU_SPECS):
            for mode, vec in orientations.items():
                with self.subTest(menu=spec.name, mode=mode):
                    original = widget._sector_vector
                    widget._sector_vector = lambda _s, v=vec: v
                    try:
                        widget._close_radial_menu()
                        widget._open_radial_menu(sector)
                        widget._menu_alpha = 1.0
                        widget._menu_reveal = 1.0
                        widget._layout_solved = None  # reset warm-start
                        _settle(widget, 150)
                        for node in widget._menu_nodes:
                            node.visible_amount = 1.0
                        self.assertEqual(widget._menu_layout_mode, mode)
                        report = widget._menu_layout_overlaps()
                        self.assertEqual(
                            report,
                            [],
                            f"{spec.name}/{mode} :\n" + "\n".join(report),
                        )
                    finally:
                        widget._sector_vector = original
                        widget._layout_solved = None

    def test_nodes_stay_inside_the_window(self) -> None:
        widget = self.widget
        rect = widget.rect()
        for sector, spec in enumerate(jm.MENU_SPECS):
            with self.subTest(menu=spec.name):
                widget._close_radial_menu()
                widget._open_radial_menu(sector)
                widget._menu_alpha = 1.0
                widget._menu_reveal = 1.0
                _settle(widget)
                self.assertTrue(widget._menu_nodes)
                for node in widget._menu_nodes:
                    # Marge : libellé + barre dépassent le pastille (~80 px max).
                    self.assertTrue(
                        rect.adjusted(-120, -120, 120, 120).contains(
                            node.position.toPoint()
                        ),
                        f"{spec.name}/{node.label} hors fenêtre : "
                        f"{node.position}",
                    )

    def test_every_node_has_a_click_callback(self) -> None:
        widget = self.widget
        for sector, spec in enumerate(jm.MENU_SPECS):
            with self.subTest(menu=spec.name):
                widget._close_radial_menu()
                widget._open_radial_menu(sector)
                _settle(widget, 60)
                for node in widget._menu_nodes:
                    self.assertIsNotNone(
                        node.callback,
                        f"{spec.name}/{node.label} sans callback (non cliquable)",
                    )

    def test_voice_menu_item_count_unchanged(self) -> None:
        """Le layout ne doit ni ajouter ni retirer d'item (Voice = 10)."""
        voice = next(s for s in jm.MENU_SPECS if s.name == "Voice")
        self.assertEqual(len(voice.items), 10)

    def test_healthy_layout_stays_near_base_spacings(self) -> None:
        """Un menu sans conflit garde le design d'origine (col 126 / row 46)."""
        widget = self.widget
        # Memory (3 items) tient dans le design historique.
        sector = next(
            i for i, s in enumerate(jm.MENU_SPECS) if s.name == "Memory"
        )
        widget._close_radial_menu()
        widget._open_radial_menu(sector)
        widget._menu_alpha = 1.0
        widget._menu_reveal = 1.0
        _settle(widget)
        solved = widget._layout_solved
        self.assertIsNotNone(solved)
        _name, _mode, col, row, line = solved
        self.assertLessEqual(
            col,
            200.0,
            f"Memory : colonne trop élargie sans conflit apparent ({col})",
        )

    def test_solver_warm_start_is_stable_across_frames(self) -> None:
        """Pas d'oscillation : la solution ne change plus après stabilisation."""
        widget = self.widget
        sector = 0  # menu à plusieurs items (Voice)
        widget._close_radial_menu()
        widget._open_radial_menu(sector)
        widget._menu_alpha = 1.0
        widget._menu_reveal = 1.0
        _settle(widget, 200)
        first = widget._layout_solved
        _settle(widget, 30)
        second = widget._layout_solved
        self.assertEqual(first, second)

    def test_all_menus_stay_compact_enough_for_the_window(self) -> None:
        """Le solveur ne doit pas faire sortir les nœuds de la fenêtre."""
        widget = self.widget
        rect = widget.rect().adjusted(-40, -40, 40, 40)
        for sector, spec in enumerate(jm.MENU_SPECS):
            with self.subTest(menu=spec.name):
                widget._close_radial_menu()
                widget._open_radial_menu(sector)
                widget._menu_alpha = 1.0
                widget._menu_reveal = 1.0
                _settle(widget)
                for node in widget._menu_nodes:
                    self.assertTrue(
                        rect.contains(node.position.toPoint()),
                        f"{spec.name}/{node.label} hors fenêtre après solve : "
                        f"{node.position}",
                    )
                # Le pas de rang reste proche du design (pas d'explosion).
                solved = widget._layout_solved
                self.assertIsNotNone(solved)
                _name, _mode, _col, row, _line = solved
                self.assertLessEqual(
                    row,
                    80.0,
                    f"{spec.name} : pas de rang excessif ({row})",
                )


class LayoutSourceOfTruthTests(unittest.TestCase):
    """La position cible est calculée par la SOURCE UNIQUE partagée."""

    def test_layout_targets_match_used_positions(self) -> None:
        app = _qt_app()  # noqa: F841
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["JARVIS_DATA_DIR"] = tmp
            try:
                widget = jm.MorphingOrbWidget()
                widget.resize(1280, 800)
                widget.show()
                sector = 0
                widget._open_radial_menu(sector)
                widget._menu_alpha = 1.0
                widget._menu_reveal = 1.0
                for _ in range(200):
                    widget.time += 1.0 / 60.0
                    widget._update_menu_nodes()
                spec = widget._menu_spec()
                anchor, sx, sy, perp_x, perp_y = widget._menu_anchor(spec, 1.0)
                curve = 7.0 + 5.0 * 1.0
                col, row, line = widget._solve_layout_spacings(
                    spec, sx, sy, perp_x, perp_y, curve
                )
                targets = widget._layout_targets(
                    anchor, sx, sy, perp_x, perp_y, curve, col, row, line
                )
                self.assertEqual(len(targets), len(widget._menu_nodes))
                for node, target in zip(widget._menu_nodes, targets):
                    self.assertLess(
                        (node.position - QPointF(target)).manhattanLength(),
                        6.0,
                        "position animée non stabilisée sur la cible du solveur",
                    )
                widget.close()
                widget.deleteLater()
            finally:
                os.environ.pop("JARVIS_DATA_DIR", None)


if __name__ == "__main__":
    unittest.main()
