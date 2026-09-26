"""Tests 1.4.1 : voix unique Blob / Desktop / Console + persistance.

Aucune GUI réelle, aucun réseau : on vérifie la source de vérité
``UI.menu_state.LIVE`` et le chargement runtime partagé par tous les modes.

Le module ``menu_state`` est chargé par fichier pour rester importable sans
OpenGL / PySide6 (``UI/__init__.py`` tire l'overlay Qt).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_menu_state():
    path = ROOT / "UI" / "menu_state.py"
    spec = importlib.util.spec_from_file_location("jarvis_menu_state", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


menu_state = _load_menu_state()


class _LiveGuard:
    """Restaure le pont LIVE après un test qui le mute."""

    def __init__(self) -> None:
        self._previous = menu_state.LIVE

    def install(self) -> "menu_state.LiveControls":
        live = menu_state.LiveControls()
        menu_state.LIVE = live
        return live

    def restore(self) -> None:
        menu_state.LIVE = self._previous


class VoiceSelectionPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = _LiveGuard()
        self.live = self.guard.install()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.menu_path = os.path.join(self.tmp.name, "menu_state.json")
        self.system_path = os.path.join(self.tmp.name, "system_state.json")

    def tearDown(self) -> None:
        self.guard.restore()

    def test_voice_selection_persists(self) -> None:
        state = menu_state.MenuState(voice_select=3, tts_volume=42, speech_speed=90)
        menu_state.save_state(state, self.menu_path)

        self.guard.install()
        reloaded = menu_state.load_runtime_preferences(self.menu_path, self.system_path)
        self.assertEqual(reloaded.voice_select, 3)
        self.assertEqual(menu_state.LIVE.get_voice_name(), menu_state.GEMINI_VOICE_NAMES["Nova"])
        self.assertEqual(menu_state.LIVE.get_tts_volume(), 42)
        self.assertEqual(menu_state.LIVE.get_speech_pace(), "vif")

    def test_desktop_uses_selected_voice(self) -> None:
        """Le Desktop ne doit pas retomber sur Charon : il lit LIVE après load."""
        state = menu_state.MenuState(voice_select=1)  # Aria -> Aoede
        menu_state.save_state(state, self.menu_path)
        self.guard.install()
        self.assertEqual(menu_state.LIVE.get_voice_name(), "Charon")

        menu_state.load_runtime_preferences(self.menu_path, self.system_path)
        kwargs = menu_state.voice_backend_kwargs()
        self.assertEqual(kwargs["voice_provider"](), "Aoede")
        self.assertEqual(menu_state.tts_runtime_config()["voice_name"], "Aoede")

    def test_ui_uses_selected_voice(self) -> None:
        state = menu_state.MenuState(voice_select=4)  # Atlas -> Fenrir
        menu_state.save_state(state, self.menu_path)
        self.guard.install()
        menu_state.load_runtime_preferences(self.menu_path, self.system_path)
        kwargs = menu_state.voice_backend_kwargs()
        self.assertEqual(kwargs["voice_provider"](), menu_state.LIVE.get_voice_name())
        self.assertEqual(kwargs["voice_provider"](), "Fenrir")

    def test_voice_change_updates_runtime(self) -> None:
        version_before = menu_state.LIVE.get_voice_version()
        menu_state.LIVE.set_voice_index(2)  # Orion -> Orus
        self.assertEqual(menu_state.LIVE.get_voice_name(), "Orus")
        self.assertEqual(menu_state.LIVE.get_voice_version(), version_before + 1)
        snapshot = menu_state.tts_runtime_config()
        self.assertEqual(snapshot["voice_name"], "Orus")
        self.assertEqual(snapshot["voice_version"], version_before + 1)

    def test_blob_and_desktop_share_the_same_providers(self) -> None:
        a = menu_state.voice_backend_kwargs()
        b = menu_state.voice_backend_kwargs()
        self.assertEqual(a.keys(), b.keys())
        self.assertEqual(a["voice_provider"](), b["voice_provider"]())
        self.assertEqual(a["voice_version_provider"](), b["voice_version_provider"]())
        self.assertEqual(a["speech_pace_provider"](), b["speech_pace_provider"]())
        self.assertEqual(a["response_mode_provider"](), b["response_mode_provider"]())

    def test_volume_speed_and_mic_roundtrip(self) -> None:
        state = menu_state.MenuState(
            tts_volume=15,
            speech_speed=10,
            mic_enabled=False,
            hotword_sensitivity=80,
            listen_mode=True,
            barge_in=False,
        )
        menu_state.save_state(state, self.menu_path)
        self.guard.install()
        menu_state.load_runtime_preferences(self.menu_path, self.system_path)
        cfg = menu_state.tts_runtime_config()
        self.assertEqual(cfg["tts_volume"], 15)
        self.assertEqual(cfg["speech_pace"], "posé")
        self.assertFalse(cfg["mic_enabled"])
        self.assertTrue(cfg["listen_mode"])
        self.assertFalse(cfg["barge_in"])
        self.assertLess(cfg["wake_threshold"], 0.6)

    def test_response_mode_persists_into_live(self) -> None:
        with open(self.system_path, "w", encoding="utf-8") as handle:
            json.dump({"response_mode_index": 0}, handle)
        menu_state.save_state(menu_state.MenuState(), self.menu_path)
        self.guard.install()
        self.assertEqual(menu_state.response_mode_label_from_live(), "Équilibré")
        menu_state.load_runtime_preferences(self.menu_path, self.system_path)
        self.assertEqual(menu_state.response_mode_label_from_live(), "Concis")
        self.assertEqual(menu_state.tts_runtime_config()["response_mode"], "Concis")

    def test_missing_files_keep_defaults(self) -> None:
        missing_menu = os.path.join(self.tmp.name, "nope.json")
        missing_system = os.path.join(self.tmp.name, "nope_sys.json")
        state = menu_state.load_runtime_preferences(missing_menu, missing_system)
        self.assertEqual(state.voice_select, 0)
        self.assertEqual(menu_state.LIVE.get_voice_name(), "Charon")
        self.assertEqual(menu_state.LIVE.get_tts_volume(), 70)

    def test_transparency_and_always_on_top_persist(self) -> None:
        state = menu_state.MenuState(transparency=55, always_on_top=True)
        menu_state.save_state(state, self.menu_path)
        reloaded = menu_state.load_state(self.menu_path)
        self.assertEqual(reloaded.transparency, 55)
        self.assertTrue(reloaded.always_on_top)


class SharedWiringTests(unittest.TestCase):
    def test_ui_and_headless_share_voice_kwargs(self) -> None:
        ui_src = (ROOT / "src" / "ui.py").read_text(encoding="utf-8")
        main_src = (ROOT / "src" / "main.py").read_text(encoding="utf-8")
        self.assertIn("voice_backend_kwargs", ui_src)
        self.assertIn("voice_backend_kwargs", main_src)
        self.assertIn("load_runtime_preferences", ui_src)
        self.assertIn("load_runtime_preferences", main_src)

    def test_session_visibility_is_not_persisted(self) -> None:
        """Masquer/Quitter : l'état caché vit seulement en session."""
        ui_src = (ROOT / "src" / "ui.py").read_text(encoding="utf-8")
        self.assertIn("class SessionVisibility", ui_src)
        self.assertIn("visibility.reset()", ui_src)
        self.assertIn("Masquer Jarvis", ui_src)
        self.assertIn("setQuitOnLastWindowClosed(False)", ui_src)


if __name__ == "__main__":
    unittest.main()
