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


def _texte_hors_police_de_symboles(texte: str) -> str:
    """Texte rendu avec la police d'interface : un texte riche perd ses ``span`` de symboles."""
    import re

    if "<" in texte and ">" in texte:
        texte = re.sub(r"<span style=\"font-family:'[^']+'\">.*?</span>", "", texte)
        texte = re.sub(r"<[^>]+>", "", texte)
    return texte.replace("&nbsp;", " ")


def test_no_dashboard_text_leaves_a_glyph_to_the_interface_font(tmp_path):
    """Toutes les pages, le journal (ligne d'erreur) et le catalogue : aucun symbole absent
    de la police d'interface dans un texte qu'elle rend (sinon : polices du système
    rechargées au premier rendu sous Windows, correctif D01)."""
    import logging

    from PySide6.QtWidgets import QAbstractButton, QComboBox, QLabel, QListWidget

    from UI.dashboard.controller import DashboardController
    from UI.dashboard.dialogs import WidgetLibraryDialog
    from UI.dashboard.glyphs import needs_symbol_font

    app = QApplication.instance() or QApplication([])
    with isolated_jarvis():
        controller = DashboardController(state_path=str(tmp_path / "s.json"), attach_bridge=False,
                                         dialog_executor=lambda d: True)
        try:
            controller.open()
            window = controller.window
            controller.set_view_mode("onepage")
            window._onepage.build_all()
            window.hub.logs.record_event("échec simulé pour le test", level=logging.ERROR, source="test")
            # Contenus dynamiques de TOUS les widgets (activés d'ordinaire à leur entrée à l'écran).
            from UI.dashboard.widget_api import DashboardWidget

            widgets = window.findChildren(DashboardWidget)
            assert len(widgets) >= 20, len(widgets)
            for widget in widgets:
                widget.activate()
            pump(app, 0.6)
            page = window.store.config.layout.page("accueil")
            dialog = WidgetLibraryDialog(window.registry, page, window, icon_color=window.theme_manager.theme.text)
            fautifs = []
            for racine in (window, dialog):
                for w in racine.findChildren(QLabel):
                    if not w.property("glyph") and needs_symbol_font(_texte_hors_police_de_symboles(QLabel.text(w))):
                        fautifs.append((type(w).__name__, QLabel.text(w)[:80]))
                for w in racine.findChildren(QAbstractButton):
                    if not w.property("glyph") and needs_symbol_font(QAbstractButton.text(w)):
                        fautifs.append((type(w).__name__, QAbstractButton.text(w)))
                for liste in racine.findChildren(QListWidget):
                    textes = [liste.item(i).text() for i in range(liste.count())]
                    fautifs += [("QListWidgetItem", t[:80]) for t in textes if needs_symbol_font(t)]
                for combo in racine.findChildren(QComboBox):
                    textes = [combo.itemText(i) for i in range(combo.count())]
                    fautifs += [("QComboBox", t) for t in textes if needs_symbol_font(t)]
            assert not fautifs, fautifs
            # La ligne d'erreur est bien affichée (et son repère est dans la police d'interface).
            from UI.dashboard.widgets.system_widgets import ActivityWidget

            lignes = [w.list.item(i).text() for w in window.findChildren(ActivityWidget) for i in range(w.list.count())]
            assert any(t.startswith("×") and "échec simulé" in t for t in lignes), lignes[:5]
            # Icônes peintes (police de symboles) dans le catalogue et la liste des pages.
            assert dialog.list.count() and all(not dialog.list.item(i).icon().isNull() for i in range(dialog.list.count()))
            from UI.dashboard.widgets.settings_widgets import LayoutSettingsWidget

            for widget in window.findChildren(LayoutSettingsWidget):
                assert widget.pages.count()
                assert all(not widget.pages.item(i).icon().isNull() for i in range(widget.pages.count()))
            dialog.deleteLater()
        finally:
            controller.shutdown()


def test_painted_glyphs_use_the_symbol_font(monkeypatch):
    """Anneau d'état (« ✕ » : Jarvis indisponible, cas d'un démarrage hors ligne) et carte
    du thème sélectionné (« ✓ ») : dessinés avec la police de symboles."""
    from PySide6.QtGui import QFont, QPixmap

    from UI.dashboard.glyphs import symbol_family
    from UI.dashboard.widgets import settings_widgets
    from UI.dashboard.widgets.common import Ring

    app = QApplication.instance() or QApplication([])
    assert app is not None
    with isolated_jarvis():
        from types import SimpleNamespace

        from UI.dashboard.config import AppearancePrefs
        from UI.dashboard.theme import resolve

        ring = Ring(SimpleNamespace(theme=resolve(AppearancePrefs(), reduced_motion=True)))
        base = QFont("Interface de test")
        symbole = symbol_family()
        assert symbole, "aucune police de symboles installée : test sans objet"
        assert ring.text_font(base, "✕").families()[0] == symbole
        assert ring.text_font(base, "OK").families()[0] == "Interface de test"
        ring.set_value(0.15, "error", "✕")
        ring.render(QPixmap(ring.size()))  # dessin réel, sans erreur

        appels = []
        original = settings_widgets.symbol_font

        def espion(font):
            appels.append(font)
            return original(font)

        monkeypatch.setattr(settings_widgets, "symbol_font", espion)
        carte = settings_widgets.ThemeCard(next(iter(settings_widgets.THEMES)))
        carte.resize(160, 90)
        carte.render(QPixmap(carte.size()))
        assert not appels, "carte non sélectionnée : aucun glyphe"
        carte.selected = True
        carte.render(QPixmap(carte.size()))
        assert len(appels) == 1


