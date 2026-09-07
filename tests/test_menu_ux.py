"""Tests d'ergonomie du menu radial (rendu offscreen, aucune action réelle).

Couvre les améliorations UX :
* le flash d'action (retour visuel) se dessine ;
* Échap ferme d'abord le menu avant de quitter l'application ;
* le clic droit ferme le menu ;
* les zones de clic sont assez larges (~19 px) ;
* la molette parcourt les options dans les deux sens ;
* la navigation clavier (flèches + Entrée) fonctionne ;
* les valeurs coûteuses passent par un cache (pas de SQLite à chaque image).
"""

import math
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import UI.jarvis_menu as jm  # noqa: E402


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _open_menu(widget: "jm.MorphingOrbWidget", sector: int) -> None:
    widget._open_radial_menu(sector)
    widget._menu_reveal = jm.MENU_SPECS[sector].reveal_scale
    widget._menu_alpha = 1.0
    for _ in range(90):
        widget.time += 1.0 / 60.0
        widget._update_menu_nodes()
    for node in widget._menu_nodes:
        node.visible_amount = 1.0


def _voice_sector(widget) -> int:
    for index, spec in enumerate(jm.MENU_SPECS):
        if spec.name == "Voice":
            return index
    raise AssertionError("menu Voice introuvable")


class MenuUXTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()
        cls.widget = jm.MorphingOrbWidget()
        cls.widget.resize(1280, 800)
        cls.widget.show()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.widget.close()
        cls.widget.deleteLater()

    def setUp(self) -> None:
        self.widget._close_radial_menu()
        self.widget._menu_action_flash = ""
        self.widget._menu_action_flash_time = -1.0
        self.widget._status_cache.clear()
        self.widget.menu_state = jm.menu_state.MenuState()
        self.widget._menu_focus_index = -1

    # ------------------------------------------------------------------
    # Flash d'action
    # ------------------------------------------------------------------
    def test_flash_is_drawn_without_error(self) -> None:
        self.widget._flash("Test de confirmation")
        pix = self.widget.grab()
        self.assertFalse(pix.isNull())

    def test_flash_disappears_after_duration(self) -> None:
        # Simuler du temps écoulé (les QTimer ne tournent pas sans boucle
        # d'événements en offscreen).
        self.widget.time += 10.0
        self.widget._flash("Message")
        self.assertEqual(self.widget._menu_action_flash, "Message")
        self.widget._menu_action_flash_time = self.widget.time - 3.0
        # Le nettoyage a lieu dans tick() (flux réel de l'animation).
        self.widget.tick()
        self.assertEqual(self.widget._menu_action_flash, "")
        self.assertLess(self.widget._menu_action_flash_time, 0.0)

    # ------------------------------------------------------------------
    # Échap / clic droit
    # ------------------------------------------------------------------
    def test_escape_closes_menu_instead_of_quitting(self) -> None:
        _open_menu(self.widget, _voice_sector(self.widget))
        self.assertGreaterEqual(self.widget._menu_sector, 0)

        class _Event:
            def __init__(self, key):
                self._key = key

            def key(self):
                return self._key

        self.widget.keyPressEvent(_Event(Qt.Key_Escape))
        self.assertLess(self.widget._menu_sector, 0)
        # L'application n'a pas été fermée : la fenêtre est toujours visible.
        self.assertTrue(self.widget.isVisible())

    def test_right_click_closes_open_menu(self) -> None:
        _open_menu(self.widget, _voice_sector(self.widget))
        self.assertGreaterEqual(self.widget._menu_sector, 0)

        class _Event:
            def __init__(self, button):
                self._button = button
                self._accepted = False

            def button(self):
                return self._button

            def position(self):
                return QPointF(10, 10)

            def accept(self):
                self._accepted = True

        event = _Event(Qt.RightButton)
        self.widget.mousePressEvent(event)
        self.assertLess(self.widget._menu_sector, 0)
        self.assertTrue(event._accepted)

    # ------------------------------------------------------------------
    # Zones de clic
    # ------------------------------------------------------------------
    def test_hit_zone_is_comfortable(self) -> None:
        _open_menu(self.widget, _voice_sector(self.widget))
        self.assertTrue(self.widget._menu_nodes)
        node = self.widget._menu_nodes[0]
        # Un clic à 15 px du centre du nœud doit être accepté (les cibles
        # de ~10 px de l'ancienne version étaient trop petites).
        offset = QPointF(15.0, 0.0)
        hit = self.widget._menu_hit_test(node.position + offset)
        self.assertIsNotNone(hit)
        self.assertEqual(hit[0], 0)

    # ------------------------------------------------------------------
    # Molette bidirectionnelle
    # ------------------------------------------------------------------
    def test_wheel_cycles_options_both_ways(self) -> None:
        sector = _voice_sector(self.widget)
        _open_menu(self.widget, sector)
        spec = jm.MENU_SPECS[sector]
        voice_item = next(item for item in spec.items if item.label == "Voice Select")
        start = self.widget.menu_state.voice_select

        class _WheelEvent:
            def __init__(self, direction):
                self._dy = 120 * direction
                self._accepted = False

            def angleDelta(self):
                class _Delta:
                    def __init__(self, dy):
                        self._dy = dy

                    def y(self):
                        return self._dy

                return _Delta(self._dy)

            def accept(self):
                self._accepted = True

        # Simuler le survol du nœud Voice Select.
        for index, node in enumerate(self.widget._menu_nodes):
            if node.label == "Voice Select":
                self.widget._menu_hot_node = index
        self.widget.wheelEvent(_WheelEvent(+1))
        self.assertEqual(self.widget.menu_state.voice_select, (start + 1) % len(jm.menu_state.VOICE_OPTIONS))
        self.widget.wheelEvent(_WheelEvent(-1))
        self.assertEqual(self.widget.menu_state.voice_select, start)

    # ------------------------------------------------------------------
    # Navigation clavier
    # ------------------------------------------------------------------
    def test_keyboard_navigation_and_enter(self) -> None:
        sector = _voice_sector(self.widget)
        _open_menu(self.widget, sector)
        self.assertEqual(self.widget._menu_focus_index, -1)

        class _KeyEvent:
            def __init__(self, key):
                self._key = key
                self._accepted = False

            def key(self):
                return self._key

            def text(self):
                return ""

            def modifiers(self):
                return Qt.NoModifier

            def accept(self):
                self._accepted = True

        # Une flèche place le focus sur le premier nœud, la suivante avance.
        self.widget.keyPressEvent(_KeyEvent(Qt.Key_Down))
        self.assertEqual(self.widget._menu_focus_index, 0)
        self.widget.keyPressEvent(_KeyEvent(Qt.Key_Down))
        self.assertEqual(self.widget._menu_focus_index, 1)

        # Entrée active le nœud focus : un slider affiche sa valeur.
        self.widget._flash("")
        self.widget.keyPressEvent(_KeyEvent(Qt.Key_Return))
        self.assertTrue(self.widget._menu_action_flash)

    def test_digit_shortcut_opens_menu(self) -> None:
        class _KeyEvent:
            def __init__(self, key):
                self._key = key

            def key(self):
                return self._key

            def text(self):
                return "1"

            def modifiers(self):
                return Qt.NoModifier

        self.widget._close_radial_menu()
        self.widget.keyPressEvent(_KeyEvent(Qt.Key_1))
        self.assertEqual(self.widget._menu_sector, 0)

    def test_keyboard_opened_menu_survives_ticks_without_mouse(self) -> None:
        # Un menu ouvert par raccourci ne doit pas se refermer tout seul :
        # la souris immobile (au centre) ne doit pas le tuer.
        self.widget._open_radial_menu(0)
        self.widget.cursor = QPointF(self.widget.center)
        for _ in range(30):
            self.widget.tick()
        self.assertEqual(self.widget._menu_sector, 0)
        self.assertGreater(self.widget._menu_alpha, 0.5)

        # ... mais Échap le referme.
        class _KeyEvent:
            def __init__(self, key):
                self._key = key

            def key(self):
                return self._key

            def text(self):
                return ""

            def modifiers(self):
                return Qt.NoModifier

        self.widget.keyPressEvent(_KeyEvent(Qt.Key_Escape))
        self.assertLess(self.widget._menu_sector, 0)
        self.assertFalse(self.widget._menu_keyboard_open)

    # ------------------------------------------------------------------
    # Cache des valeurs coûteuses
    # ------------------------------------------------------------------
    def test_status_cache_limits_expensive_calls(self) -> None:
        calls = []

        def producer():
            calls.append(1)
            return "42"

        for _ in range(60):  # une seconde de rendu à 60 FPS
            self.widget._cached_status("test_key", producer, ttl=1.0)
        self.assertEqual(len(calls), 1)

        # Après expiration du TTL, une nouvelle lecture a lieu.
        self.widget.time += 1.1
        self.widget._cached_status("test_key", producer, ttl=1.0)
        self.assertEqual(len(calls), 2)

    # ------------------------------------------------------------------
    # Présence
    # ------------------------------------------------------------------
    def test_presence_states_render_without_error(self) -> None:
        for state in ("loading", "listening", "thinking", "speaking", "hidden"):
            jm.set_presence_state(state)
            self.assertEqual(jm.presence_state, state)
            pix = self.widget.grab()
            self.assertFalse(pix.isNull())
        jm.set_presence_state("n'importe quoi")
        self.assertEqual(jm.presence_state, "hidden")

    def test_mic_off_renders_and_is_visible(self) -> None:
        jm.menu_state.LIVE.set_mic_enabled(False)
        try:
            jm.set_presence_state("listening")
            pix = self.widget.grab()
            self.assertFalse(pix.isNull())
        finally:
            jm.menu_state.LIVE.set_mic_enabled(True)

    # ------------------------------------------------------------------
    # Réglages réels
    # ------------------------------------------------------------------
    def test_tts_volume_slider_updates_live_bridge(self) -> None:
        jm.menu_state.LIVE.set_tts_volume(50)
        spec = jm.MENU_SPECS[_voice_sector(self.widget)]
        item = next(i for i in spec.items if i.label == "TTS Volume")
        self.widget._menu_set_slider(spec, item, 83)
        self.assertEqual(self.widget.menu_state.tts_volume, 83)
        self.assertEqual(jm.menu_state.LIVE.get_tts_volume(), 83)

    def test_voice_select_option_updates_voice_version(self) -> None:
        spec = jm.MENU_SPECS[_voice_sector(self.widget)]
        item = next(i for i in spec.items if i.label == "Voice Select")
        version_before = jm.menu_state.LIVE.get_voice_version()
        self.widget._menu_cycle_option(spec, item)
        self.assertEqual(
            jm.menu_state.LIVE.get_voice_version(), version_before + 1
        )


if __name__ == "__main__":
    unittest.main()
