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


def test_first_open_paints_the_window_before_building_the_page(tmp_path):
    """Correctif D01 (validation Windows PR #50 : 1 898 ms > 1 500 ms sur Pentium G5400).

    La fenêtre s'affiche d'abord (en-tête, navigation, « Chargement… ») ; la page
    et ses widgets sont construits juste après la première peinture. Tout accès
    aux widgets, ou une page demandée explicitement, termine la construction
    sur-le-champ : aucun appelant ne voit de fenêtre vide.
    """
    from UI.dashboard.controller import DashboardController

    app = QApplication.instance() or QApplication([])
    with isolated_jarvis():
        controller = DashboardController(state_path=str(tmp_path / "s.json"), attach_bridge=False,
                                         dialog_executor=lambda d: True)
        try:
            controller.open()
            app.processEvents()  # première peinture
            window = controller.window
            assert window.isVisible()
            assert window._initial_build_pending, "la page doit être construite APRÈS la première peinture"
            assert window.stack.currentWidget() is window._loading_view
            deadline = time.monotonic() + 5.0
            while window._initial_build_pending and time.monotonic() < deadline:
                app.processEvents()
                time.sleep(0.002)
            assert not window._initial_build_pending, "le contenu arrive sans intervention"
            assert window._loading_view is None
            assert window.widget_stats()["widgets"] > 0
            stats = window.widget_stats()
            assert stats["active_widgets"] == stats["widgets"], "widgets actifs une fois la fenêtre visible"
        finally:
            controller.shutdown()
    with isolated_jarvis():
        controller = DashboardController(state_path=str(tmp_path / "t.json"), attach_bridge=False,
                                         dialog_executor=lambda d: True)
        try:
            controller.ensure_window()
            assert controller.window._initial_build_pending
            assert controller.window.all_canvases(), "un accès aux widgets termine la construction"
            assert not controller.window._initial_build_pending
        finally:
            controller.shutdown()
    with isolated_jarvis():
        controller = DashboardController(state_path=str(tmp_path / "u.json"), attach_bridge=False,
                                         dialog_executor=lambda d: True)
        try:
            controller.open("memoire")  # page demandée (commande vocale) : construction immédiate
            assert not controller.window._initial_build_pending
            assert controller.window.current_page() == "memoire"
        finally:
            controller.shutdown()


def test_onepage_builds_a_few_widgets_per_event_loop_turn(tmp_path, monkeypatch):
    """Correctif C10 (validation Windows PR #50 : OnePage « premier affichage 179 » ms,
    un seul bloc de la boucle Qt au-delà du seuil de latence de 150 ms).

    Chaque tour de boucle ne crée que quelques widgets (budget de temps, au moins
    un widget par tour) ; le résultat final est identique à une construction d'un
    bloc ; ``build_all`` termine tout immédiatement (captures, tests).
    """
    from UI.dashboard import grid_view, pages_view
    from UI.dashboard.controller import DashboardController

    app = QApplication.instance() or QApplication([])
    created_per_turn: list[int] = []
    original_step = grid_view.GridCanvas.build_step

    def counting_step(self, budget_ms=12.0):
        before = len(self.frames)
        done = original_step(self, budget_ms)
        created_per_turn.append(len(self.frames) - before)
        return done

    monkeypatch.setattr(grid_view.GridCanvas, "build_step", counting_step)
    monkeypatch.setattr(pages_view.OnePageView, "BUILD_BUDGET_MS", 0.0)  # pire cas : un widget par tour
    with isolated_jarvis():
        controller = DashboardController(state_path=str(tmp_path / "s.json"), attach_bridge=False,
                                         dialog_executor=lambda d: True)
        try:
            controller.open()
            controller.set_view_mode("onepage")
            onepage = controller.window._onepage
            deadline = time.monotonic() + 10.0
            while not onepage.fully_built() and time.monotonic() < deadline:
                app.processEvents()
            assert onepage.fully_built()
            assert created_per_turn and max(created_per_turn) == 1, created_per_turn
            expected = sum(len([p for p in c.page.widgets if not p.hidden]) for c in onepage.canvases())
            assert sum(len(c.frames) for c in onepage.canvases()) == expected
            for canvas in onepage.canvases():
                assert canvas.built
                assert set(canvas.frames) == {p.id for p in canvas.page.widgets if not p.hidden}
        finally:
            controller.shutdown()
    with isolated_jarvis():
        controller = DashboardController(state_path=str(tmp_path / "t.json"), attach_bridge=False,
                                         dialog_executor=lambda d: True)
        try:
            controller.open()
            controller.set_view_mode("onepage")
            onepage = controller.window._onepage
            onepage.build_all()
            assert onepage.fully_built()
            assert all(canvas.built for canvas in onepage.canvases())
        finally:
            controller.shutdown()


