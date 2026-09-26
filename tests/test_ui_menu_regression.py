"""Garde-fous de non-régression UI : fonds d'items + anti-chevauchement.

Contexte
--------
La release v1.5.0 a été étiquetée sur la tête de la branche Deezer (PR #27),
coupée depuis ``v1.1.2``. Ce commit ne contenait donc ni les fonds d'items
des menus radiaux (1.3.1/1.3.2) ni le solveur de layout anti-chevauchement
(1.3.2) : l'application publiée affichait des libellés sans fond et un menu
Voice dont les libellés se superposaient. ``main`` n'a jamais perdu ces
fonctionnalités, mais RIEN dans le dépôt ne l'attestait de façon
auto-suffisante : la branche publiée transportait ses propres tests
(anciens), donc la CI était verte.

Ce module verrouille explicitement le contrat des deux fonctionnalités
concernées, en complément de ``test_menu_backgrounds`` (cycle complet),
``test_menu_layout`` (géométrie) et ``test_item_bg_opacity`` (réglage) :

1. le contrat existe dans le code (API + réglage) — un arbre antérieur à la
   1.3.2 échoue immédiatement, sans même rendre une image ;
2. menu ouvert → CHAQUE item a un fond, sans survol ;
3. ce fond n'est PAS un highlight permanent : il reste nettement en retrait
   du fond survolé (sinon tous les items paraissent sélectionnés) ;
4. le survol reste distinct et mesurable ;
5. menu fermé → plus aucun fond ;
6. menu Voice (12 items depuis le Writing Mode 1.4.0) : fonds présents,
   ZÉRO chevauchement, nœuds dans la fenêtre.

Les mesures portent sur les PIXELS réels (``grab()`` offscreen), moyennés
sur le rectangle de libellé calculé par le solveur de layout — c'est-à-dire
exactement la zone où le fond est censé être peint.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import UI.jarvis_menu as jm  # noqa: E402
from UI import appearance_actions, visibility_bridge  # noqa: E402

MENUS = ("Voice", "System", "Memory", "Appearance", "Routines")

#: Contribution minimale (alpha moyen) du FOND sur le rectangle du libellé.
#: Mesure DIFFÉRENTIELLE : rendu normal moins rendu « fond désactivé ». Une
#: valeur absolue ne prouverait rien (texte, pastille et halo de l'orbe
#: marquent déjà les pixels — c'est ainsi que la 1.3.0 passait ses tests
#: alors qu'aucun fond n'était visible).
REST_BG_FLOOR = 15
#: Alpha moyen maximal quand le menu est fermé (plus rien ne doit rester).
BG_GONE = 12
#: Le fond au repos doit rester nettement en dessous du fond survolé, sinon
#: tous les items paraissent sélectionnés en permanence.
REST_OVER_HOVER_MAX = 0.85
#: Écart minimal (contribution du fond) entre repos et survol : le survol
#: doit rester perceptible.
HOVER_DELTA_MIN = 8


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _settle(widget: "jm.MorphingOrbWidget", frames: int = 120) -> None:
    for _ in range(frames):
        widget.tick()


def _force_fully_open(widget: "jm.MorphingOrbWidget") -> None:
    """Fige le menu en position totalement ouverte (fin des fondus)."""
    widget._menu_alpha = 1.0
    widget._menu_reveal = 1.0
    for _ in range(40):
        widget._update_menu_nodes()
    for node in widget._menu_nodes:
        node.visible_amount = 1.0


class _MenuRenderCase(unittest.TestCase):
    """Base commune : un orbe isolé, offscreen, de taille fixe."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()
        # Isoler AVANT de construire le widget : __init__ lit l'apparence et
        # l'état du menu depuis JARVIS_DATA_DIR.
        cls.tmp = Path(tempfile.mkdtemp(prefix="jarvis_ui_regression_"))
        cls._prev_data_dir = os.environ.get("JARVIS_DATA_DIR")
        os.environ["JARVIS_DATA_DIR"] = str(cls.tmp)
        cls.widget = jm.MorphingOrbWidget()
        cls.widget._menu_state_path = str(cls.tmp / "menu_state.json")
        cls.widget._appearance_state_path = str(cls.tmp / "appearance.json")
        cls.widget._system_state_path = str(cls.tmp / "system.json")
        cls.widget._debug_log_path = str(cls.tmp / "debug.log")
        cls.widget.menu_state = jm.menu_state.MenuState()
        cls.widget.setFixedSize(1280, 800)
        cls.widget.show()
        jm.set_voice_energy(0.0)
        jm.set_presence_state("hidden")
        cls.sectors = {spec.name: index for index, spec in enumerate(jm.MENU_SPECS)}

    @classmethod
    def tearDownClass(cls) -> None:
        cls.widget._close_radial_menu()
        cls.widget.close()
        cls.widget.deleteLater()
        jm.set_voice_energy(0.0)
        jm.set_presence_state("hidden")
        if cls._prev_data_dir is None:
            os.environ.pop("JARVIS_DATA_DIR", None)
        else:
            os.environ["JARVIS_DATA_DIR"] = cls._prev_data_dir

    def setUp(self) -> None:
        visibility_bridge.VISIBILITY.reset()
        # Le halo (énergie vocale / présence) laissé actif par un module de
        # tests précédent ajoute de l'alpha autour de l'orbe et fausse les
        # mesures. Idem pour la taille : tick() rappelle showFullScreen().
        jm.set_voice_energy(0.0)
        jm.set_presence_state("hidden")
        widget = self.widget
        widget._halo_energy = 0.0
        widget._presence_energy = 0.0
        widget._close_radial_menu()
        widget._reset_menu_visuals()
        widget._blob_hidden_by_command = False
        widget.appearance_state.blob_hidden = False
        widget.appearance_state.item_bg_opacity = appearance_actions.ITEM_BG_REST
        widget.cursor = widget.center
        widget._menu_checked_at = -10.0
        widget._mode_checked_at = -10.0

        def _show_fixed() -> None:
            widget.setFixedSize(1280, 800)
            super(jm.MorphingOrbWidget, widget).show()

        widget.showFullScreen = _show_fixed
        widget.setFixedSize(1280, 800)
        widget.show()
        _settle(widget, 30)

    # -- outillage de mesure -------------------------------------------
    def _label_boxes(self):
        widget = self.widget
        spec = widget._menu_spec()
        self.assertIsNotNone(spec)
        positions = [QPointF(node.position) for node in widget._menu_nodes]
        widths, heights, shifts = widget._layout_label_metrics(spec)
        return widget._layout_boxes(spec, positions, widths, heights, shifts)

    def _mean_alpha(self, image, box) -> float:
        """Alpha moyen sur le rectangle du libellé (pas un pixel isolé)."""
        x0, y0, x1, y1 = (int(round(value)) for value in box)
        x0 = max(0, min(image.width() - 1, x0))
        x1 = max(0, min(image.width(), x1))
        y0 = max(0, min(image.height() - 1, y0))
        y1 = max(0, min(image.height(), y1))
        total = 0
        count = 0
        for x in range(x0, max(x0 + 1, x1)):
            for y in range(y0, max(y0 + 1, y1)):
                total += image.pixelColor(x, y).alpha()
                count += 1
        return total / max(1, count)

    def _open(self, name: str):
        widget = self.widget
        widget.cursor = widget.center
        widget._open_radial_menu(self.sectors[name])
        _settle(widget)
        _force_fully_open(widget)
        self.assertTrue(widget._menu_nodes, name)
        return widget

    def _clear_hover(self) -> None:
        widget = self.widget
        for node in widget._menu_nodes:
            node.hover_amount = 0.0
            node.click_amount = 0.0
        widget._menu_focus_index = -1

    def _background_contributions(self, name: str, hovered: int = -1):
        """Contribution du FOND seul, item par item (mesure différentielle).

        Deux rendus du même menu : l'un normal, l'autre avec
        ``item_bg_opacity`` forcé à 0 ET aucun survol — c'est-à-dire sans
        aucun fond d'item. La différence d'alpha moyen sur le rectangle du
        libellé isole la contribution du fond, indépendamment du texte, de
        la pastille et du halo de l'orbe.
        """
        widget = self._open(name)
        self._clear_hover()
        boxes = self._label_boxes()

        previous = widget.appearance_state.item_bg_opacity
        widget.appearance_state.item_bg_opacity = 0.0
        base = widget.grab().toImage()
        widget.appearance_state.item_bg_opacity = previous

        if 0 <= hovered < len(widget._menu_nodes):
            widget._menu_nodes[hovered].hover_amount = 1.0
        painted = widget.grab().toImage()
        self._clear_hover()

        return [
            self._mean_alpha(painted, label_box) - self._mean_alpha(base, label_box)
            for label_box, _body_box in boxes
        ]


