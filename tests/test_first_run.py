"""Tests de l'assistant de premier lancement (`src/first_run.py`)."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _ci_diag  # noqa: E402,F401  (diagnostic CI temporaire)

from src import first_run, settings  # noqa: E402


class FirstRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = os.path.join(self.tmp.name, "config.json")

    def test_cli_wizard_saves_config(self):
        inputs = iter(["Marius", "AIza" + "x" * 20, "m", "o"])
        with patch("builtins.input", side_effect=lambda prompt="": next(inputs)):
            with patch("getpass.getpass", side_effect=lambda prompt: next(inputs)):
                cfg = first_run.run_cli_wizard(self.config_path, confirm=True)
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg.user, "Marius")
        self.assertEqual(cfg.api_key, "AIza" + "x" * 20)
        self.assertEqual(cfg.model, "m")
        self.assertTrue(os.path.isfile(self.config_path))

    def test_cli_wizard_cancel(self):
        with patch("builtins.input", side_effect=["", ""]):
            cfg = first_run.run_cli_wizard(self.config_path, confirm=False)
        self.assertIsNone(cfg)

    def test_cli_wizard_rejects_empty_key(self):
        inputs = iter(["M", "  "])
        with patch("builtins.input", side_effect=lambda prompt="": next(inputs)):
            with patch("getpass.getpass", side_effect=lambda prompt: next(inputs)):
                cfg = first_run.run_cli_wizard(self.config_path, confirm=False)
        self.assertIsNone(cfg)

    def test_run_wizard_gui_falls_back(self):
        # run_wizard(gui=False) délègue à run_cli_wizard. On vérifie que le
        # parcours console fonctionne et enregistre la configuration.
        inputs = iter(["M", "K" * 20, "m", "o"])
        with patch("builtins.input", side_effect=lambda prompt="": next(inputs)):
            with patch("getpass.getpass", side_effect=lambda prompt: next(inputs)):
                cfg = first_run.run_wizard(self.config_path, gui=False)
        self.assertIsNotNone(cfg)
        self.assertTrue(os.path.isfile(self.config_path))


if __name__ == "__main__":
    unittest.main()
