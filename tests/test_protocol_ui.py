"""Rendu hors écran de l'overlay des Protocoles + déclencheurs de l'orbe.

Ces tests attrapent les régressions de peinture (un ``paintEvent`` qui lève
une exception laisse un écran noir chez l'utilisateur, sans message) et
vérifient que les déclencheurs cachés sont bien câblés sur l'orbe.
"""

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from src import protocols  # noqa: E402
from UI.boot_sequence import BootSequenceOverlay  # noqa: E402


def _app() -> QApplication:
    return QApplication.instance() or QApplication(sys.argv)


class BootOverlayRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def _events(self, protocol):
        collected = []
        protocols.play_protocol(protocol, collected.append, fast=True)
        return collected

    def test_rendu_a_chaque_etape_et_sur_plusieurs_formats(self):
        events = self._events(protocols.WAKE_UP)
        for width, height in ((1920, 1080), (1280, 720), (1024, 1400)):
            overlay = BootSequenceOverlay()
            overlay.resize(width, height)
            for event in events:
                overlay.handle_event(event)
                for _ in range(3):
                    overlay._tick()
                # grab() déclenche paintEvent : toute exception remonte ici.
                pixmap = overlay.grab()
                self.assertFalse(pixmap.isNull())
            overlay.close()

    def test_rendu_de_tous_les_protocoles(self):
        for protocol in protocols.PROTOCOLS:
            overlay = BootSequenceOverlay()
            overlay.resize(1600, 900)
            for event in self._events(protocol):
                overlay.handle_event(event)
            for _ in range(30):
                overlay._tick()
            self.assertFalse(overlay.grab().isNull())
            overlay.close()

    def test_etat_apres_sequence_complete(self):
        overlay = BootSequenceOverlay()
        overlay.resize(1280, 720)
        for event in self._events(protocols.DIAGNOSTIC):
            overlay.handle_event(event)
        self.assertEqual(overlay.state, "closing")
        self.assertEqual(overlay.progress, 1.0)
        self.assertEqual(overlay.closing, protocols.DIAGNOSTIC.closing)
        self.assertEqual(overlay.done_beats, len(protocols.DIAGNOSTIC.beats))
        overlay.close()

    def test_le_journal_est_borne(self):
        overlay = BootSequenceOverlay()
        overlay.resize(1280, 720)
        overlay.handle_event({"type": protocols.EVENT_START, "beats": ["x"] * 60})
        for index in range(60):
            overlay.handle_event({
                "type": protocols.EVENT_BEAT,
                "index": index,
                "label": f"étape {index}",
                "detail": "détail",
                "progress": (index + 1) / 60,
            })
        self.assertLessEqual(len(overlay.lines), overlay.MAX_LINES)
        self.assertFalse(overlay.grab().isNull())
        overlay.close()

    def test_evenement_incomplet_ne_plante_pas(self):
        overlay = BootSequenceOverlay()
        overlay.resize(800, 600)
        for event in ({"type": protocols.EVENT_START},
                      {"type": protocols.EVENT_BEAT},
                      {"type": protocols.EVENT_LINE},
                      {"type": protocols.EVENT_DONE},
                      {"type": "inconnu"},
                      {}):
            overlay.handle_event(event)
            overlay._tick()
        self.assertFalse(overlay.grab().isNull())
        overlay.close()

    def test_overlay_ne_vole_pas_les_clics(self):
        overlay = BootSequenceOverlay()
        self.assertTrue(overlay.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.assertTrue(overlay.testAttribute(Qt.WA_ShowWithoutActivating))
        overlay.close()

    def test_cache_quand_inactif(self):
        overlay = BootSequenceOverlay()
        overlay.resize(800, 600)
        self.assertFalse(overlay.isVisible())
        # Un rendu à intensité nulle sort immédiatement, sans dessiner.
        self.assertFalse(overlay.grab().isNull())
        overlay.close()


class OrbSecretTriggerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _app()

    def setUp(self):
        from UI.jarvis_menu import MorphingOrbWidget

        self.orb = MorphingOrbWidget()
        self.orb.resize(1280, 800)
        self.launched = []
        self.orb.launch_protocol = self.launched.append

    def tearDown(self):
        run = protocols.active_run()
        if run is not None and run.running:
            run.cancel()
            run.join(timeout=5.0)
        self.orb.close()

    def _type(self, text):
        for char in text:
            event = QKeyEvent(QKeyEvent.KeyPress, ord(char.upper()),
                              Qt.NoModifier, char)
            self.orb.keyPressEvent(event)

    def test_code_secret_lance_le_protocole(self):
        self._type("wakeup")
        self.assertEqual(self.launched, ["wake_up"])

    def test_code_focus(self):
        self._type("focus")
        self.assertEqual(self.launched, ["focus"])

    def test_taper_jarvis_ne_coupe_pas_le_micro(self):
        from UI import menu_state

        before = self.orb.menu_state.mic_enabled
        self._type("jarvis")
        self.assertEqual(self.launched, ["wake_up"])
        self.assertEqual(self.orb.menu_state.mic_enabled, before)
        menu_state.LIVE.set_mic_enabled(before)

    def test_le_raccourci_micro_fonctionne_toujours(self):
        from UI import menu_state

        before = self.orb.menu_state.mic_enabled
        self._type("m")
        self.assertNotEqual(self.orb.menu_state.mic_enabled, before)
        self.assertEqual(self.launched, [])
        self._type("m")
        self.assertEqual(self.orb.menu_state.mic_enabled, before)
        menu_state.LIVE.set_mic_enabled(before)

    def test_triple_clic_au_coeur_lance_le_reveil(self):
        center = self.orb.current_center
        for _ in range(3):
            event = QMouseEvent(
                QMouseEvent.MouseButtonPress,
                QPointF(center.x() + 2.0, center.y() + 2.0),
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
            )
            self.orb.mousePressEvent(event)
        self.assertEqual(self.launched, ["wake_up"])

    def test_clic_hors_du_coeur_ne_declenche_rien(self):
        center = self.orb.current_center
        for _ in range(5):
            event = QMouseEvent(
                QMouseEvent.MouseButtonPress,
                QPointF(center.x() + self.orb.base_radius * 4.0, center.y()),
                Qt.LeftButton, Qt.LeftButton, Qt.NoModifier,
            )
            self.orb.mousePressEvent(event)
        self.assertEqual(self.launched, [])

    def test_echap_interrompt_le_protocole_avant_de_quitter(self):
        run = protocols.start_protocol("wake_up")
        self.addCleanup(lambda: run.join(timeout=5.0))
        self.assertTrue(self.orb.protocol_running())

        event = QKeyEvent(QKeyEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier)
        self.orb.keyPressEvent(event)
        run.join(timeout=5.0)
        self.assertFalse(run.running)
        # La fenêtre reste ouverte : Échap n'a fait qu'interrompre.
        self.assertFalse(self.orb.isHidden() and self.launched)


if __name__ == "__main__":
    unittest.main()