class UiContractPresenceTests(unittest.TestCase):
    """Le contrat existe dans le CODE — échoue sur tout arbre < 1.3.2."""

    def test_item_background_api_is_present(self) -> None:
        for attribute in ("_draw_hover_background", "_draw_menu_node"):
            self.assertTrue(
                hasattr(jm.MorphingOrbWidget, attribute),
                f"API du fond d'item absente : {attribute}",
            )
        self.assertTrue(hasattr(jm, "hover_background_color"))
        self.assertTrue(hasattr(jm, "hover_border_color"))

    def test_layout_solver_api_is_present(self) -> None:
        for attribute in (
            "_solve_layout_spacings",
            "_layout_targets",
            "_layout_boxes",
            "_layout_overlap_scan",
            "_menu_layout_overlaps",
        ):
            self.assertTrue(
                hasattr(jm.MorphingOrbWidget, attribute),
                f"API du solveur anti-chevauchement absente : {attribute}",
            )

    def test_item_bg_opacity_setting_is_present(self) -> None:
        state = appearance_actions.AppearanceState()
        self.assertTrue(hasattr(state, "item_bg_opacity"))
        self.assertIn("item_bg_opacity", appearance_actions.state_to_dict(state))

    def test_default_opacity_is_partial_not_a_permanent_highlight(self) -> None:
        """Défaut strictement entre 0 et 1.

        ``0.0`` ramènerait au fond « uniquement au survol » (régression 1.3.0),
        ``1.0`` ferait paraître TOUS les items sélectionnés en permanence.
        """
        default = appearance_actions.ITEM_BG_REST
        self.assertGreater(default, 0.0)
        self.assertLess(default, 1.0)
        self.assertEqual(
            appearance_actions.AppearanceState().item_bg_opacity, default
        )

    def test_voice_menu_keeps_its_writing_items(self) -> None:
        """Le menu Voice de la 1.4.x (12 items) ne doit pas revenir en arrière."""
        voice = next(spec for spec in jm.MENU_SPECS if spec.name == "Voice")
        labels = [item.label for item in voice.items]
        self.assertEqual(len(labels), 12, labels)
        self.assertIn("Active Field", labels)
        self.assertIn("Text Files", labels)

    def test_release_guard_sees_a_complete_ui_contract(self) -> None:
        """Le garde-fou de publication doit être satisfait par cet arbre."""
        sys.path.insert(
            0,
            os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "..", "scripts"
            ),
        )
        import check_release_lineage

        self.assertEqual(check_release_lineage.check_ui_contract(), [])


