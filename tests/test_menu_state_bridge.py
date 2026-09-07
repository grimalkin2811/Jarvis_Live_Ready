"""Tests du pont thread-safe UI/menu_state (réglages temps réel du backend).

Couvre les nouveaux contrôles : voix Gemini, volume TTS, débit de parole,
écoute continue, et la migration honnête de l'ancien toggle « Startup ».
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from UI import menu_state  # noqa: E402


class VoiceMappingTests(unittest.TestCase):
    def test_every_menu_voice_maps_to_a_gemini_voice(self) -> None:
        for option in menu_state.VOICE_OPTIONS:
            self.assertIn(option, menu_state.GEMINI_VOICE_NAMES)
            self.assertTrue(menu_state.GEMINI_VOICE_NAMES[option])

    def test_set_voice_index_updates_name_and_version(self) -> None:
        live = menu_state.LiveControls()
        self.assertEqual(live.get_voice_name(), "Charon")  # Jarvis par défaut
        version_before = live.get_voice_version()
        live.set_voice_index(3)  # Nova
        self.assertEqual(live.get_voice_name(), "Kore")
        self.assertEqual(live.get_voice_version(), version_before + 1)

    def test_set_voice_index_same_voice_no_version_bump(self) -> None:
        live = menu_state.LiveControls()
        version_before = live.get_voice_version()
        live.set_voice_index(0)  # Jarvis, inchangé
        self.assertEqual(live.get_voice_version(), version_before)

    def test_set_voice_index_rolls_over(self) -> None:
        live = menu_state.LiveControls()
        live.set_voice_index(len(menu_state.VOICE_OPTIONS) + 2)
        self.assertEqual(live.get_voice_name(), menu_state.GEMINI_VOICE_NAMES[menu_state.VOICE_OPTIONS[2]])


class SpeechPaceTests(unittest.TestCase):
    def test_pace_buckets(self) -> None:
        live = menu_state.LiveControls()
        live.set_speech_speed(10)
        self.assertEqual(live.get_speech_pace(), "posé")
        live.set_speech_speed(50)
        self.assertEqual(live.get_speech_pace(), "normal")
        live.set_speech_speed(90)
        self.assertEqual(live.get_speech_pace(), "vif")

    def test_speed_clamped(self) -> None:
        live = menu_state.LiveControls()
        live.set_speech_speed(500)
        self.assertEqual(live.get_speech_speed(), 100)
        live.set_speech_speed(-10)
        self.assertEqual(live.get_speech_speed(), 0)


class LiveControlsTests(unittest.TestCase):
    def test_volume_and_listen_mode(self) -> None:
        live = menu_state.LiveControls()
        live.set_tts_volume(42)
        self.assertEqual(live.get_tts_volume(), 42)
        self.assertFalse(live.get_listen_mode())
        live.set_listen_mode(True)
        self.assertTrue(live.get_listen_mode())

    def test_sync_live_pushes_everything(self) -> None:
        live = menu_state.LiveControls()
        state = menu_state.MenuState(
            tts_volume=80,
            speech_speed=90,
            listen_mode=True,
            mic_enabled=False,
            hotword_sensitivity=75,
            voice_select=2,
        )
        menu_state.LIVE = live  # patch temporaire
        try:
            menu_state._sync_live(state)
            self.assertEqual(live.get_tts_volume(), 80)
            self.assertEqual(live.get_speech_speed(), 90)
            self.assertTrue(live.get_listen_mode())
            self.assertFalse(live.get_mic_enabled())
            self.assertEqual(live.get_hotword_sensitivity(), 75)
            self.assertEqual(live.get_voice_name(), menu_state.GEMINI_VOICE_NAMES["Orion"])
        finally:
            menu_state.LIVE = menu_state.LiveControls()


class StartupMigrationTests(unittest.TestCase):
    def _write_state(self, payload: dict) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return path

    def test_legacy_startup_true_is_reset_to_false(self) -> None:
        # L'ancien toggle Startup n'avait aucun effet : on ne doit pas
        # afficher « On » sans que le raccourci existe réellement.
        path = self._write_state({"startup": True})
        try:
            state = menu_state.load_state(path)
            self.assertFalse(state.startup)
        finally:
            os.remove(path)

    def test_managed_startup_is_kept(self) -> None:
        path = self._write_state({"startup": True, "startup_managed": True})
        try:
            state = menu_state.load_state(path)
            self.assertTrue(state.startup)
            self.assertTrue(state.startup_managed)
        finally:
            os.remove(path)

    def test_default_startup_is_off(self) -> None:
        self.assertFalse(menu_state.MenuState().startup)


if __name__ == "__main__":
    unittest.main()
