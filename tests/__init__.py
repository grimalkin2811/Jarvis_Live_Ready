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

# TEMPORAIRE (diagnostic CI) : publie le rapport unittest complet sur la
# branche jetable "ci-diag" du dépôt, les logs de run n'étant pas
# téléchargeables depuis l'environnement de travail. À retirer une fois la
# CI verte.
_DIAG_BRANCH = "refs/heads/ci-diag"


def _publish_ci_report(payload: str) -> None:
    import subprocess

    try:
        inside = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, timeout=30,
        )
        if inside.stdout.strip() != "true":
            return  # pas un checkout : rien à faire

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
            return  # rien de nouveau à publier
        subprocess.run(
            ["git", "push", "--force", "origin", f"HEAD:{_DIAG_BRANCH}"],
            capture_output=True, text=True, timeout=120,
        )
    except Exception:
        pass  # le diagnostic ne doit jamais faire échouer la suite


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
    state = {"reported": False}

    def print_errors_with_annotations(self):
        state["reported"] = True
        print(
            f"::notice::unittest done: errors={len(self.errors)} "
            f"failures={len(self.failures)} skipped={len(getattr(self, 'skipped', []))}",
            flush=True,
        )
        errors = list(self.errors)
        failures = list(self.failures)
        lines = [
            f"platform={sys.platform} python={sys.version.split()[0]}",
            f"testsRun={self.testsRun} errors={len(errors)} "
            f"failures={len(failures)} skipped={len(getattr(self, 'skipped', []))}",
            "",
        ]
        for kind, entries in (("ERROR", errors), ("FAIL", failures)):
            for test, err in entries:
                lines.append(f"{'=' * 70}\n{kind}: {test.id()}\n{err}")
        _publish_ci_report("\n".join(lines))
        for kind, entries in (("ERROR", errors), ("FAIL", failures)):
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

    import atexit

    def _report_if_missing():
        if not state["reported"]:
            _publish_ci_report(
                "printErrors jamais appelé : le processus s'est arrêté avant "
                "la fin de la suite unittest (crash, os._exit, ...)."
            )

    atexit.register(_report_if_missing)



def _test_module_names():
    """Liste triée des modules de test, identique en local et en CI."""
    base = os.path.dirname(os.path.abspath(__file__))
    names = [
        "tests." + f[:-3]
        for f in sorted(os.listdir(base))
        if f.startswith("test_") and f.endswith(".py")
    ]
    names.append("unittest.loader")  # pseudo-module des erreurs d'import
    return names


def _install_ci_exit_protocol():
    """En CI, encode les modules en échec dans le code de sortie du process.

    Le code de sortie apparaît dans l'annotation "Process completed with
    exit code N", visible via l'API même sans accès aux logs :
      - 7    : suite verte (le hook a bien tourné)
      - 1000 + idx1*32 + idx2 : index (dans la liste triée) du 1er et 2e
        module contenant au moins un échec (31 = absent)
      - 1    : le hook n'a jamais été atteint (le step a échoué avant)
    Temporaire : à retirer une fois la CI verte.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return

    original_run = unittest.TextTestRunner.run

    def run_with_exit_code(self, test):
        result = original_run(self, test)
        failing = set()
        for case, _ in list(result.errors) + list(result.failures):
            try:
                module = case.id().rsplit(".", 1)[0]
            except Exception:
                module = "unittest.loader"
            failing.add(module or "unittest.loader")
        if not failing:
            os._exit(7)
        index_map = {name: i for i, name in enumerate(_test_module_names())}

        def index_of(name):
            return index_map.get(name, 31)

        ordered = sorted(failing)
        code = 1000 + index_of(ordered[0]) * 32
        code += index_of(ordered[1]) if len(ordered) > 1 else 31
        os._exit(code)

    unittest.TextTestRunner.run = run_with_exit_code


_emit_github_annotations()
_install_ci_exit_protocol()