def test_memory_importance_is_drawn_with_interface_font_characters():
    from UI.dashboard.glyphs import needs_symbol_font
    from UI.dashboard.widgets.memory_widget import importance_marks

    assert importance_marks(3) == "●●●○○"
    assert importance_marks(5) == "●●●●●" and importance_marks(None) == "○○○○○"
    assert importance_marks(9) == "●●●●●" and importance_marks("x") == "○○○○○"
    assert not needs_symbol_font("".join(importance_marks(n) for n in range(6)))


def test_symbol_inventory_is_checked_against_real_fonts_on_native_windows():
    """L'inventaire couvre les symboles réellement affichés et le workflow natif ÉCHOUE si
    l'une des deux prémisses du correctif n'est pas vérifiée par les polices de Windows."""
    import importlib.util
    from pathlib import Path

    from UI.dashboard.glyphs import INTERFACE_FONT_SYMBOLS, needs_symbol_font

    racine = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("profil_d01", racine / "scripts" / "dashboard_first_open_profile.py")
    profil = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(profil)
    inventaire = profil.symboles_du_dashboard()
    assert set("⌂⚙✎☑◈◎⤢✉⋯✕⊘✓") <= set(inventaire), inventaire
    assert not set("★✖▲") & set(inventaire), "symboles retirés du texte"
    assert needs_symbol_font("⌂⚙✕⊘") == "⌂⚙✕⊘" and not needs_symbol_font("".join(INTERFACE_FONT_SYMBOLS))
    flux = (racine / ".github" / "workflows" / "pr-windows-native.yml").read_text(encoding="utf-8")
    etape = flux.split("- name: Couverture des symboles du Dashboard (correctif D01)", 1)[1]
    assert "if: always()" in etape.split("run:", 1)[0]
    assert "continue-on-error" not in etape.split("run:", 1)[0]
    assert "interface_non_couverts" in etape and "symboles_non_couverts" in etape
    assert "sys.exit(1 if echecs else 0)" in etape


def _ouvrir_jusqu_au_debut_du_contenu(controller, app):
    controller.open()
    window = controller.window
    deadline = time.monotonic() + 5.0
    while window._initial_build_pending and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.002)
    assert not window._initial_build_pending
    return window


def test_first_open_builds_the_start_page_in_batches(tmp_path, monkeypatch):
    """Correctif C10 : sous Windows natif, la latence maximale de la boucle Qt était mesurée
    pendant la construction du contenu initial (page d'accueil créée d'un bloc). La page est
    désormais construite par lots, la boucle Qt restant réactive entre deux lots."""
    from UI.dashboard.controller import DashboardController
    from UI.dashboard.pages_view import PageView

    monkeypatch.setattr(PageView, "BUILD_BUDGET_MS", 0.0)  # un cadre par lot (au moins un garanti)
    app = QApplication.instance() or QApplication([])
    with isolated_jarvis():
        controller = DashboardController(state_path=str(tmp_path / "s.json"), attach_bridge=False,
                                         dialog_executor=lambda d: True)
        try:
            window = _ouvrir_jusqu_au_debut_du_contenu(controller, app)
            view = window._page_views[window.current_page()]
            attendus = sorted(p.id for p in view.page.widgets if not p.hidden)
            assert len(attendus) >= 3
            assert window.content_pending() and len(view.canvas.frames) < len(attendus), "page construite par lots"
            observes = set()
            deadline = time.monotonic() + 5.0
            while window.content_pending() and time.monotonic() < deadline:
                observes.add(len(view.canvas.frames))
                app.processEvents()
            assert not window.content_pending(), "le contenu arrive sans intervention"
            assert len(observes) >= 2, observes
            assert sorted(view.canvas.frames) == attendus
            stats = window.widget_stats()
            assert stats["active_widgets"] == stats["widgets"] == len(attendus)
        finally:
            controller.shutdown()


