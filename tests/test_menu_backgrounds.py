"""Tests des fonds des items des 5 menus du Blob (rendu offscreen, pixels).

Comportement contractuel vérifié pour CHAQUE menu (Voice, System, Memory,
Appearance, Routines) et pour leurs transitions :

* menu fermé            → aucun fond d'item affiché ;
* menu ouvert, pas de survol → les fonds de TOUS les items sont affichés
                               simultanément (indépendamment du hover) ;
* survol d'un item      → aucun comportement incohérent (les autres fonds
                          restent affichés) ;
* souris hors des items → les fonds restent affichés tant que le menu est
                          ouvert ;
* menu fermé            → tous les fonds disparaissent ;
* rouverte du menu      → tous les fonds réapparaissent ;
* passage menu → menu   → aucun fond de l'ancien menu ne reste affiché ;
* Blob masqué par un mode avec un menu ouvert → état visuel nettoyé en bloc ;
* Blob réaffiché        → état visuel correct ;
* cycles menu 1→2→3→4→5→1 répétés → aucune fuite d'état.

Les assertions se font sur les PIXELS réels (grab offscreen) au centre de
chaque nœud : alpha > 0 = un fond y est dessiné, alpha ≈ 0 = rien.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtCore import QPointF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import UI.jarvis_menu as jm  # noqa: E402
from UI import visibility_bridge  # noqa: E402

#: Alpha minimum pour qu'un fond soit considéré « visible » au centre du
#: nœud (le fond nominal sans survol est ≈ 39/255).
BG_VISIBLE = 25
#: Alpha maximum pour qu'une zone soit considérée « sans fond ».
BG_GONE = 12


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _settle(widget: "jm.MorphingOrbWidget", frames: int = 120) -> None:
    """Avance l'animation de `frames` images (temps simulé, souris fixée)."""
    for _ in range(frames):
        widget.tick()


def _force_fully_open(widget: "jm.MorphingOrbWidget") -> None:
    # Convergence immédiate de la révélation (mêmes conditions que les
    # tests de rendu existants) : le fondu n'est pas l'objet du test.
    for node in widget._menu_nodes:
        node.visible_amount = 1.0
        node.hover_amount = 0.0
        node.click_amount = 0.0
    widget._menu_alpha = 1.0
    widget._menu_reveal = jm.MENU_SPECS[widget._menu_sector].reveal_scale


def _pixel_alpha(widget: "jm.MorphingOrbWidget", node) -> int:
    pix = widget.grab()
    image = pix.toImage()
    return int(image.pixelColor(int(node.position.x()), int(node.position.y())).alpha())


def _sector_vector(widget: "jm.MorphingOrbWidget", sector: int):
    return widget._sector_vector(sector)


class MenuBackgroundTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()
        cls.widget = jm.MorphingOrbWidget()
        # Isoler les fichiers d'état : aucun test n'écrit dans le profil
        # utilisateur réel.
        cls.tmp = Path(tempfile.mkdtemp(prefix="jarvis_menu_bg_"))
        cls.widget._menu_state_path = str(cls.tmp / "menu_state.json")
        cls.widget._appearance_state_path = str(cls.tmp / "appearance.json")
        cls.widget._system_state_path = str(cls.tmp / "system.json")
        cls.widget._debug_log_path = str(cls.tmp / "debug.log")
        cls.widget.resize(1280, 800)
        cls.widget.show()
        jm.set_presence_state("hidden")
        cls.sectors = {spec.name: index for index, spec in enumerate(jm.MENU_SPECS)}
        assert len(cls.sectors) == 5, "le Blob doit avoir exactement 5 menus"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.widget._close_radial_menu()
        cls.widget.close()
        cls.widget.deleteLater()

    def setUp(self) -> None:
        visibility_bridge.VISIBILITY.reset()
        widget = self.widget
        widget._close_radial_menu()
        widget._reset_menu_visuals()
        widget._blob_hidden_by_command = False
        widget._blob_show_override = False
        widget.appearance_state.blob_hidden = False
        widget._window_hidden = False
        widget.cursor = widget.center
        widget._menu_checked_at = -10.0
        widget._mode_checked_at = -10.0
        _settle(widget, 30)
        if not widget.isVisible():
            widget.showFullScreen()

    # ------------------------------------------------------------------
    # 1-4. Pour CHAQUE menu : ouvert → tous les fonds ; survol ; hors items
    #      → fonds conservés ; fermé → fonds disparus.
    # ------------------------------------------------------------------
    def test_every_menu_backgrounds_full_cycle(self) -> None:
        for name in ("Voice", "System", "Memory", "Appearance", "Routines"):
            with self.subTest(menu=name):
                widget = self.widget
                sector = self.sectors[name]
                widget.cursor = widget.center
                widget._open_radial_menu(sector)
                _settle(widget)
                _force_fully_open(widget)

                nodes = widget._menu_nodes
                self.assertTrue(nodes, name)

                # (1) Ouvert, souris au centre (aucun survol) : TOUS les
                # fonds sont affichés simultanément.
                for index, node in enumerate(nodes):
                    with self.subTest(menu=name, node=node.label):
                        alpha = _pixel_alpha(widget, node)
                        self.assertGreaterEqual(
                            alpha, BG_VISIBLE,
                            f"{name}/{node.label}: fond absent sans survol "
                            f"(alpha={alpha})",
                        )

                # (2) Survoler chaque item : cohérent, les autres fonds
                # restent affichés.
                for index, node in enumerate(nodes):
                    with self.subTest(menu=name, hovered=node.label):
                        # Copie du point : node.position est muté par les
                        # ticks (lerp de position) ; un alias ferait dériver
                        # le curseur avec le nœud et refermerait le menu.
                        widget.cursor = QPointF(node.position)
                        _settle(widget, 40)
                        # Le nœud survolé a SON fond…
                        hover_alpha = _pixel_alpha(widget, node)
                        self.assertGreaterEqual(hover_alpha, BG_VISIBLE)
                        # …et les nœuds suivants gardent le leur.
                        for other in nodes[index + 1:3]:
                            alpha = _pixel_alpha(widget, other)
                            self.assertGreaterEqual(
                                alpha, BG_VISIBLE,
                                f"le fond de {other.label} s'est effacé au "
                                f"survol de {node.label}",
                            )

                # (3) Souris hors des items (dans le secteur, menu ouvert) :
                # les fonds restent affichés tant que le menu est ouvert.
                sx, sy = _sector_vector(widget, sector)
                widget.cursor = QPointF(
                    widget.center.x() + sx * widget.base_radius * 0.75,
                    widget.center.y() + sy * widget.base_radius * 0.75,
                )
                _settle(widget, 60)
                self.assertGreaterEqual(widget._menu_sector, 0, "le menu doit rester ouvert")
                for node in widget._menu_nodes:
                    alpha = _pixel_alpha(widget, node)
                    self.assertGreaterEqual(
                        alpha, BG_VISIBLE,
                        f"menu ouvert mais fond de {node.label} disparu hors survol",
                    )

                # (4) Fermer : tous les fonds disparaissent.
                widget.cursor = widget.center
                widget._close_radial_menu()
                _settle(widget, 60)
                self.assertLess(widget._menu_sector, 0)
                self.assertLess(widget._menu_alpha, 0.05)
                # Les nœuds ont été purgés à la fermeture : on échantillonne
                # l'ancienne zone (positions mémorisées) pour vérifier qu'aucun
                # fond n'a survécu.
                self.assertEqual(widget._menu_nodes, [])
                pix = widget.grab().toImage()
                for node in nodes:
                    pos = node.position
                    alpha = int(pix.pixelColor(int(pos.x()), int(pos.y())).alpha())
                    self.assertLessEqual(
                        alpha, BG_GONE,
                        f"un fond de {name} a survécu à la fermeture",
                    )

    # ------------------------------------------------------------------
    # 5. Rouvrir après fermeture : les fonds réapparaissent.
    # ------------------------------------------------------------------
    def test_reopen_restores_all_backgrounds(self) -> None:
        widget = self.widget
        sector = self.sectors["System"]
        widget._open_radial_menu(sector)
        _settle(widget)
        _force_fully_open(widget)
        first_alphas = [_pixel_alpha(widget, n) for n in widget._menu_nodes]
        self.assertTrue(all(a >= BG_VISIBLE for a in first_alphas))

        widget._close_radial_menu()
        _settle(widget, 60)
        self.assertEqual(
            widget._menu_nodes, [], "les nœuds devaient être purgés à la fermeture"
        )

        # Rouvrir : tous les fonds reviennent.
        widget._open_radial_menu(sector)
        _settle(widget)
        _force_fully_open(widget)
        reopen_alphas = [_pixel_alpha(widget, n) for n in widget._menu_nodes]
        self.assertEqual(len(reopen_alphas), len(first_alphas))
        self.assertTrue(all(a >= BG_VISIBLE for a in reopen_alphas))

    # ------------------------------------------------------------------
    # 6. Passage d'un menu à l'autre : aucun fond de l'ancien ne reste.
    # ------------------------------------------------------------------
    def test_menu_switch_cleans_previous_backgrounds(self) -> None:
        widget = self.widget
        order = [
            self.sectors["Voice"],
            self.sectors["System"],
            self.sectors["Memory"],
            self.sectors["Appearance"],
            self.sectors["Routines"],
            self.sectors["Voice"],
        ]
        for previous, current in zip(order, order[1:]):
            with self.subTest(previous=previous, current=current):
                widget._open_radial_menu(previous)
                _settle(widget)
                _force_fully_open(widget)
                previous_positions = [QPointF(n.position) for n in widget._menu_nodes]
                self.assertTrue(all(
                    _pixel_alpha(widget, n) >= BG_VISIBLE for n in widget._menu_nodes
                ))

                # Changer de menu (comme la souris le ferait au secteur
                # suivant) : l'ancien menu est remplacé, pas fusionné.
                widget._open_radial_menu(current)
                _settle(widget)
                _force_fully_open(widget)

                # Le menu courant : tous les fonds visibles.
                for node in widget._menu_nodes:
                    alpha = _pixel_alpha(widget, node)
                    self.assertGreaterEqual(
                        alpha, BG_VISIBLE,
                        f"menu {current} ouvert : fond de {node.label} absent",
                    )
                # L'ancien menu : aucun fond ne fuite.
                pix = widget.grab().toImage()
                for pos in previous_positions:
                    alpha = int(pix.pixelColor(int(pos.x()), int(pos.y())).alpha())
                    self.assertLessEqual(
                        alpha, BG_GONE,
                        f"un fond du menu {previous} fuite dans le menu {current}",
                    )

    # ------------------------------------------------------------------
    # 7-8. Blob masqué par un mode avec un menu ouvert : nettoyage complet,
    #       réaffichage correct.
    # ------------------------------------------------------------------
    def test_blob_hidden_by_mode_cleans_open_menu(self) -> None:
        widget = self.widget
        sector = self.sectors["Memory"]
        widget._open_radial_menu(sector)
        _settle(widget)
        _force_fully_open(widget)
        self.assertTrue(widget.isVisible())
        self.assertTrue(widget._menu_nodes)

        class _SuppressingManager:
            def should_suppress_visuals(self) -> bool:
                return True

        with patch(
            "src.modes.get_default_mode_manager", return_value=_SuppressingManager()
        ):
            widget._mode_checked_at = -10.0
            _settle(widget, 10)

        # (7) Masqué par le mode : la fenêtre est cachée ET l'état visuel du
        # menu est nettoyé en bloc (sector, alpha, nœuds, fonds).
        self.assertFalse(widget.isVisible())
        self.assertLess(widget._menu_sector, 0)
        self.assertLess(widget._menu_alpha, 0.01)
        self.assertEqual(widget._menu_nodes, [])
        self.assertEqual(widget._menu_reveal, 0.0)

        # (8) Le mode se termine : le blob réapparaît, aucun résidu de menu.
        widget._mode_checked_at = -10.0
        _settle(widget, 10)
        self.assertTrue(widget.isVisible())
        self.assertLess(widget._menu_sector, 0)
        self.assertEqual(widget._menu_nodes, [])
        pix = widget.grab().toImage()
        alpha_center = int(pix.pixelColor(
            int(widget.center.x()), int(widget.center.y())
        ).alpha())
        self.assertGreater(alpha_center, 0)  # l'orbe est bien redessiné

    def test_blob_hidden_by_mode_repeated_cycles(self) -> None:
        widget = self.widget
        sector = self.sectors["Routines"]

        class _ToggleManager:
            def __init__(self) -> None:
                self.suppressed = False

            def should_suppress_visuals(self) -> bool:
                return self.suppressed

        manager = _ToggleManager()
        for cycle in range(3):
            with self.subTest(cycle=cycle):
                widget._open_radial_menu(sector)
                _settle(widget)
                _force_fully_open(widget)
                manager.suppressed = True
                widget._mode_checked_at = -10.0
                with patch(
                    "src.modes.get_default_mode_manager", return_value=manager
                ):
                    _settle(widget, 5)
                self.assertFalse(widget.isVisible())
                self.assertEqual(widget._menu_nodes, [])
                manager.suppressed = False
                widget._mode_checked_at = -10.0
                _settle(widget, 5)
                self.assertTrue(widget.isVisible())
                self.assertLess(widget._menu_sector, 0)
                self.assertEqual(widget._menu_nodes, [])

    # ------------------------------------------------------------------
    # 9. La commande vocale « affiche le menu » rouvre tous les fonds,
    #    même après fermeture et même si le blob a été masqué.
    # ------------------------------------------------------------------
    def test_voice_show_menu_restores_backgrounds(self) -> None:
        widget = self.widget
        sector = self.sectors["Appearance"]
        widget._open_radial_menu(sector)
        _settle(widget)
        _force_fully_open(widget)
        widget._close_radial_menu()
        _settle(widget, 60)

        # « affiche le menu » via le pont (comme le ferait la voix).
        visibility_bridge.VISIBILITY.show_menu("apparence")
        _settle(widget, 10)
        self.assertEqual(widget._menu_sector, sector)
        _settle(widget, 110)
        _force_fully_open(widget)
        for node in widget._menu_nodes:
            alpha = _pixel_alpha(widget, node)
            self.assertGreaterEqual(alpha, BG_VISIBLE)

    def test_hover_does_not_make_background_required(self) -> None:
        # Garde-fou contractuel : au repos (visible=1, hover=0) le fond est
        # déjà nettement visible ; le hover n'ajoute qu'un surcroît.
        widget = self.widget
        sector = self.sectors["Voice"]
        widget._open_radial_menu(sector)
        _settle(widget)
        _force_fully_open(widget)
        node = widget._menu_nodes[0]
        rest_alpha = _pixel_alpha(widget, node)

        widget.cursor = QPointF(node.position)
        _settle(widget, 40)
        hover_alpha = _pixel_alpha(widget, node)

        self.assertGreaterEqual(rest_alpha, BG_VISIBLE)
        # Le fond « sans survol » représente au moins la moitié du fond
        # survolé : il n'est plus subordonné au hover.
        self.assertGreaterEqual(rest_alpha, hover_alpha * 0.5)

    def test_fond_reellement_dessine_sans_survol_dans_les_5_menus(self) -> None:
        """Le fond d'item existe VRAIMENT sans survol, dans les 5 menus.

        Test en DIFFÉRENTIEL : on rend le menu ouvert deux fois, avec puis
        sans le fond permanent (``ITEM_BG_REST`` forcé à 0), et on compte les
        pixels qui changent. Mesurer une valeur absolue ne prouvait rien (le
        texte et la pastille sont déjà opaques) : la 1.3.0 passait ses
        propres tests alors qu'aucun fond n'était visible à l'écran
        (5/255 mesuré). La géométrie du rectangle variant selon le menu
        (libellés à droite ou à gauche), le test ne dépend d'aucune
        position : il compare deux rendus complets.
        """
        widget = self.widget
        for name, sector in sorted(self.sectors.items(), key=lambda item: item[1]):
            with self.subTest(menu=name):
                widget.cursor = widget.center
                widget._open_radial_menu(sector)
                _settle(widget)
                _force_fully_open(widget)
                self.assertTrue(widget._menu_nodes, name)

                with patch.object(jm, "ITEM_BG_REST", 0.0):
                    sans_fond = widget.grab().toImage()
                avec_fond = widget.grab().toImage()

                boite = _nodes_bounding_box(widget, marge=190)
                modifies = 0
                for y in range(boite.top(), boite.bottom(), 2):
                    for x in range(boite.left(), boite.right(), 2):
                        delta = abs(
                            avec_fond.pixelColor(x, y).alpha()
                            - sans_fond.pixelColor(x, y).alpha()
                        )
                        if delta > 20:
                            modifies += 1

                # Chaque item couvre une large surface : un fond réellement
                # dessiné modifie des centaines de pixels (mesuré ≈ 3 000).
                self.assertGreater(
                    modifies,
                    400,
                    f"{name} : le fond permanent ne dessine presque rien "
                    f"({modifies} pixels modifiés) — le fond reste invisible "
                    f"sans survol.",
                )
                widget._close_radial_menu()
                _settle(widget, 30)


def _nodes_bounding_box(widget: "jm.MorphingOrbWidget", marge: int = 190):
    """Rectangle englobant les nœuds du menu (pour limiter la comparaison)."""
    from PySide6.QtCore import QRect

    xs = [node.position.x() for node in widget._menu_nodes]
    ys = [node.position.y() for node in widget._menu_nodes]
    boite = QRect(
        int(min(xs)) - marge,
        int(min(ys)) - marge,
        int(max(xs) - min(xs)) + 2 * marge,
        int(max(ys) - min(ys)) + 2 * marge,
    )
    return boite.intersected(widget.rect())


if __name__ == "__main__":
    unittest.main()
