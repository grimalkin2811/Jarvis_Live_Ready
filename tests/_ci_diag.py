"""Diagnostic CI temporaire : rend l'échec des tests observable sans logs.

Importé explicitement par chaque module de test car ``unittest discover
tests`` charge les modules sans passer par le package ``tests``.

Il installe :
  1. des flux stdout/stderr UTF-8 tolérants (runners Windows en cp1252) ;
  2. le patch gc de TemporaryDirectory.cleanup (comme tests/__init__.py,
     qui n'est pas chargé par discovery) ;
  3. l'émission d'annotations GitHub ``::error`` par échec unittest ;
  4. la publication du rapport complet sur la branche jetable ``ci-diag`` ;
  5. un code de sortie = 1000 + somme de (1 << index) pour chaque module
     contenant au moins un échec (index dans la liste triée des modules ;
     code 7 si la suite est verte, 1 si le hook n'est pas atteint).

Tout est inactif hors CI (GITHUB_ACTIONS != "true") et à retirer une fois
la CI verte.
"""

import gc
import os
import sys
import tempfile
import unittest

_INSTALLED = getattr(sys, "_jarvis_ci_diag_installed", False)
sys._jarvis_ci_diag_installed = True

_REPORT_BRANCH = "refs/heads/ci-diag"


# ---------------------------------------------------------------------------
# 1. Flux UTF-8
# ---------------------------------------------------------------------------
def _make_streams_unicode_safe():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass


# ---------------------------------------------------------------------------
# 2. Patch gc sur TemporaryDirectory (comme tests/__init__.py)
# ---------------------------------------------------------------------------
def _install_gc_tempdir_patch():
    if getattr(tempfile.TemporaryDirectory.cleanup, "_jarvis_gc_patch", False):
        return
    original = tempfile.TemporaryDirectory.cleanup

    def cleanup_with_gc(self):
        """Libère les objets SQLite/fichiers avant la suppression du tempdir."""
        gc.collect()
        return original(self)

    cleanup_with_gc._jarvis_gc_patch = True
    tempfile.TemporaryDirectory.cleanup = cleanup_with_gc


# ---------------------------------------------------------------------------
# 3. Annotations GitHub
# ---------------------------------------------------------------------------
def _install_annotations():
    original_print_errors = unittest.TextTestResult.printErrors

    def print_errors_with_annotations(self):
        print(
            f"::notice::unittest done: testsRun={self.testsRun} "
            f"errors={len(self.errors)} failures={len(self.failures)} "
            f"skipped={len(getattr(self, 'skipped', []))}",
            flush=True,
        )
        for kind, entries in (("ERROR", self.errors), ("FAIL", self.failures)):
            for test, err in entries:
                tail = [ln for ln in err.splitlines() if ln.strip()]
                msg = f"{kind} {test.id()} :: {tail[-1] if tail else ''}"[:400]
                msg = msg.encode("ascii", "replace").decode("ascii")
                try:
                    print(f"::error::{msg}", flush=True)
                except Exception:
                    pass
        original_print_errors(self)

    unittest.TextTestResult.printErrors = print_errors_with_annotations


# ---------------------------------------------------------------------------
# 4. Publication du rapport sur la branche ci-diag
# ---------------------------------------------------------------------------
def _publish_ci_report(payload):
    import subprocess

    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=30,
        )
        if inside.stdout.strip() != "true":
            return
        run_id = os.environ.get("GITHUB_RUN_ID", "local")
        attempt = os.environ.get("GITHUB_RUN_ATTEMPT", "0")
        os.makedirs("ci-diag", exist_ok=True)
        report_path = os.path.join("ci-diag", f"report-{run_id}-{attempt}.txt")
        with open(report_path, "w", encoding="utf-8", errors="replace") as handle:
            handle.write(payload[:200_000])
        subprocess.run(["git", "config", "user.email", "ci-diag@users.noreply.github.com"], check=True, timeout=30)
        subprocess.run(["git", "config", "user.name", "ci-diag"], check=True, timeout=30)
        subprocess.run(["git", "add", "-f", report_path], check=True, timeout=30)
        commit = subprocess.run(
            ["git", "commit", "-m", f"ci: rapport de diagnostic {run_id}-{attempt}"],
            capture_output=True, text=True, timeout=60,
        )
        if commit.returncode != 0:
            return
        subprocess.run(
            ["git", "push", "--force", "origin", f"HEAD:{_REPORT_BRANCH}"],
            capture_output=True, text=True, timeout=120,
        )
    except Exception:
        pass  # le diagnostic ne doit jamais faire échouer la suite


# ---------------------------------------------------------------------------
# 5. Code de sortie = masque des modules en échec
# ---------------------------------------------------------------------------
def _test_module_names():
    base = os.path.dirname(os.path.abspath(__file__))
    names = [
        f[:-3]
        for f in sorted(os.listdir(base))
        if f.startswith("test_") and f.endswith(".py")
    ]
    names.append("unittest.loader")
    return names


def _install_exit_protocol():
    original_run = unittest.TextTestRunner.run

    def run_with_exit_code(self, test):
        result = original_run(self, test)
        failing = set()
        for case, _ in list(result.errors) + list(result.failures):
            try:
                parts = case.id().split(".")
                # id() = module.Classe.méthode ; les _FailedTest de loader
                # donnent "unittest.loader._FailedTest.<nom>".
                module = ".".join(parts[:-2]) if len(parts) >= 3 else "unittest.loader"
            except Exception:
                module = "unittest.loader"
            failing.add(module or "unittest.loader")
        if not failing:
            # Suite verte : laisser unittest se terminer naturellement
            # (code 0 => step réussi).
            print("::notice::suite verte (aucun échec)", flush=True)
            return result
        index_map = {name: i for i, name in enumerate(_test_module_names())}
        mask = 0
        report_lines = [
            f"platform={sys.platform} python={sys.version.split()[0]}",
            f"testsRun={result.testsRun} errors={len(result.errors)} "
            f"failures={len(result.failures)} skipped={len(getattr(result, 'skipped', []))}",
            "",
        ]
        for name in sorted(failing):
            index = index_map.get(name)
            if index is not None and index < 30:
                mask |= 1 << index
        for kind, entries in (("ERROR", result.errors), ("FAIL", result.failures)):
            for case, err in entries:
                report_lines.append(f"{'=' * 70}\n{kind}: {case.id()}\n{err}")
        _publish_ci_report("\n".join(report_lines))
        os._exit(1000 + mask)

    unittest.TextTestRunner.run = run_with_exit_code


def _install_all():
    if _INSTALLED:
        return
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    _make_streams_unicode_safe()
    _install_gc_tempdir_patch()
    print("::notice::ci_diag actif (flux utf-8, annotations, code de sortie masque)", flush=True)
    _install_annotations()
    _install_exit_protocol()


_install_all()