def test_perf_script_ends_with_the_failed_criteria():
    """Correctif C10 : la dernière ligne (reprise par le protocole) nomme le critère dépassé."""
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    import dashboard_perf

    results = {"checks": {"event_loop_max_lag_ms": {"value": 182.4, "limit": 150.0, "ok": False},
                          "window_first_open_ms": {"value": 61.0, "limit": 1500.0, "ok": True}}}
    assert dashboard_perf.echecs(results) == ["event_loop_max_lag_ms=182.4 (seuil 150.0)"]


def test_themes_use_one_installed_family_and_a_symbol_font_for_glyphs():
    """Correctif D01, cause établie sur Windows natif : une police à PLUSIEURS familles
    (anciennes chaînes « "Segoe UI", "Inter", … ») ou un glyphe absent de la police
    principale font charger à Qt toutes les polices du système (155 fichiers, 3,5 s à
    froid). Chaque rôle de police du thème est donc UNE famille installée, et les
    éléments à glyphe utilisent la police de symboles comme police principale."""
    from PySide6.QtGui import QFontDatabase

    from UI.dashboard import theme as theme_module
    from UI.dashboard.config import AppearancePrefs

    QApplication.instance() or QApplication([])
    installed = set(QFontDatabase.families())
    symbol = theme_module.symbol_family()
    for theme_id in theme_module.THEMES:
        prefs = AppearancePrefs()
        prefs.theme = theme_id
        resolved = theme_module.resolve(prefs, reduced_motion=False)
        for kind, families in (("ui", resolved.spec.font_ui), ("title", resolved.spec.font_title),
                               ("mono", resolved.spec.font_mono)):
            chain = resolved.font_family(kind)
            assert "," not in chain, (theme_id, kind, chain)
            name = chain.strip('"')
            assert name in families
            assert name in installed or not any(f in installed for f in families), (theme_id, kind, name)
        css = theme_module.stylesheet(resolved)
        if symbol:
            assert f'*[glyph="true"] {{ font-family: "{symbol}"; }}' in css


def _symboles(texte: str) -> str:
    # Pictogrammes (flèches, formes, symboles divers) : jamais confiés à la police d'interface.
    return "".join(ch for ch in texte if 0x2190 <= ord(ch) <= 0x2BFF)


def test_chrome_and_frames_never_render_glyphs_with_the_interface_font(tmp_path):
    from PySide6.QtWidgets import QAbstractButton, QLabel

    from UI.dashboard.controller import DashboardController
    from UI.dashboard.widgets.jarvis_widgets import QuickActionsWidget

    app = QApplication.instance() or QApplication([])
    with isolated_jarvis():
        controller = DashboardController(state_path=str(tmp_path / "s.json"), attach_bridge=False,
                                         dialog_executor=lambda d: True)
        try:
            controller.open()
            window = controller.window
            canvases = window.all_canvases()
            pump(app, 0.2)
            chrome = [w for root in (window.sidebar, window.findChild(type(window.sidebar), "header"))
                      for w in root.findChildren(QAbstractButton) + root.findChildren(QLabel)]
            assert window.nav_buttons and chrome
            def texte(w):  # méthode Qt non liée (StatusPill masque text() par un attribut)
                return QAbstractButton.text(w) if isinstance(w, QAbstractButton) else QLabel.text(w)

            fautifs = [(w.objectName() or type(w).__name__, texte(w)) for w in chrome
                       if _symboles(texte(w)) and not w.property("glyph")]
            assert not fautifs, fautifs
            for button in window.nav_buttons.values():
                assert not button.icon().isNull(), "icône de page peinte avec la police de symboles"
            # Libellés conservés à côté des icônes (régression vue sur capture).
            from PySide6.QtCore import Qt

            assert window.edit_button.text() == "Éditer" and window.view_button.text() in ("OnePage", "Pages")
            for button in (window.view_button, window.edit_button, window.bell):
                assert button.toolButtonStyle() == Qt.ToolButtonTextBesideIcon
            frames = [frame for canvas in canvases for frame in canvas.frames.values()]
            assert frames
            for frame in frames:
                assert frame.icon.property("glyph")
                assert all(b.property("glyph") for b in [*frame.edit_buttons, frame.menu_button])
            for widget in window.findChildren(QuickActionsWidget):
                for button, _action in widget._buttons.values():
                    assert not _symboles(button.text()), button.text()
            controller.set_view_mode("onepage")
            onepage = window._onepage
            onepage.build_all()
            for _page, header, _canvas in onepage.sections:
                plain = header.text().replace("&nbsp;", " ")
                assert not _symboles(plain.split("</span>")[-1]), "glyphe de section hors police de symboles"
        finally:
            controller.shutdown()
