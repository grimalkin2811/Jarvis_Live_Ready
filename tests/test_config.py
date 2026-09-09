"""Tests de la configuration (`src/config.py` + `src/settings.py`)."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import settings  # noqa: E402
from src.config import Config, is_configured, load_config, save_config  # noqa: E402


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(self.tmp.name, "config.json")

    def test_first_run_needed_without_env(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(os.environ, {"JARVIS_DATA_DIR": self.tmp.name}, clear=False):
                self.assertTrue(settings.first_run_needed(self.config_path))

    def test_save_and_load_app_config(self):
        cfg = settings.AppConfig(user="Marius", api_key="AIza" + "x" * 20, model="m1")
        settings.save_app_config(cfg, self.config_path)
        loaded = settings.load_app_config(self.config_path)
        self.assertEqual(loaded.user, "Marius")
        self.assertEqual(loaded.api_key, "AIza" + "x" * 20)
        self.assertEqual(loaded.model, "m1")
        self.assertTrue(loaded.is_configured())

    def test_migrate_from_env(self):
        with patch.dict(
            os.environ,
            {"JARVIS_USER": "Marius", "GEMINI_API_KEY": "AIza" + "y" * 20, "JARVIS_MODEL": "m2"},
            clear=True,
        ):
            cfg = settings.migrate_from_env(self.config_path)
            self.assertIsNotNone(cfg)
            self.assertEqual(cfg.user, "Marius")
            self.assertTrue(os.path.isfile(self.config_path))

    def test_load_config_uses_env_when_no_json(self):
        with patch.dict(
            os.environ,
            {"JARVIS_USER": "Marius", "GEMINI_API_KEY": "AIza" + "z" * 20, "GEMINI_MODEL": "m3"},
            clear=True,
        ):
            config = load_config()
            self.assertEqual(config.user, "Marius")
            self.assertEqual(config.api_key, "AIza" + "z" * 20)
            self.assertEqual(config.model, "m3")

    def test_load_config_missing_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch.dict(os.environ, {"JARVIS_DATA_DIR": self.tmp.name}, clear=False):
                with self.assertRaises(RuntimeError):
                    load_config()

    def test_save_config_roundtrip(self):
        config = Config(user="M", api_key="K" * 20, model="m", memory_enabled=False)
        with patch.dict(os.environ, {"JARVIS_DATA_DIR": self.tmp.name}, clear=False):
            save_config(config)
            # config.py écrit vers %JARVIS_DATA_DIR%/config/config.json
            self.assertTrue(
                os.path.isfile(os.path.join(self.tmp.name, "config", "config.json"))
            )

    def test_validate_api_key_strips_quotes(self):
        self.assertEqual(settings.validate_api_key('"AIzaabc"'), "AIzaabc")


if __name__ == "__main__":
    unittest.main()