class MenuItemBackgroundContractTests(_MenuRenderCase):
    """Fonds d'items : présents, en retrait du survol, absents menu fermé."""

    def test_open_menu_paints_a_background_behind_every_item(self) -> None:
        for name in MENUS:
            with self.subTest(menu=name):
                contributions = self._background_contributions(name)
                self.assertTrue(contributions)
                for index, amount in enumerate(contributions):
                    label = self.widget._menu_nodes[index].label
                    self.assertGreaterEqual(
                        amount,
                        REST_BG_FLOOR,
                        f"{name}/{label} : aucun fond peint sans survol "
                        f"(contribution={amount:.1f})",
                    )
                self.widget._close_radial_menu()
                _settle(self.widget, 30)

    def test_background_is_not_a_permanent_highlight(self) -> None:
        """Le fond au repos doit rester nettement plus faible que le survol."""
        for name in MENUS:
            with self.subTest(menu=name):
                rest = self._background_contributions(name)[0]
                hover = self._background_contributions(name, hovered=0)[0]
                self.assertGreater(hover, 0.0)
                self.assertLessEqual(
                    rest,
                    hover * REST_OVER_HOVER_MAX,
                    f"{name} : le fond au repos ({rest:.1f}) est aussi marqué "
                    f"que le survol ({hover:.1f}) — tous les items paraissent "
                    f"sélectionnés.",
                )
                self.widget._close_radial_menu()
                _settle(self.widget, 30)

    def test_hover_stays_visibly_distinct(self) -> None:
        for name in MENUS:
            with self.subTest(menu=name):
                rest = self._background_contributions(name)[0]
                hover = self._background_contributions(name, hovered=0)[0]
                self.assertGreaterEqual(
                    hover - rest,
                    HOVER_DELTA_MIN,
                    f"{name} : le survol n'est plus distinguable du repos "
                    f"(repos={rest:.1f}, survol={hover:.1f})",
                )
                self.widget._close_radial_menu()
                _settle(self.widget, 30)

    def test_hover_only_marks_the_hovered_item(self) -> None:
        """Survoler un item ne doit pas allumer ses voisins."""
        widget = self._open("Voice")
        self._clear_hover()
        boxes = self._label_boxes()
        before = [self._mean_alpha(widget.grab().toImage(), box[0]) for box in boxes]

        widget._menu_nodes[0].hover_amount = 1.0
        image = widget.grab().toImage()
        after = [self._mean_alpha(image, box[0]) for box in boxes]
        widget._menu_nodes[0].hover_amount = 0.0

        self.assertGreater(after[0], before[0])
        for index in range(1, len(boxes)):
            self.assertAlmostEqual(
                after[index],
                before[index],
                delta=6.0,
                msg=f"le survol de {widget._menu_nodes[0].label} a modifié le "
                f"fond de {widget._menu_nodes[index].label}",
            )

    def test_closed_menu_leaves_no_background(self) -> None:
        widget = self._open("Voice")
        boxes = self._label_boxes()
        widget._close_radial_menu()
        _settle(widget, 90)
        self.assertLess(widget._menu_alpha, 0.05)
        image = widget.grab().toImage()
        for label_box, _body in boxes:
            self.assertLessEqual(
                self._mean_alpha(image, label_box),
                BG_GONE,
                "un fond d'item a survécu à la fermeture du menu",
            )


