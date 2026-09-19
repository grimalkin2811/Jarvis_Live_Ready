"""Tests de l'effet de survol du menu radial.

Le survol dessine un rectangle sombre aux coins arrondis derrière le libellé
ciblé. Trois exigences sont vérifiées ici :

* le fond est **sombre** (texte clair parfaitement lisible par-dessus) ;
* sa couleur est **dérivée du thème courant du Blob** (aucune couleur codée en
  dur) et change donc automatiquement avec l'orbe ;
* il est purement décoratif : il n'ajoute aucune marge et ne déplace ni le
  texte ni les autres nœuds.
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from UI import appearance_actions, jarvis_menu as jm  # noqa: E402

ALIGN_LEFT = Qt.AlignLeft | Qt.AlignVCenter
ALIGN_RIGHT = Qt.AlignRight | Qt.AlignVCenter


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _widget() -> "jm.MorphingOrbWidget":
    _qt_app()
    widget = jm.MorphingOrbWidget()
    widget.resize(1280, 800)
    return widget


def _paint_hover(
    widget,
    theme_color: QColor,
    amount: float,
    text: str = "Always Listening · On",
    alignment=ALIGN_LEFT,
    label_rect: QRectF | None = None,
) -> tuple[QImage, QRectF]:
    """Dessine uniquement le fond de survol sur une image ARGB.

    On peint directement dans une ``QImage`` au format ARGB32 (et non via une
    ``QPixmap`` dont le format — et donc la présence du canal alpha — varie
    selon la plateforme) pour que ces assertions soient déterministes partout.
    """
    rect = QRectF(label_rect) if label_rect is not None else QRectF(40.0, 21.0, 150.0, 18.0)
    image = QImage(320, 60, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setFont(QFont("Segoe UI", 9))
    widget._draw_hover_background(painter, theme_color, rect, alignment, text, amount)
    painter.end()
    return image, rect


def _painted_pixels(image: QImage) -> list[QColor]:
    found = []
    for y in range(image.height()):
        for x in range(image.width()):
            color = image.pixelColor(x, y)
            if color.alpha() > 0:
                found.append(color)
    return found


class HoverColorTests(unittest.TestCase):
    """La couleur du fond suit le Blob, jamais une valeur codée en dur."""

    def test_background_is_dark_for_every_theme(self) -> None:
        for theme, (glow, _text) in appearance_actions.THEME_COLORS.items():
            color = jm.hover_background_color(glow, 1.0)
            _h, _s, light, _a = color.getHslF()
            self.assertLess(
                light, 0.25,
                f"thème {theme} : fond de survol trop clair (L={light:.3f})",
            )

    def test_background_keeps_the_theme_hue(self) -> None:
        for theme, (glow, _text) in appearance_actions.THEME_COLORS.items():
            glow_hue, _s, _l, _a = glow.getHslF()
            color = jm.hover_background_color(glow, 1.0)
            hue, _s2, _l2, _a2 = color.getHslF()
            self.assertGreaterEqual(hue, 0.0, f"thème {theme} : teinte invalide")
            # Tolérance large : la conversion HSL aller/retour arrondit.
            self.assertAlmostEqual(hue, glow_hue, delta=0.02, msg=f"thème {theme}")

    def test_background_follows_theme_changes(self) -> None:
        # Deux thèmes différents doivent produire deux fonds différents :
        # c'est la garantie qu'aucune couleur unique n'est codée en dur.
        blue = jm.hover_background_color(appearance_actions.THEME_COLORS["blue"][0], 1.0)
        red = jm.hover_background_color(appearance_actions.THEME_COLORS["red"][0], 1.0)
        green = jm.hover_background_color(appearance_actions.THEME_COLORS["green"][0], 1.0)
        self.assertNotEqual((blue.red(), blue.green(), blue.blue()),
                            (red.red(), red.green(), red.blue()))
        self.assertNotEqual((blue.red(), blue.green(), blue.blue()),
                            (green.red(), green.green(), green.blue()))

    def test_cycling_the_blob_theme_changes_the_hover_color(self) -> None:
        state = appearance_actions.AppearanceState()
        seen = set()
        for _ in range(len(appearance_actions.THEME_ORDER)):
            color = jm.hover_background_color(state.glow_color, 1.0)
            seen.add((color.red(), color.green(), color.blue()))
            appearance_actions.cycle_theme(state)
        self.assertEqual(len(seen), len(appearance_actions.THEME_ORDER))

    def test_alpha_fades_in_with_the_hover_amount(self) -> None:
        glow = appearance_actions.THEME_COLORS["blue"][0]
        self.assertEqual(jm.hover_background_color(glow, 0.0).alpha(), 0)
        mid = jm.hover_background_color(glow, 0.5).alpha()
        full = jm.hover_background_color(glow, 1.0).alpha()
        self.assertEqual(full, jm.HOVER_MAX_ALPHA)
        self.assertLess(mid, full)
        self.assertGreater(mid, 0)

    def test_amount_is_clamped(self) -> None:
        glow = appearance_actions.THEME_COLORS["purple"][0]
        self.assertEqual(jm.hover_background_color(glow, 5.0).alpha(), jm.HOVER_MAX_ALPHA)
        self.assertEqual(jm.hover_background_color(glow, -3.0).alpha(), 0)

    def test_invalid_color_falls_back_to_a_dark_grey(self) -> None:
        # QColor() invalide : teinte -1. Le fond doit rester utilisable.
        color = jm.hover_background_color(QColor(), 1.0)
        self.assertTrue(color.isValid())
        _h, _s, light, _a = color.getHslF()
        self.assertLess(light, 0.25)
        self.assertGreater(color.alpha(), 0)

    def test_border_is_theme_colored_and_fades(self) -> None:
        glow = appearance_actions.THEME_COLORS["yellow"][0]
        self.assertEqual(jm.hover_border_color(glow, 0.0).alpha(), 0)
        border = jm.hover_border_color(glow, 1.0)
        self.assertEqual(border.alpha(), jm.HOVER_BORDER_ALPHA)
        self.assertEqual((border.red(), border.green(), border.blue()),
                         (glow.red(), glow.green(), glow.blue()))


class HoverPaintingTests(unittest.TestCase):
    """Le rectangle est réellement peint, sans rien déplacer."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.widget = _widget()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.widget.deleteLater()

    def test_nothing_is_painted_without_hover(self) -> None:
        image, _rect = _paint_hover(self.widget, appearance_actions.THEME_COLORS["blue"][0], 0.0)
        self.assertEqual(_painted_pixels(image), [])

    def test_rectangle_is_painted_on_hover(self) -> None:
        image, _rect = _paint_hover(self.widget, appearance_actions.THEME_COLORS["blue"][0], 1.0)
        pixels = _painted_pixels(image)
        self.assertGreater(len(pixels), 500, "le fond de survol n'est pas peint")

    def test_label_rect_is_left_untouched(self) -> None:
        # Exigence clé : le rectangle n'ajoute aucune marge au libellé, il ne
        # peut donc pas décaler le texte ni les autres nœuds.
        glow = appearance_actions.THEME_COLORS["blue"][0]
        rect = QRectF(40.0, 21.0, 150.0, 18.0)
        before = QRectF(rect)
        _paint_hover(self.widget, glow, 1.0, label_rect=rect)
        self.assertEqual(rect, before)

    def test_painted_area_is_dark(self) -> None:
        # Le liseré (couleur du thème) reste clair par nature : c'est l'intérieur
        # du rectangle, là où se trouve le texte, qui doit être sombre.
        image, rect = _paint_hover(self.widget, appearance_actions.THEME_COLORS["white"][0], 1.0)
        centre = image.pixelColor(int(rect.center().x()), int(rect.center().y()))
        self.assertGreater(centre.alpha(), 150)
        _h, _s, light, _a = centre.getHslF()
        self.assertLess(light, 0.2, "l'intérieur du fond de survol doit rester sombre")

        opaque = [p for p in _painted_pixels(image) if p.alpha() > 200]
        self.assertTrue(opaque, "aucun pixel suffisamment opaque")
        dark = [p for p in opaque if p.getHslF()[2] < 0.25]
        # La bordure antialiassée est minoritaire : l'essentiel reste sombre.
        self.assertGreater(len(dark) / len(opaque), 0.9)

    def test_painted_area_follows_the_theme(self) -> None:
        images = {}
        for theme in ("blue", "red", "green"):
            glow = appearance_actions.THEME_COLORS[theme][0]
            image, _rect = _paint_hover(self.widget, glow, 1.0)
            images[theme] = image
        self.assertNotEqual(images["blue"], images["red"])
        self.assertNotEqual(images["blue"], images["green"])

    def test_right_aligned_label_paints_on_the_right(self) -> None:
        glow = appearance_actions.THEME_COLORS["blue"][0]
        rect = QRectF(40.0, 21.0, 150.0, 18.0)
        left_image, _ = _paint_hover(self.widget, glow, 1.0, alignment=ALIGN_LEFT, label_rect=rect)
        right_image, _ = _paint_hover(self.widget, glow, 1.0, alignment=ALIGN_RIGHT, label_rect=rect)
        self.assertNotEqual(left_image, right_image)

        def _leftmost(image: QImage) -> int:
            for x in range(image.width()):
                for y in range(image.height()):
                    if image.pixelColor(x, y).alpha() > 0:
                        return x
            return -1

        self.assertGreater(_leftmost(right_image), _leftmost(left_image))

    def test_multiline_label_is_covered(self) -> None:
        glow = appearance_actions.THEME_COLORS["blue"][0]
        single, _ = _paint_hover(self.widget, glow, 1.0, text="Memory")
        double, _ = _paint_hover(self.widget, glow, 1.0, text="Long-term\nMemory")
        self.assertGreater(len(_painted_pixels(double)), len(_painted_pixels(single)))


