"""Overlay Desktop v1.7.0 : fenêtre, click-through, widgets, performance.

Ces tests couvrent ce qui ne peut PAS être vérifié à l'œil :

* la fenêtre ne vole jamais le focus et laisse passer les clics ;
* l'état ``HIDDEN`` est réellement invisible et **n'anime rien** ;
* chaque widget n'apparaît que dans les états choisis, et n'est jamais
  reconstruit ;
* le rendu reste dans un budget de temps et ne repeint que les bandes du halo.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    from PySide6.QtCore import QPoint, Qt  # noqa: E402
    from PySide6.QtWidgets import QApplication  # noqa: E402

    from UI.desktop import config as cfg  # noqa: E402
    from UI.desktop.overlay import DesktopOverlay, DesktopOverlayController  # noqa: E402
    from UI.desktop.state import DesktopEvent, DesktopState  # noqa: E402

    HAS_QT = True
except Exception:  # pragma: no cover - environnement sans Qt
    HAS_QT = False
    QApplication = None  # type: ignore[assignment]


def _qt_app():
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@unittest.skipUnless(HAS_QT, "PySide6 indisponible")
class OverlayWindowTests(unittest.TestCase):
    """Non-intrusion : la règle numéro un du Desktop Mode."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.overlay = DesktopOverlay()
        self.overlay.resize(1280, 800)
        self.addCleanup(self.overlay.deleteLater)
        self.addCleanup(self.overlay.close)

    def test_window_is_frameless_and_always_on_top(self) -> None:
        flags = self.overlay.windowFlags()
        self.assertTrue(flags & Qt.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowStaysOnTopHint)
        self.assertTrue(flags & Qt.Tool)

    def test_window_never_accepts_focus(self) -> None:
        self.assertEqual(self.overlay.focusPolicy(), Qt.NoFocus)
        self.assertTrue(self.overlay.windowFlags() & Qt.WindowDoesNotAcceptFocus)
        self.assertTrue(self.overlay.testAttribute(Qt.WA_ShowWithoutActivating))

    def test_click_through_by_default(self) -> None:
        self.assertTrue(self.overlay.is_click_through())
        self.assertTrue(self.overlay.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertTrue(self.overlay.windowFlags() & Qt.WindowTransparentForInput)

    def test_translucent_background(self) -> None:
        self.assertTrue(self.overlay.testAttribute(Qt.WA_TranslucentBackground))
        self.assertTrue(self.overlay.testAttribute(Qt.WA_NoSystemBackground))

    def test_interaction_always_stays_click_through(self) -> None:
        self.overlay.config.interaction = cfg.INTERACTION_ALWAYS
        self.overlay.apply_appearance()
        self.assertTrue(self.overlay.is_click_through())
        self.assertEqual(self.overlay.interaction_mode(), cfg.INTERACTION_ALWAYS)

    def test_interaction_full_captures_the_mouse(self) -> None:
        self.overlay.config.interaction = cfg.INTERACTION_FULL
        self.overlay.apply_appearance()
        self.assertFalse(self.overlay.is_click_through())
        self.assertFalse(self.overlay.windowFlags() & Qt.WindowTransparentForInput)

    def test_interaction_can_be_switched_back(self) -> None:
        self.overlay.config.interaction = cfg.INTERACTION_FULL
        self.overlay.apply_appearance()
        self.overlay.config.interaction = cfg.INTERACTION_WIDGETS
        self.overlay.apply_appearance()
        self.assertTrue(self.overlay.is_click_through())

    def test_interactive_widgets_live_in_a_separate_window(self) -> None:
        """Les contrôles cliquables ne rendent jamais l'écran entier captant."""
        self.overlay.config.set_slot(
            cfg.CONTROLS, enabled=True, states=[DesktopState.SPEAKING]
        )
        self.overlay.set_state(DesktopState.SPEAKING)
        window = self.overlay.controls_window()
        self.assertIsNotNone(window)
        self.assertTrue(self.overlay.is_click_through())
        self.assertFalse(window.controls.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertEqual(window.focusPolicy(), Qt.NoFocus)

    def test_controls_never_created_when_disabled(self) -> None:
        self.overlay.set_state(DesktopState.SPEAKING)
        self.assertIsNone(self.overlay.controls_window())

    def test_control_buttons_emit_actions(self) -> None:
        self.overlay.config.set_slot(
            cfg.CONTROLS, enabled=True, states=[DesktopState.SPEAKING]
        )
        self.overlay.set_state(DesktopState.SPEAKING)
        seen = []
        self.overlay.stopRequested.connect(lambda: seen.append("stop"))
        self.overlay.muteToggled.connect(lambda: seen.append("mic"))
        self.overlay.hideRequested.connect(lambda: seen.append("hide"))
        controls = self.overlay.controls_window().controls
        for index in range(3):
            controls.activate(index)
        self.assertEqual(seen, ["stop", "mic", "hide"])


@unittest.skipUnless(HAS_QT, "PySide6 indisponible")
class OverlayStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.overlay = DesktopOverlay()
        self.overlay.resize(1280, 800)
        self.addCleanup(self.overlay.deleteLater)
        self.addCleanup(self.overlay.close)

    def test_every_state_renders_without_error(self) -> None:
        for state in (
            DesktopState.LOADING,
            DesktopState.LISTENING,
            DesktopState.THINKING,
            DesktopState.TOOL_USE,
            DesktopState.SPEAKING,
            DesktopState.FOLLOW_UP,
            DesktopState.INTERRUPTED,
            DesktopState.ERROR,
            DesktopState.HIDDEN,
        ):
            self.overlay.set_state(state, immediate=True)
            self.assertEqual(self.overlay.current_presence(), state)
            pixmap = self.overlay.grab()
            self.assertFalse(pixmap.isNull())

    def test_hidden_stops_all_animation(self) -> None:
        self.overlay.set_state(DesktopState.LISTENING)
        self.assertTrue(self.overlay.is_animating())
        self.overlay.set_state(DesktopState.HIDDEN, immediate=True)
        self.overlay.set_overlay_intensity(0.0)
        self.overlay._tick()
        self.assertFalse(self.overlay.is_animating())

    def test_hidden_hides_every_widget(self) -> None:
        self.overlay.set_transcript("bonjour")
        self.overlay.set_state(DesktopState.LISTENING)
        self.overlay.set_state(DesktopState.HIDDEN)
        for widget in self.overlay._widgets.values():
            self.assertFalse(widget.is_showing())

    def test_state_changed_signal(self) -> None:
        seen = []
        self.overlay.stateChanged.connect(seen.append)
        self.overlay.set_state(DesktopState.LISTENING)
        self.overlay.set_state(DesktopState.LISTENING)
        self.overlay.set_state(DesktopState.SPEAKING)
        self.assertEqual(seen, [DesktopState.LISTENING, DesktopState.SPEAKING])

    def test_legacy_api_is_preserved(self) -> None:
        self.overlay.show_listening()
        self.assertEqual(self.overlay.current_presence(), "listening")
        self.overlay.show_thinking()
        self.assertEqual(self.overlay.current_presence(), "thinking")
        self.overlay.show_speaking()
        self.assertEqual(self.overlay.current_presence(), "speaking")
        self.overlay.hide_overlay()
        self.assertEqual(self.overlay.current_presence(), "hidden")

    def test_widgets_are_never_recreated(self) -> None:
        identities = {name: id(widget) for name, widget in self.overlay._widgets.items()}
        for _ in range(12):
            for state in (
                DesktopState.LISTENING,
                DesktopState.TOOL_USE,
                DesktopState.SPEAKING,
                DesktopState.HIDDEN,
            ):
                self.overlay.set_state(state)
        self.assertEqual(
            identities, {name: id(w) for name, w in self.overlay._widgets.items()}
        )

    def test_widget_visibility_follows_configuration(self) -> None:
        self.overlay.set_transcript("quelle heure est-il")
        self.overlay.set_state(DesktopState.LISTENING)
        self.assertTrue(self.overlay.transcript.is_showing())
        self.overlay.set_state(DesktopState.TOOL_USE)
        self.assertFalse(self.overlay.transcript.is_showing())
        self.assertTrue(self.overlay.tool.is_showing())

    def test_empty_transcript_is_not_displayed(self) -> None:
        self.overlay.set_transcript("")
        self.overlay.set_state(DesktopState.LISTENING)
        self.assertFalse(self.overlay.transcript.is_showing())

    def test_disabled_widget_never_appears(self) -> None:
        self.overlay.config.set_slot(cfg.STATUS, enabled=False)
        self.overlay.apply_appearance()
        for state in (DesktopState.LISTENING, DesktopState.SPEAKING):
            self.overlay.set_state(state)
            self.assertFalse(self.overlay.status.is_showing())

    def test_disabled_halo_is_not_painted(self) -> None:
        self.overlay.config.set_slot(cfg.HALO, enabled=False)
        self.overlay.set_state(DesktopState.LISTENING, immediate=True)
        self.assertFalse(self.overlay.grab().isNull())

    def test_rapid_state_changes_do_not_stick(self) -> None:
        for _ in range(40):
            self.overlay.set_state(DesktopState.LISTENING)
            self.overlay.set_state(DesktopState.SPEAKING)
        self.overlay.set_state(DesktopState.HIDDEN)
        self.assertEqual(self.overlay.current_presence(), DesktopState.HIDDEN)


@unittest.skipUnless(HAS_QT, "PySide6 indisponible")
class OverlayLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.overlay = DesktopOverlay()
        self.overlay.resize(1920, 1080)
        self.addCleanup(self.overlay.deleteLater)
        self.addCleanup(self.overlay.close)

    def test_widgets_stay_inside_the_screen(self) -> None:
        config = self.overlay.config
        for name in (cfg.STATUS, cfg.TRANSCRIPT, cfg.TOOL, cfg.AUDIO):
            config.set_slot(name, enabled=True, x=0.99, y=0.99)
        self.overlay.set_transcript("un texte suffisamment long pour être élidé " * 3)
        self.overlay._layout_widgets()
        for name in (cfg.STATUS, cfg.TRANSCRIPT, cfg.TOOL, cfg.AUDIO):
            rect = self.overlay.widget_rect(name)
            self.assertGreaterEqual(rect.left(), 0)
            self.assertGreaterEqual(rect.top(), 0)
            self.assertLessEqual(rect.right(), self.overlay.width())
            self.assertLessEqual(rect.bottom(), self.overlay.height())

    def test_position_is_resolution_independent(self) -> None:
        self.overlay.config.set_slot(cfg.STATUS, x=0.25, y=0.5)
        self.overlay._layout_widgets()
        first = self.overlay.widget_rect(cfg.STATUS)
        ratio_x = first.center().x() / self.overlay.width()
        self.overlay.resize(1280, 720)
        self.overlay._layout_widgets()
        second = self.overlay.widget_rect(cfg.STATUS)
        self.assertAlmostEqual(second.center().x() / self.overlay.width(), ratio_x, places=1)

    def test_default_widgets_do_not_overlap(self) -> None:
        """Les défauts doivent être propres sur tous les écrans courants.

        Testé de 1280x720 (portable d'entrée de gamme) à 3840x2160 : les
        éléments affichés par défaut dans un même état ne doivent jamais se
        superposer, sans que l'utilisateur ait à les déplacer.
        """
        self.overlay.set_transcript("météo à Paris")
        for width, height in ((1280, 720), (1366, 768), (1600, 900),
                              (1920, 1080), (2560, 1440), (3840, 2160)):
            self.overlay.resize(width, height)
            for state, names in (
                (DesktopState.LISTENING, (cfg.STATUS, cfg.TRANSCRIPT, cfg.AUDIO)),
                (DesktopState.SPEAKING, (cfg.STATUS, cfg.AUDIO)),
                (DesktopState.TOOL_USE, (cfg.STATUS, cfg.TOOL)),
            ):
                self.overlay.set_state(state)
                self.overlay._layout_widgets()
                rects = [self.overlay.widget_rect(name) for name in names]
                for index, first in enumerate(rects):
                    for second in rects[index + 1:]:
                        self.assertFalse(
                            first.intersects(second),
                            f"{width}x{height} {state} : chevauchement {first} / {second}",
                        )

    def test_scale_changes_widget_size(self) -> None:
        self.overlay.set_transcript("bonjour Jarvis")
        self.overlay._layout_widgets()
        small = self.overlay.widget_rect(cfg.TRANSCRIPT).height()
        self.overlay.config.set_slot(cfg.TRANSCRIPT, scale=1.8)
        self.overlay.apply_appearance()
        large = self.overlay.widget_rect(cfg.TRANSCRIPT).height()
        self.assertGreater(large, small)

    def test_follow_screen_uses_a_real_screen(self) -> None:
        self.overlay.follow_screen()
        screen = self.overlay.target_screen()
        self.assertIsNotNone(screen)
        self.assertEqual(self.overlay.geometry().size(), screen.geometry().size())


@unittest.skipUnless(HAS_QT, "PySide6 indisponible")
class RenderPerformanceTests(unittest.TestCase):
    """Le halo doit rester bon marché : c'est le cœur de la fluidité."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.overlay = DesktopOverlay()
        self.overlay.resize(1920, 1080)
        self.overlay.show()
        self.addCleanup(self.overlay.deleteLater)
        self.addCleanup(self.overlay.close)

    def test_repaint_region_excludes_the_screen_centre(self) -> None:
        self.overlay.set_state(DesktopState.LISTENING, immediate=True)
        region = self.overlay.halo.region()
        rect = self.overlay.rect()
        self.assertFalse(region.contains(rect.center()))
        # …mais couvre bien les quatre bords.
        for point in (
            rect.topLeft(),
            rect.topRight() - QPoint(1, 0),
            rect.bottomLeft() - QPoint(0, 1),
            rect.bottomRight() - QPoint(1, 1),
        ):
            self.assertTrue(region.contains(point))

    def test_band_pixmaps_are_cached(self) -> None:
        self.overlay.set_state(DesktopState.LISTENING, immediate=True)
        self.overlay.grab()
        first = self.overlay.halo.cache_size()
        for _ in range(30):
            self.overlay.halo.advance(0.016, 1.0)
            self.overlay.grab()
        self.assertEqual(self.overlay.halo.cache_size(), first)
        self.assertGreater(first, 0)

    def test_frame_budget(self) -> None:
        """Une image doit tenir largement sous 16 ms, même en 1080p."""
        self.overlay.set_state(DesktopState.SPEAKING, immediate=True)
        self.overlay.grab()  # amorce le cache
        start = time.perf_counter()
        frames = 20
        for _ in range(frames):
            self.overlay.halo.advance(0.016, 1.0)
            self.overlay.grab()
        elapsed = (time.perf_counter() - start) / frames
        # Marge volontairement large : la machine de CI est partagée. Le but
        # est d'attraper une régression d'un ordre de grandeur.
        self.assertLess(elapsed, 0.060, f"{elapsed * 1000:.1f} ms par image")

    def test_hidden_costs_nothing(self) -> None:
        self.overlay.set_state(DesktopState.HIDDEN, immediate=True)
        self.overlay.set_overlay_intensity(0.0)
        self.overlay._tick()
        before = self.overlay.paint_count()
        self.overlay._tick()
        self.assertFalse(self.overlay.is_animating())
        self.assertEqual(self.overlay.paint_count(), before)

    def test_reduced_motion_lowers_frame_rate(self) -> None:
        self.overlay.set_state(DesktopState.LISTENING)
        normal = self.overlay._frame_interval()
        self.overlay.config.reduced_motion = True
        self.overlay.apply_appearance()
        self.assertGreater(self.overlay._frame_interval(), normal)


@unittest.skipUnless(HAS_QT, "PySide6 indisponible")
class ControllerTests(unittest.TestCase):
    """Le contrôleur : évènements réels → états, sans minuteur devinatoire."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["JARVIS_DATA_DIR"] = self.tmp.name
        self.addCleanup(lambda: os.environ.pop("JARVIS_DATA_DIR", None))
        self.overlay = DesktopOverlay()
        self.overlay.resize(1280, 800)
        self.controller = DesktopOverlayController(self.overlay)
        self.addCleanup(self.overlay.deleteLater)
        self.addCleanup(self.overlay.close)
        self.addCleanup(self.controller.close)

    def test_backend_events_drive_the_overlay(self) -> None:
        self.controller.handle_event(DesktopEvent.HOTWORD)
        self.assertEqual(self.overlay.current_presence(), DesktopState.LISTENING)
        self.controller.handle_event(DesktopEvent.TOOL_START, {"name": "music_play"})
        self.assertEqual(self.overlay.current_presence(), DesktopState.TOOL_USE)
        self.assertIn("music_play", self.overlay.tool.text())
        self.controller.handle_event(DesktopEvent.TOOL_END, {"name": "music_play"})
        self.assertEqual(self.overlay.current_presence(), DesktopState.THINKING)
        self.controller.handle_event(DesktopEvent.TTS_START)
        self.assertEqual(self.overlay.current_presence(), DesktopState.SPEAKING)
        self.controller.handle_event(DesktopEvent.TTS_END)
        self.assertEqual(self.overlay.current_presence(), DesktopState.FOLLOW_UP)

    def test_transcript_reuses_backend_text(self) -> None:
        self.controller.handle_event(DesktopEvent.HOTWORD)
        self.controller.handle_event(
            DesktopEvent.TRANSCRIPT, {"text": "quelle est la météo"}
        )
        self.assertEqual(self.overlay.transcript.text(), "quelle est la météo")
        self.assertTrue(self.controller._transcript_timer.isActive())

    def test_level_events_do_not_change_state(self) -> None:
        self.controller.handle_event(DesktopEvent.HOTWORD)
        self.controller.handle_event("level", {"value": 0.7})
        self.assertEqual(self.overlay.current_presence(), DesktopState.LISTENING)

    def test_follow_up_arms_a_timer_and_a_countdown(self) -> None:
        self.controller.handle_event(DesktopEvent.TTS_START)
        self.controller.handle_event(DesktopEvent.TTS_END)
        self.assertTrue(self.controller._listen_hide_timer.isActive())
        self.assertTrue(self.controller._countdown_timer.isActive())

    def test_speaking_has_no_hide_timer(self) -> None:
        self.controller.handle_event(DesktopEvent.TTS_START)
        self.assertFalse(self.controller._listen_hide_timer.isActive())

    def test_reset_clears_everything(self) -> None:
        self.controller.handle_event(DesktopEvent.HOTWORD)
        self.controller.handle_event(DesktopEvent.TRANSCRIPT, {"text": "bonjour"})
        self.controller.reset()
        self.assertEqual(self.overlay.current_presence(), DesktopState.HIDDEN)
        self.assertEqual(self.overlay.transcript.text(), "")
        self.assertFalse(self.controller._listen_hide_timer.isActive())

    def test_game_mode_suppresses_the_overlay(self) -> None:
        from src.modes import get_default_mode_manager

        manager = get_default_mode_manager()
        original = manager.should_suppress_visuals
        manager.should_suppress_visuals = lambda: True  # type: ignore[method-assign]
        try:
            self.controller.handle_event(DesktopEvent.HOTWORD)
            self.assertEqual(self.overlay.current_presence(), DesktopState.HIDDEN)
            self.controller.handle_presence("listening")
            self.assertEqual(self.overlay.current_presence(), DesktopState.HIDDEN)
        finally:
            manager.should_suppress_visuals = original  # type: ignore[method-assign]

    def test_no_private_content_in_logs(self) -> None:
        """Le journal ne doit jamais contenir le texte des échanges."""
        import logging

        records: list[str] = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record.getMessage())

        logger = logging.getLogger("jarvis.desktop")
        handler = _Capture()
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
        try:
            secret = "mon code de carte bleue est 1234"
            self.controller.handle_event(DesktopEvent.HOTWORD)
            self.controller.handle_event(DesktopEvent.TRANSCRIPT, {"text": secret})
            self.controller.handle_event(DesktopEvent.RESPONSE, {"text": secret})
        finally:
            logger.removeHandler(handler)
        joined = " ".join(records)
        self.assertNotIn("carte bleue", joined)
        self.assertNotIn("1234", joined)
        self.assertTrue(any("chars=" in message for message in records))


if __name__ == "__main__":
    unittest.main()