class MenuOverlapContractTests(_MenuRenderCase):
    """Anti-chevauchement : aucun libellé/pastille ne doit se superposer."""

    def test_no_overlap_in_any_menu(self) -> None:
        for name in MENUS:
            with self.subTest(menu=name):
                widget = self._open(name)
                self.assertEqual(
                    widget._menu_layout_overlaps(),
                    [],
                    f"{name} : éléments de menu superposés",
                )
                widget._close_radial_menu()
                _settle(widget, 30)

    def test_voice_menu_twelve_items_have_backgrounds_and_no_overlap(self) -> None:
        """Cas emblématique de la régression publiée : le menu Voice."""
        widget = self._open("Voice")
        self.assertEqual(len(widget._menu_nodes), 12)
        self.assertEqual(widget._menu_layout_overlaps(), [])
        for index, amount in enumerate(self._background_contributions("Voice")):
            self.assertGreaterEqual(
                amount,
                REST_BG_FLOOR,
                f"Voice/{widget._menu_nodes[index].label} : fond absent "
                f"(contribution={amount:.1f})",
            )

    def test_voice_menu_nodes_stay_inside_the_window(self) -> None:
        widget = self._open("Voice")
        width, height = widget.width(), widget.height()
        for node in widget._menu_nodes:
            self.assertGreaterEqual(node.position.x(), 0.0, node.label)
            self.assertGreaterEqual(node.position.y(), 0.0, node.label)
            self.assertLessEqual(node.position.x(), width, node.label)
            self.assertLessEqual(node.position.y(), height, node.label)

    def test_switching_menus_keeps_zero_overlap(self) -> None:
        """Le warm-start du solveur ne doit pas fuir d'un menu à l'autre."""
        widget = self.widget
        for _ in range(2):
            for name in MENUS:
                self._open(name)
                self.assertEqual(
                    widget._menu_layout_overlaps(), [], f"{name} après bascule"
                )


if __name__ == "__main__":
    unittest.main()
