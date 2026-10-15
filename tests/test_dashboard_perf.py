"""Garde-fous de performance du Dashboard (seuils volontairement larges).

Les mesures fines et reproductibles sont produites par
``scripts/dashboard_perf.py`` (voir docs/dashboard/PROTOCOLE_DE_TEST.md) ;
ces tests ne vérifient que les invariants qui ne dépendent pas de la machine :
coût nul Dashboard masqué, aucun import lourd au démarrage, aucun minuteur
d'animation en mode léger, cadres de widgets mis en cache.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.dashboard_support import isolated_jarvis, pump  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_creating_the_controller_does_not_import_heavy_backends():
    """Le démarrage de Jarvis ne doit pas payer google.genai ni les widgets."""
    code = (
        "import sys, os, tempfile\n"
        "os.environ['QT_QPA_PLATFORM']='offscreen'\n"
        "from PySide6.QtWidgets import QApplication\n"
        "app=QApplication([])\n"
        "from UI.dashboard.controller import DashboardController\n"
        "d=tempfile.mkdtemp()\n"
        "c=DashboardController(state_path=os.path.join(d,'s.json'), attach_bridge=False)\n"
        "print('genai' if 'google.genai' in sys.modules else 'ok')\n"
        "print('widgets' if 'UI.dashboard.widgets.memory_widget' in sys.modules else 'ok')\n"
        "c.shutdown()\n"
    )
    env = dict(os.environ, PYTHONPATH=ROOT)
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    lines = [line for line in out.stdout.splitlines() if line in ("ok", "genai", "widgets")]
    assert lines == ["ok", "ok"], out.stdout + out.stderr


def test_hidden_dashboard_costs_nothing_and_panels_are_cached(tmp_path):
    from UI.dashboard.controller import DashboardController

    app = QApplication.instance() or QApplication([])
    with isolated_jarvis():
        controller = DashboardController(state_path=str(tmp_path / "s.json"), attach_bridge=False,
                                         dialog_executor=lambda d: True, reduced_motion_probe=lambda: False)
        try:
            controller.theme_manager.update(animation_level="pousse")
            controller.open()
            controller.window.resize(1500, 900)
            pump(app, 0.5)
            frames = [f for c in controller.window.all_canvases() for f in c.frames.values()]
            counts = [f.paint_count for f in frames]
            pump(app, 0.6)  # le fond animé repeint la fenêtre plusieurs fois
            assert controller.window.root.frames_painted > 2
            assert [f.paint_count for f in frames] == counts, "les cadres ne sont jamais redessinés à neuf"
            controller.hide_window()
            pump(app, 0.1)
            stats = controller.performance_snapshot()
            assert stats["active_widgets"] == 0 and stats["active_timers"] == 0
            assert not stats["resource_thread"]
            painted = controller.window.root.frames_painted
            pump(app, 0.4)
            assert controller.window.root.frames_painted == painted, "aucun rendu fenêtre masquée"
        finally:
            controller.shutdown()


def test_page_switch_and_first_open_stay_interactive(tmp_path):
    from UI.dashboard.controller import DashboardController

    app = QApplication.instance() or QApplication([])
    with isolated_jarvis():
        controller = DashboardController(state_path=str(tmp_path / "s.json"), attach_bridge=False,
                                         dialog_executor=lambda d: True)
        try:
            start = time.perf_counter()
            controller.open()
            app.processEvents()
            first_open = time.perf_counter() - start
            durations = []
            for page in [p.id for p in controller.store.config.layout.pages]:
                start = time.perf_counter()
                controller.window.open_page(page, animate=False)
                app.processEvents()
                durations.append(time.perf_counter() - start)
            # Seuils larges (machines de CI lentes) ; les seuils fins sont
            # vérifiés par scripts/dashboard_perf.py.
            assert first_open < 5.0
            assert max(durations) < 2.0
        finally:
            controller.shutdown()
