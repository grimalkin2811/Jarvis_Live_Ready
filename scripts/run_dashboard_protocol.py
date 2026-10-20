"""Protocole de test dédié au Dashboard — indépendant du build et de la release.

Enchaîne, dans cet ordre, et s'arrête au premier échec seulement si
``--fail-fast`` est passé :

1. tests unitaires du Dashboard (sans fenêtre) ;
2. tests d'intégration (fenêtre hors écran, données isolées, moteur simulé) ;
3. tests de performance (pytest) puis mesures chiffrées (``dashboard_perf.py``) ;
4. protocole visuel : captures d'écran (``dashboard_visual_protocol.py``) ;
5. contrôles statiques : ruff + compileall sur le code du Dashboard.

Usage :
    python scripts/run_dashboard_protocol.py                 # tout
    python scripts/run_dashboard_protocol.py --skip visual   # sans captures
    python scripts/run_dashboard_protocol.py --only unit integration

Résultat : ``build/dashboard_protocol/summary.json`` (+ sorties de chaque
étape). Toutes les données sont isolées (dossiers temporaires) : la mémoire,
les notes et les préférences réelles de l'utilisateur ne sont jamais touchées.
Voir docs/dashboard/PROTOCOLE_DE_TEST.md.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys

# Windows console cp1252 safety: prevent UnicodeEncodeError on unicode characters
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

UNIT_TESTS = [
    "tests/test_dashboard_config.py",
    "tests/test_dashboard_layout.py",
    "tests/test_dashboard_security.py",
    "tests/test_dashboard_memory_service.py",
    "tests/test_dashboard_notifications.py",
    "tests/test_dashboard_resources.py",
    "tests/test_runtime_status.py",
    "tests/test_conversation_listeners.py",
    "tests/test_pyside_refcount.py",
    "tests/test_suite_isolation.py",
]
INTEGRATION_TESTS = [
    "tests/test_gemini_text_input.py",
    "tests/test_gemini_text_voice_interplay.py",
    "tests/test_dashboard_widgets.py",
    "tests/test_dashboard_window.py",
    "tests/test_dashboard_conversation_widget.py",
    "tests/test_dashboard_tasks.py",
    "tests/test_dashboard_entry_points.py",
    "tests/test_dashboard_productivity.py",
]
PERF_TESTS = ["tests/test_dashboard_perf.py"]
STATIC_TARGETS = ["UI/dashboard", "UI/dashboard_bridge.py", "src/runtime_status.py", "scripts/dashboard_perf.py",
                  "scripts/dashboard_visual_protocol.py", "scripts/run_dashboard_protocol.py"]
STEPS = ("unit", "integration", "perf", "visual", "static")


def _run(name: str, command: list[str], out_dir: Path) -> dict:
    env = dict(os.environ)
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    started = time.perf_counter()
    print(f"\n=== {name} : {' '.join(command)}", flush=True)
    # Décodage tolérant : sous Windows, un message natif (Qt, système) peut sortir
    # dans la page de code ANSI ; un décodage strict faisait planter le protocole
    # (code de sortie non nul) alors que l'étape elle-même avait réussi.
    proc = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    elapsed = time.perf_counter() - started
    log = out_dir / f"{name}.log"
    log.write_text(proc.stdout + ("\n--- stderr ---\n" + proc.stderr if proc.stderr else ""), encoding="utf-8")
    tail = [line for line in proc.stdout.strip().splitlines() if line.strip()][-1:] or [""]
    msg = f"    → code {proc.returncode} en {elapsed:.1f} s · {tail[0]}"
    try:
        print(msg, flush=True)
    except UnicodeEncodeError:
        print(msg.encode("ascii", errors="replace").decode("ascii"), flush=True)
    return {"step": name, "command": command, "returncode": proc.returncode, "seconds": round(elapsed, 1),
            "last_line": tail[0], "log": str(log.relative_to(ROOT)) if log.is_relative_to(ROOT) else str(log)}


def _perf_details(path: Path) -> dict:
    """Critères dépassés et mesures clés de ``dashboard_perf.py`` (correctif C10).

    Le résumé ne donnait que la dernière ligne de chaque étape : pour les
    mesures, la ligne OnePage (indicative) — jamais le critère dépassé.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"failed_checks": None, "details": "perf.json absent ou illisible"}
    failed = {name: {"value": item.get("value"), "limit": item.get("limit")}
              for name, item in data.get("checks", {}).items() if not item.get("ok")}
    keys = ("window_first_open_ms", "window_first_content_ms", "event_loop_max_lag_ms",
            "event_loop_max_lag_by_phase_ms", "page_switch_median_ms", "page_switch_max_ms", "onepage_build_ms",
            "cpu_idle_pousse_pct", "cpu_hidden_pct")
    return {"failed_checks": failed, "measures": {key: data.get(key) for key in keys if key in data}}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", default=str(ROOT / "build" / "dashboard_protocol"))
    parser.add_argument("--only", nargs="*", choices=STEPS, help="Étapes à exécuter (défaut : toutes).")
    parser.add_argument("--skip", nargs="*", choices=STEPS, default=[], help="Étapes à ignorer.")
    parser.add_argument("--fail-fast", action="store_true")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    selected = [s for s in (args.only or STEPS) if s not in set(args.skip)]
    py = sys.executable
    pytest = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-ra"]
    commands = {
        "unit": [("unit", pytest + UNIT_TESTS)],
        "integration": [("integration", pytest + INTEGRATION_TESTS)],
        "perf": [("perf_tests", pytest + PERF_TESTS),
                 ("perf_measures", [py, "scripts/dashboard_perf.py", "--out", str(out_dir / "perf")])],
        "visual": [("visual", [py, "scripts/dashboard_visual_protocol.py", "--out", str(out_dir / "screens")])],
        "static": [("ruff", [py, "-m", "ruff", "check", *STATIC_TARGETS, "tests"]),
                   ("compileall", [py, "-m", "compileall", "-q", "UI/dashboard", "src", "scripts", "tests"])],
    }
    results: list[dict] = []
    for step in selected:
        for name, command in commands[step]:
            result = _run(name, command, out_dir)
            if name == "perf_measures":
                result.update(_perf_details(out_dir / "perf" / "perf.json"))
            results.append(result)
            if result["returncode"] != 0 and args.fail_fast:
                break
        if results and results[-1]["returncode"] != 0 and args.fail_fast:
            break
    ok = all(r["returncode"] == 0 for r in results)
    exit_code = 0 if ok else 1
    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "platform": f"{platform.system()} {platform.release()} · Python {platform.python_version()}",
        "qpa": os.environ.get("QT_QPA_PLATFORM", "offscreen"),
        "steps": results,
        "success": ok,
        # Code de sortie ATTENDU de ce script : l'orchestrateur le compare au
        # code réellement observé pour signaler toute incohérence.
        "exit_code": exit_code,
        "notes": "Moteur vocal simulé, données isolées. Ne remplace pas la validation Windows réelle "
                 "(voir la liste de contrôle de PROTOCOLE_DE_TEST.md).",
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        print(f"\nRésumé : {'SUCCÈS' if ok else 'ÉCHEC'} — {out_dir / 'summary.json'}", flush=True)
    except (OSError, UnicodeError):
        pass  # l'affichage final ne doit jamais contredire le résultat des étapes
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
