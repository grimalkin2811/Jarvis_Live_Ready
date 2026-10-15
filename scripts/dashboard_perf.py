"""Mesures de performance reproductibles du Dashboard (hors écran).

Usage :
    python scripts/dashboard_perf.py --out build/dashboard_perf
    python scripts/dashboard_perf.py --idle-seconds 3 --json

Critères (justifiés dans docs/dashboard/PROTOCOLE_DE_TEST.md) :
* première ouverture de la fenêtre < 1 500 ms ;
* changement de page : médiane < 150 ms, maximum < 400 ms ;
* latence maximale de la boucle Qt pendant les chargements < 150 ms
  (au-delà de ~100-150 ms une interface est perçue comme figée) ;
* Dashboard masqué : 0 widget actif, 0 minuteur, mesures ressources arrêtées ;
* CPU au repos (fenêtre visible) : léger < 2 %, normal < 10 %, poussé < 20 %
  d'un cœur ; masqué < 2 %.

Les données sont isolées (dossier temporaire) ; l'état moteur est SIMULÉ.
Les chiffres dépendent de la machine : ils sont imprimés avec la plateforme.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("JARVIS_REDUCED_MOTION", "0")

CRITERIA = {
    "window_first_open_ms": 1500.0,
    "page_switch_median_ms": 150.0,
    "page_switch_max_ms": 400.0,
    "event_loop_max_lag_ms": 150.0,
    "cpu_idle_leger_pct": 2.0,
    "cpu_idle_normal_pct": 10.0,
    "cpu_idle_pousse_pct": 20.0,
    "cpu_hidden_pct": 2.0,
}


class LagProbe:
    """Mesure la latence de la boucle Qt (battement toutes les 10 ms)."""

    def __init__(self) -> None:
        from PySide6.QtCore import QTimer

        self.timer = QTimer()
        self.timer.setInterval(10)
        self.timer.timeout.connect(self._tick)
        self.last = None
        self.max_gap = 0.0

    def _tick(self) -> None:
        now = time.perf_counter()
        if self.last is not None:
            self.max_gap = max(self.max_gap, (now - self.last) * 1000.0 - 10.0)
        self.last = now

    def start(self) -> None:
        self.last = None
        self.max_gap = 0.0
        self.timer.start()

    def stop(self) -> float:
        self.timer.stop()
        return max(0.0, self.max_gap)


def run(idle_seconds: float = 4.0) -> dict:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from src.conversation import get_default_conversation_context
    from src.runtime_status import RUNTIME, EngineState
    from tests.dashboard_support import FakeTextEngine, isolated_jarvis, pump

    results: dict = {"platform": f"{platform.system()} {platform.release()} · Python {platform.python_version()}",
                     "qpa": os.environ.get("QT_QPA_PLATFORM"), "cpu_count": os.cpu_count()}
    with isolated_jarvis("jarvis-dashboard-perf-") as env:
        for index in range(500):
            env["memory"].create_memory_entry(f"Souvenir de test numéro {index} sur un sujet varié", "fact", 3,
                                              distinct=True)
        context = get_default_conversation_context()
        context.start_new_conversation("perf")
        for index in range(100):
            context.add_user_message(f"Question {index} : peux-tu détailler ce point ?")
            context.add_assistant_message(f"Réponse {index} : voici une réponse de longueur réaliste. " * 3)
        RUNTIME.reset()
        RUNTIME.set_engine_state(EngineState.READY, model="modele-de-test")
        RUNTIME.update(audio_state="ok", wake_word="loaded", memory_state="ok")
        RUNTIME.register_text_handler(FakeTextEngine(context))

        from UI.dashboard.controller import DashboardController

        start = time.perf_counter()
        controller = DashboardController(state_path=str(Path(env["root"]) / "s.json"), attach_bridge=False,
                                         dialog_executor=lambda d: True, open_path=lambda p: True,
                                         native_sender=lambda t, m: None, reduced_motion_probe=lambda: False)
        results["controller_init_ms"] = round((time.perf_counter() - start) * 1000, 1)
        probe = LagProbe()
        probe.start()
        start = time.perf_counter()
        controller.open()
        controller.window.resize(1600, 950)
        pump(app, 0.01)
        results["window_first_open_ms"] = round((time.perf_counter() - start) * 1000, 1)
        controller.hub.jobs.wait_idle(10)
        pump(app, 0.2)
        switches = []
        pages = [p.id for p in controller.store.config.layout.pages]
        for _round in range(3):
            for page in pages:
                start = time.perf_counter()
                controller.window.open_page(page, animate=False)
                app.processEvents()
                switches.append((time.perf_counter() - start) * 1000)
                controller.hub.jobs.wait_idle(10)
        results["page_switch_median_ms"] = round(statistics.median(switches), 1)
        results["page_switch_max_ms"] = round(max(switches), 1)
        start = time.perf_counter()
        controller.set_view_mode("onepage")
        app.processEvents()
        # Temps jusqu'au premier affichage (squelette + première section) ;
        # les sections suivantes sont construites une par tour de boucle.
        results["onepage_build_ms"] = round((time.perf_counter() - start) * 1000, 1)
        onepage = controller.window._onepage
        deadline = time.perf_counter() + 20
        while onepage is not None and not onepage.fully_built() and time.perf_counter() < deadline:
            app.processEvents()
        results["onepage_full_build_ms"] = round((time.perf_counter() - start) * 1000, 1)
        controller.hub.jobs.wait_idle(10)
        pump(app, 0.3)
        controller.set_view_mode("multipage")
        pump(app, 0.2)
        results["event_loop_max_lag_ms"] = round(probe.stop(), 1)
        results["widgets_after_navigation"] = controller.window.widget_stats()

        def cpu_over(seconds: float) -> float:
            wall0, cpu0 = time.perf_counter(), time.process_time()
            pump(app, seconds)
            return round((time.process_time() - cpu0) / max(1e-6, time.perf_counter() - wall0) * 100.0, 2)

        controller.window.open_page("accueil", animate=False)
        for level in ("leger", "normal", "pousse"):
            controller.theme_manager.update(animation_level=level)
            pump(app, 0.3)
            results[f"cpu_idle_{level}_pct"] = cpu_over(idle_seconds)
        controller.theme_manager.update(animation_level="normal")
        controller.hide_window()
        pump(app, 0.3)
        results["hidden_stats"] = controller.performance_snapshot()
        results["cpu_hidden_pct"] = cpu_over(idle_seconds)
        controller.shutdown()
        RUNTIME.reset()
    checks = {}
    for key, limit in CRITERIA.items():
        value = results.get(key)
        checks[key] = {"value": value, "limit": limit, "ok": value is not None and value <= limit}
    hidden = results["hidden_stats"]
    checks["hidden_idle"] = {"value": f"{hidden['active_widgets']} widget(s), {hidden['active_timers']} minuteur(s), "
                                      f"ressources {'actives' if hidden['resource_thread'] else 'arrêtées'}",
                             "limit": "0, 0, arrêtées",
                             "ok": hidden["active_widgets"] == 0 and hidden["active_timers"] == 0
                             and not hidden["resource_thread"]}
    results["checks"] = checks
    results["all_ok"] = all(item["ok"] for item in checks.values())
    return results


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "build" / "dashboard_perf"))
    parser.add_argument("--idle-seconds", type=float, default=4.0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    results = run(args.idle_seconds)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "perf.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    else:
        print(f"Plateforme : {results['platform']} (QPA {results['qpa']}, {results['cpu_count']} CPU)")
        for key, item in results["checks"].items():
            print(f"  [{'OK' if item['ok'] else 'ÉCHEC'}] {key} = {item['value']} (seuil {item['limit']})")
        print(f"  initialisation contrôleur : {results['controller_init_ms']} ms ; OnePage : premier affichage "
              f"{results['onepage_build_ms']} ms, construction complète {results['onepage_full_build_ms']} ms")
    return 0 if results["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