class HoverIntegrationTests(unittest.TestCase):
    """Le survol est branché sur le rendu réel du menu radial."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.widget = _widget()
        cls.widget.show()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.widget.close()
        cls.widget.deleteLater()

    def _open_voice_menu(self) -> int:
        sector = next(i for i, s in enumerate(jm.MENU_SPECS) if s.name == "Voice")
        self.widget._open_radial_menu(sector)
        self.widget._menu_reveal = jm.MENU_SPECS[sector].reveal_scale
        self.widget._menu_alpha = 1.0
        for node in self.widget._menu_nodes:
            node.visible_amount = 1.0
            node.click_amount = 0.0
        return sector

    def test_hovered_node_changes_the_rendering(self) -> None:
        self._open_voice_menu()
        node = self.widget._menu_nodes[0]
        node.hover_amount = 0.0
        idle = self.widget.grab().toImage()
        node.hover_amount = 1.0
        hovered = self.widget.grab().toImage()
        self.assertNotEqual(idle, hovered, "le survol ne change rien au rendu")

    def test_rendering_stays_valid_for_every_theme(self) -> None:
        # Un changement de couleur du Blob ne doit jamais casser le rendu.
        self._open_voice_menu()
        for node in self.widget._menu_nodes:
            node.hover_amount = 1.0
        for theme in appearance_actions.THEME_ORDER:
            appearance_actions._apply_theme(self.widget.appearance_state, theme)
            self.widget._appearance_signature = None
            self.widget._apply_appearance_state()
            pixmap = self.widget.grab()
            self.assertFalse(pixmap.isNull(), f"rendu invalide pour le thème {theme}")

    def test_hover_does_not_move_the_node(self) -> None:
        self._open_voice_menu()
        node = self.widget._menu_nodes[1]
        position = QPointF(node.position)
        node.hover_amount = 1.0
        self.widget.update()
        self.assertEqual(node.position, position)


if __name__ == "__main__":
    unittest.main()
