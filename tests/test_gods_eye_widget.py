"""Hôte Qt du module — comportement headless (sans PySide6 ni QApplication)."""

from __future__ import annotations

import unittest


class WidgetHeadlessTests(unittest.TestCase):
    def test_import_sans_qt(self):
        # Le module doit s'importer sans PySide6 (aucun import Qt au niveau module).
        from src.gods_eye import widget

        self.assertTrue(callable(widget.request_open))
        self.assertTrue(callable(widget.web_build_available))

    def test_request_open_sans_qapplication(self):
        from src.gods_eye import widget

        try:
            from PySide6.QtWidgets import QApplication

            if QApplication.instance() is not None:
                self.skipTest("QApplication active — test headless uniquement")
        except Exception:
            pass
        self.assertFalse(widget.request_open(timeout=0.1))

    def test_dist_index_path(self):
        from src.gods_eye import widget

        self.assertTrue(str(widget.DIST_INDEX).endswith("dist/index.html"))
        self.assertIn("gods_eye_view", str(widget.DIST_INDEX))


if __name__ == "__main__":
    unittest.main()
