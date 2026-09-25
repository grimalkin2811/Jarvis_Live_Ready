"""Tests d'intégration : clic dans le menu radial -> pont LIVE -> backend.

Ces tests vérifient la chaîne complète réellement utilisée en production
(``src/ui.py`` et ``src/main.py`` passent ``menu_state.LIVE.get_*`` à
``AudioIO``) : un clic sur un item du menu doit changer le comportement du
moteur audio, sans redémarrage.

Couvre les deux réglages ajoutés en 1.2.0 :

* Voice → « Listen After Reply » (écoute post-réponse) ;
* Appearance → « Blob Visible » (masquer / réafficher l'orbe).
"""

import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtWidgets import QApplication  # noqa: E402

from UI import jarvis_menu as jm, menu_state  # noqa: E402


def _install_fake_sounddevice() -> None:
    import types

    if "sounddevice" in sys.modules:
        return
    sd = types.ModuleType("sounddevice")

    class _FakeStream:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    sd.RawInputStream = _FakeStream
    sd.RawOutputStream = _FakeStream
    sys.modules["sounddevice"] = sd


_install_fake_sounddevice()

from src.audio import AudioIO  # noqa: E402


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def _sector_of(name: str) -> int:
    for index, spec in enumerate(jm.MENU_SPECS):
        if spec.name == name:
            return index
    raise AssertionError(f"menu {name} introuvable")


def _click(widget: "jm.MorphingOrbWidget", menu_name: str, label: str) -> None:
    """Ouvre le menu demandé et clique l'item, comme le ferait l'utilisateur."""
    widget._open_radial_menu(_sector_of(menu_name))
    node = next(n for n in widget._menu_nodes if n.label == label)
    assert node.callback is not None, f"aucun callback pour {label!r}"
    node.callback()
    QApplication.processEvents()


class LiveBridgeIntegrationTests(unittest.TestCase):
    """Le pont LIVE pilote réellement AudioIO, exactement comme en prod."""

    def setUp(self) -> None:
        self._previous_live = menu_state.LIVE
        menu_state.LIVE = menu_state.LiveControls()
        self.presence: list[str] = []
        self.audio = AudioIO(
            lambda pcm: None,
            presence_hook=self.presence.append,
            mic_enabled=menu_state.LIVE.get_mic_enabled,
            wake_threshold=menu_state.LIVE.get_wake_threshold,
            listen_mode_provider=menu_state.LIVE.get_listen_mode,
            post_response_provider=menu_state.LIVE.get_post_response_listen,
        )
        self.audio.running = True
        self.audio.awake = True
        self.audio.follow_up_until = time.monotonic() - 1.0

    def tearDown(self) -> None:
        menu_state.LIVE = self._previous_live

    def test_enabled_by_default_keeps_the_conversation_open(self) -> None:
        self.audio.extend_listening()
        self.assertTrue(self.audio.awake)
        self.assertGreater(self.audio.follow_up_until, time.monotonic())
        self.assertEqual(self.presence[-1], "listening")

    def test_disabling_through_the_bridge_requires_the_wake_word(self) -> None:
        menu_state.LIVE.set_post_response_listen(False)
        self.audio.extend_listening()
        self.assertFalse(self.audio.awake)
        self.assertEqual(self.presence[-1], "hidden")
        # Sans wake word détecté, Jarvis reste en veille.
        self.audio._handle_idle_block(b"\x00" * AudioIO.INPUT_BLOCKSIZE)
        self.assertFalse(self.audio.awake)

    def test_switch_is_reversible_without_restart(self) -> None:
        menu_state.LIVE.set_post_response_listen(False)
        self.audio.extend_listening()
        self.assertFalse(self.audio.awake)

        menu_state.LIVE.set_post_response_listen(True)
        self.audio._wake()  # l'utilisateur a redit « Hey Jarvis »
        self.audio.follow_up_until = time.monotonic() - 1.0
        self.audio.extend_listening()
        self.assertTrue(self.audio.awake)
        self.assertGreater(self.audio.follow_up_until, time.monotonic())


class MenuClickIntegrationTests(unittest.TestCase):
    """Un clic sur l'item du menu met bien à jour le pont LIVE."""

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
        self._previous_live = menu_state.LIVE
        menu_state.LIVE = menu_state.LiveControls()
        self.widget.menu_state = menu_state.MenuState()
        # La sauvegarde du menu est débouncée (0,5 s) : sans boucle d'évènements
        # offscreen, ``self.time`` n'avance pas. On repart d'une fenêtre de
        # debounce vierge pour que le clic du test écrive réellement l'état.
        self.widget._menu_state_last_save = -10.0
        self.widget._menu_state_dirty = False
        self.widget.set_blob_visible(True)
        QApplication.processEvents()

    def tearDown(self) -> None:
        menu_state.LIVE = self._previous_live

    def test_clicking_listen_after_reply_updates_menu_state_and_bridge(self) -> None:
        self.assertTrue(menu_state.LIVE.get_post_response_listen())
        _click(self.widget, "Voice", "Listen After Reply")
        self.assertFalse(self.widget.menu_state.post_response_listen)
        self.assertFalse(menu_state.LIVE.get_post_response_listen())
        # Le réglage est persisté (rechargé au prochain lancement).
        reloaded = menu_state.load_state(self.widget._menu_state_path)
        self.assertFalse(reloaded.post_response_listen)
        # Second clic : retour à l'état initial.
        _click(self.widget, "Voice", "Listen After Reply")
        self.assertTrue(self.widget.menu_state.post_response_listen)
        self.assertTrue(menu_state.LIVE.get_post_response_listen())

    def test_menu_item_shows_its_state(self) -> None:
        spec = jm.MENU_SPECS[_sector_of("Voice")]
        item = next(i for i in spec.items if i.label == "Listen After Reply")
        self.assertTrue(self.widget._menu_toggle_value(spec, item))
        self.assertEqual(self.widget._voice_value("Listen After Reply"), "On")
        _click(self.widget, "Voice", "Listen After Reply")
        self.assertFalse(self.widget._menu_toggle_value(spec, item))
        self.assertEqual(self.widget._voice_value("Listen After Reply"), "Off")

    def test_clicking_blob_visible_hides_the_orb(self) -> None:
        self.assertTrue(self.widget.isVisible())
        _click(self.widget, "Appearance", "Blob Visible")
        self.assertFalse(self.widget.isVisible())
        self.assertTrue(self.widget.appearance_state.blob_hidden)

    def test_toggle_feedback_is_explicit(self) -> None:
        _click(self.widget, "Voice", "Listen After Reply")
        self.assertIn("Hey Jarvis", self.widget._menu_action_flash)
        _click(self.widget, "Voice", "Listen After Reply")
        self.assertIn("activée", self.widget._menu_action_flash)

    def test_always_listening_precedence_is_announced(self) -> None:
        _click(self.widget, "Voice", "Always Listening")
        _click(self.widget, "Voice", "Listen After Reply")
        self.assertIn("Always Listening", self.widget._menu_action_flash)


if __name__ == "__main__":
    unittest.main()
