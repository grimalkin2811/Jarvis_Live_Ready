"""Tests de la journalisation (`src/logging_setup.py`)."""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _ci_diag  # noqa: E402,F401  (diagnostic CI temporaire)

from src import logging_setup  # noqa: E402


class LoggingSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Les addCleanup tournent en LIFO : les handlers sont donc fermés
        # AVANT la suppression du dossier temporaire. Sous Windows, un
        # FileHandler encore ouvert bloque jarvis.log et fait échouer le
        # cleanup avec PermissionError (WinError 32).
        self.addCleanup(self._close_jarvis_handlers)
        self.log_file = Path(self.tmp.name) / "jarvis.log"

    @staticmethod
    def _close_jarvis_handlers():
        logger = logging.getLogger("jarvis")
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    def test_setup_creates_log_file(self):
        logger = logging_setup.setup_logging(
            level="DEBUG",
            filename=str(self.log_file),
            console=False,
            rotate=False,
        )
        logger.info("hello")
        logger.debug("debug line")
        for handler in logger.handlers:
            handler.flush()
        self.assertTrue(self.log_file.is_file())
        content = self.log_file.read_text(encoding="utf-8")
        self.assertIn("hello", content)
        self.assertIn("debug line", content)

    def test_get_logger_child(self):
        logger = logging_setup.get_logger("audio")
        self.assertEqual(logger.name, "jarvis.audio")

    def test_rotating_handler(self):
        logger = logging_setup.setup_logging(
            level="INFO",
            filename=str(self.log_file),
            console=False,
            rotate=True,
            max_bytes=100,
            backup_count=2,
        )
        for i in range(50):
            logger.info("message %d", i)
        for handler in logger.handlers:
            if isinstance(handler, logging.handlers.RotatingFileHandler):
                handler.flush()
        self.assertIn("message", self.log_file.read_text(encoding="utf-8"))

    def test_level_from_env(self):
        with patch.dict(os.environ, {"JARVIS_LOG_LEVEL": "WARNING"}, clear=False):
            logger = logging_setup.setup_logging(
                filename=str(self.log_file),
                console=False,
                rotate=False,
            )
            # Le niveau est appliqué au handler ; le logger est à DEBUG.
            handler_levels = [h.level for h in logger.handlers]
            self.assertTrue(any(l == logging.WARNING for l in handler_levels))


if __name__ == "__main__":
    unittest.main()