def test_programmatic_access_gets_a_complete_start_page(tmp_path, monkeypatch):
    from UI.dashboard.controller import DashboardController
    from UI.dashboard.pages_view import PageView

    monkeypatch.setattr(PageView, "BUILD_BUDGET_MS", 0.0)
    app = QApplication.instance() or QApplication([])
    for acces in ("all_canvases", "open_page"):
        with isolated_jarvis():
            controller = DashboardController(state_path=str(tmp_path / f"{acces}.json"), attach_bridge=False,
                                             dialog_executor=lambda d: True)
            try:
                window = _ouvrir_jusqu_au_debut_du_contenu(controller, app)
                assert window.content_pending()
                if acces == "all_canvases":
                    window.all_canvases()
                else:
                    window.open_page(window.current_page())
                assert not window.content_pending(), acces
                view = window._page_views[window.current_page()]
                assert sorted(view.canvas.frames) == sorted(p.id for p in view.page.widgets if not p.hidden)
            finally:
                controller.shutdown()


def test_start_page_is_not_built_while_the_window_is_hidden(tmp_path, monkeypatch):
    from UI.dashboard.controller import DashboardController
    from UI.dashboard.pages_view import PageView

    monkeypatch.setattr(PageView, "BUILD_BUDGET_MS", 0.0)
    app = QApplication.instance() or QApplication([])
    with isolated_jarvis():
        controller = DashboardController(state_path=str(tmp_path / "s.json"), attach_bridge=False,
                                         dialog_executor=lambda d: True)
        try:
            window = _ouvrir_jusqu_au_debut_du_contenu(controller, app)
            view = window._page_views[window.current_page()]
            controller.hide_window()
            app.processEvents()
            figes = len(view.canvas.frames)
            assert not view._build_timer.isActive(), "aucun minuteur de construction fenêtre masquée"
            pump(app, 0.2)
            assert len(view.canvas.frames) == figes, "aucune construction fenêtre masquée"
            assert window.widget_stats()["active_widgets"] == 0
            controller.open()
            deadline = time.monotonic() + 5.0
            while window.content_pending() and time.monotonic() < deadline:
                app.processEvents()
            assert not window.content_pending(), "reprise à la réouverture"
        finally:
            controller.shutdown()


def test_first_open_probe_reports_sane_phases(tmp_path):
    """La sonde de profil D01 (scripts/dashboard_first_open_profile.py) doit horodater la vraie
    première peinture : elle enveloppe les DEUX points d'entrée qui peuvent suivre la peinture
    (``_complete_initial_build_incrementally``, chemin naturel, et ``_complete_initial_build``,
    filet des accès directs) — chacun suffit ; retirer les deux fait échouer ce test. Le jour où
    le chemin a changé sans la sonde, la mesure native a rapporté « première peinture
    14 993 ms, contenu 0 ms » : artefact de mesure, pas du produit."""
    import json
    import subprocess

    script = ROOT + "/scripts/dashboard_first_open_profile.py"
    out = subprocess.run([sys.executable, script, "--enfant", "ouverture", "--reouvertures", "0"],
                        cwd=ROOT, capture_output=True, text=True, timeout=180,
                        env=dict(os.environ, QT_QPA_PLATFORM="offscreen",
                                 LD_LIBRARY_PATH=os.environ.get("LD_LIBRARY_PATH", "")))
    ligne = next((l for l in reversed(out.stdout.splitlines()) if l.startswith("{")), "")
    assert ligne, out.stdout[-800:] + out.stderr[-800:]
    data = json.loads(ligne)
    phases = data["phases_ms"]
    assert 0.0 < phases["premiere_peinture"] < 1500.0, phases
    assert 0.0 < phases["contenu"] < 5000.0, phases
    assert data["premiere_ouverture_ms"] < 2500.0, data["premiere_ouverture_ms"]
    assert 0.0 < data["contenu_complet_ms"] < 8000.0


def test_no_glyph_known_to_scan_the_system_fonts():
    """Mesure native (sonde par glyphe, processus neufs) : sur les 37 glyphes de
    l'interface, « ⚡ » (U+26A1) est le SEUL dont le rendu charge les polices du système
    (149 fichiers), en texte comme en icône peinte — les 36 autres en coûtent 1. La tuile
    « Actions rapides » utilise « ▷ ». La garde générale est l'étape native « Couverture des
    symboles du Dashboard » (coût par glyphe, échec au-delà de 5 fichiers)."""
    import importlib.util

    from pathlib import Path

    from UI.dashboard.registry import default_registry

    racine = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("profil_d01_glyphes",
                                                  racine / "scripts" / "dashboard_first_open_profile.py")
    profil = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(profil)
    inventaire = profil.symboles_du_dashboard()
    assert "⚡" not in inventaire, "glyphe dont le rendu balaie les polices du système (mesure native)"
    assert "▷" in inventaire
    spec_widget = default_registry().spec("jarvis.quick_actions")
    assert spec_widget is not None and spec_widget.icon == "▷", spec_widget and spec_widget.icon
    flux = (racine / ".github" / "workflows" / "pr-windows-native.yml").read_text(encoding="utf-8")
    etape = flux.split("- name: Couverture des symboles du Dashboard (correctif D01)", 1)[1]
    assert "couts_glyphes" in etape and "> 5" in etape, "la garde native du coût par glyphe a disparu"
