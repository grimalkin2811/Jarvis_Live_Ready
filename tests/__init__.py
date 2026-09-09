"""Test helpers shared by the unittest suite."""

import gc
import os
import sys
import tempfile
import unittest


_original_cleanup = tempfile.TemporaryDirectory.cleanup


def _cleanup_with_gc(self):
    """Release short-lived SQLite/file objects before Windows removes a temp dir."""
    gc.collect()
    return _original_cleanup(self)


tempfile.TemporaryDirectory.cleanup = _cleanup_with_gc


def _make_streams_unicode_safe():
    """Rendre stdout/stderr tolérants aux caractères non cp1252.

    Sur les runners Windows, stdout/stderr redirigés utilisent l'encodage
    locale (cp1252) : les bandeaux Unicode de Jarvis (╔═══, etc.) lèveraient
    UnicodeEncodeError en plein milieu des tests. En UTF-8 + ``replace``,
    tout s'affiche sans jamais casser une exécution.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass  # streams remplacés par des objets sans reconfigure (pytest, etc.)


_make_streams_unicode_safe()


def _emit_github_annotations():
    """En CI, émettre chaque échec comme annotation GitHub ``::error``.

    Le runner GitHub Actions interprète les commandes de workflow imprimées
    sur stdout de n'importe quel step : chaque FAIL/ERROR devient donc une
    annotation visible dans l'UI et lisible via l'API checks, sans avoir à
    télécharger les logs bruts. Hors CI, aucun changement de comportement.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return

    print("::notice::tests/__init__ loaded: annotations + utf-8 streams active", flush=True)

    original_print_errors = unittest.TextTestResult.printErrors

    def print_errors_with_annotations(self):
        print(
            f"::notice::unittest done: errors={len(self.errors)} "
            f"failures={len(self.failures)} skipped={len(getattr(self, 'skipped', []))}",
            flush=True,
        )
        errors = list(self.errors)
        failures = list(self.failures)
        for kind, entries in (("ERROR", errors), ("FAIL", failures)):
            for test, err in entries:
                lines = [ln for ln in err.splitlines() if ln.strip()]
                last = lines[-1] if lines else ""
                # ASCII only + borne a 400 caracteres : une commande de
                # workflow doit rester monoligne et encodable partout.
                msg = f"{kind} {test.id()} :: {last}"[:400]
                msg = msg.encode("ascii", "replace").decode("ascii")
                try:
                    print(f"::error::{msg}", flush=True)
                except Exception:
                    pass
        original_print_errors(self)

    unittest.TextTestResult.printErrors = print_errors_with_annotations


_emit_github_annotations()
