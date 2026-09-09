"""Test helpers shared by the unittest suite."""

import gc
import os
import tempfile
import unittest


_original_cleanup = tempfile.TemporaryDirectory.cleanup


def _cleanup_with_gc(self):
    """Release short-lived SQLite/file objects before Windows removes a temp dir."""
    gc.collect()
    return _original_cleanup(self)


tempfile.TemporaryDirectory.cleanup = _cleanup_with_gc


def _emit_github_annotations():
    """En CI, émettre chaque échec comme annotation GitHub ``::error``.

    Le runner GitHub Actions interprète les commandes de workflow imprimées
    sur stdout de n'importe quel step : chaque FAIL/ERROR devient donc une
    annotation visible dans l'UI et lisible via l'API checks, sans avoir à
    télécharger les logs bruts. Hors CI, aucun changement de comportement.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return

    original_print_errors = unittest.TextTestResult.printErrors

    def print_errors_with_annotations(self):
        for test, err in self.errors + self.failures:
            lines = [ln for ln in err.splitlines() if ln.strip()]
            last = lines[-1] if lines else ""
            kind = "ERROR" if (test, err) in self.errors else "FAIL"
            print(f"::error::{kind} {test.id()} — {last}")
        original_print_errors(self)

    unittest.TextTestResult.printErrors = print_errors_with_annotations


_emit_github_annotations()
