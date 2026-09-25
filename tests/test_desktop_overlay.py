"""Tests du cadre Desktop (halo) : apparition / disparition, sans refonte."""

from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    from PySide6.QtWidgets import QApplication  # noqa: E402

    from UI.screen_halo_overlay import ScreenHaloOverlay  # noqa: E402
    from src.ui import PresenceRouter  # noqa: E402

    HAS_QT = True
except Exception:
    HAS_QT = False
    QApplication = None  # type: ignore[assignment]
    ScreenHaloOverlay = None  # type: ignore[assignment]
    PresenceRouter = None  # type: ignore[assignment]


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


@unittest.skipUnless(HAS_QT, "PySide6 / OpenGL indisponible")
class OverlayPresenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.overlay = ScreenHaloOverlay()
        self.overlay.resize(800, 600)

    def tearDown(self) -> None:
        self.overlay.close()
        self.overlay.deleteLater()

    def test_sequence_listening_speaking_hidden(self) -> None:
        self.assertEqual(self.overlay.current_presence(), "hidden")
        self.overlay.show_listening()
        self.assertEqual(self.overlay.current_presence(), "listening")
        self.overlay.show_thinking()
        self.assertEqual(self.overlay.current_presence(), "thinking")
        self.overlay.show_speaking()
        self.assertEqual(self.overlay.current_presence(), "speaking")
        self.overlay.hide_overlay()
        self.assertEqual(self.overlay.current_presence(), "hidden")

    def test_rapid_successive_requests_do_not_stick(self) -> None:
        for _ in range(8):
            self.overlay.show_listening()
            self.overlay.show_speaking()
            self.overlay.hide_overlay()
        self.assertEqual(self.overlay.current_presence(), "hidden")

    def test_short_and_long_response_same_api(self) -> None:
        self.overlay.show_listening()
        self.overlay.show_speaking()
        self.overlay.hide_overlay()
        self.overlay.show_listening()
        self.overlay.show_speaking()
        self.overlay.show_speaking()
        self.overlay.hide_overlay()
        self.assertEqual(self.overlay.current_presence(), "hidden")

    def test_interrupt_returns_to_listening_then_hidden(self) -> None:
        self.overlay.show_speaking()
        self.overlay.show_listening()
        self.assertEqual(self.overlay.current_presence(), "listening")
        self.overlay.hide_overlay()
        self.assertEqual(self.overlay.current_presence(), "hidden")

    def test_paint_does_not_crash(self) -> None:
        self.overlay.show_speaking()
        pix = self.overlay.grab()
        self.assertFalse(pix.isNull())
        self.overlay.hide_overlay()
        pix = self.overlay.grab()
        self.assertFalse(pix.isNull())


@unittest.skipUnless(HAS_QT, "PySide6 / OpenGL indisponible")
class PresenceRouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _qt_app()

    def setUp(self) -> None:
        self.overlay = ScreenHaloOverlay()
        self.overlay.resize(640, 480)
        self.router = PresenceRouter(self.overlay)

    def tearDown(self) -> None:
        self.router._listen_hide_timer.stop()
        self.overlay.close()
        self.overlay.deleteLater()

    def test_router_maps_states(self) -> None:
        self.router.handle_presence("listening")
        self.assertEqual(self.overlay.current_presence(), "listening")
        self.assertTrue(self.router._listen_hide_timer.isActive())
        self.router.handle_presence("thinking")
        self.assertEqual(self.overlay.current_presence(), "thinking")
        self.assertFalse(self.router._listen_hide_timer.isActive())
        self.router.handle_presence("speaking")
        self.assertEqual(self.overlay.current_presence(), "speaking")
        self.router.handle_presence("hidden")
        self.assertEqual(self.overlay.current_presence(), "hidden")

    def test_auto_hide_clears_lingering_listening(self) -> None:
        self.router.handle_presence("listening")
        self.router._hide_if_listening()
        self.assertEqual(self.overlay.current_presence(), "hidden")

    def test_auto_hide_does_not_interrupt_speaking(self) -> None:
        self.router.handle_presence("speaking")
        self.router._hide_if_listening()
        self.assertEqual(self.overlay.current_presence(), "speaking")


if __name__ == "__main__":
    unittest.main()
