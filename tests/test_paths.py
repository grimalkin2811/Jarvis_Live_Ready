"""Tests de la centralisation des chemins (`src/paths.py`)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import paths  # noqa: E402


class PathTests(unittest.TestCase):
    def test_data_dir_env_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"JARVIS_DATA_DIR": tmp}, clear=False):
                self.assertEqual(str(paths.data_dir()), str(Path(tmp).resolve()))

    def test_config_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"JARVIS_DATA_DIR": tmp}, clear=False):
                self.assertEqual(
                    str(paths.config_file()),
                    str(Path(tmp).resolve() / "config" / "config.json"),
                )

    def test_memory_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"JARVIS_DATA_DIR": tmp}, clear=False):
                self.assertEqual(str(paths.memory_db()), str(Path(tmp).resolve() / "memory.db"))

    def test_routines_schedule_modes_at_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"JARVIS_DATA_DIR": tmp}, clear=False):
                root = Path(tmp).resolve()
                self.assertEqual(str(paths.schedule_db()), str(root / "schedule.db"))
                self.assertEqual(str(paths.routines_file()), str(root / "routines.json"))
                self.assertEqual(str(paths.modes_file()), str(root / "mode.json"))
                self.assertEqual(str(paths.notes_file()), str(root / "notes.json"))

    def test_ui_state_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"JARVIS_DATA_DIR": tmp}, clear=False):
                root = Path(tmp).resolve()
                self.assertEqual(str(paths.menu_state_file()), str(root / "ui" / "menu_state.json"))
                self.assertEqual(str(paths.appearance_state_file()), str(root / "ui" / "appearance_state.json"))
                self.assertEqual(str(paths.debug_log_file()), str(root / "ui" / "jarvis_debug.log"))

    def test_logs_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"JARVIS_DATA_DIR": tmp}, clear=False):
                self.assertEqual(
                    str(paths.app_log_file()),
                    str(Path(tmp).resolve() / "logs" / "jarvis.log"),
                )

    def test_openwakeword_models_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"JARVIS_DATA_DIR": tmp}, clear=False):
                self.assertEqual(
                    str(paths.openwakeword_models_dir()),
                    str(Path(tmp).resolve() / "models" / "openwakeword"),
                )

    def test_ensure_data_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"JARVIS_DATA_DIR": tmp}, clear=False):
                paths.ensure_data_dirs()
                for sub in ("config", "logs", "ui", "models"):
                    self.assertTrue((Path(tmp) / sub).is_dir())

    def test_is_frozen_false(self):
        self.assertFalse(paths.is_frozen())


if __name__ == "__main__":
    unittest.main()
