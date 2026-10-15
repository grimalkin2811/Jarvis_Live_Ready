"""Intégration fenêtre/contrôleur du Dashboard (hors écran, données isolées)."""

from __future__ import annotations

import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.dashboard_support import isolated_jarvis, make_task_manager, pump, wait_until  # noqa: E402


class FakeInterfaceController(QObject):
    mode_changed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.mode = "blob"
        self.calls: list[str] = []

    def current_mode(self) -> str:
        return self.mode

    def set_mode(self, mode: str) -> str:
        self.calls.append(mode)
        self.mode = mode
        self.mode_changed.emit(mode)
        return mode


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def make_controller(app, tmp_path):
    from UI.dashboard.controller import DashboardController

    created = []
    quits = []
    with isolated_jarvis() as env:
        def make(**kwargs):
            kwargs.setdefault("state_path", str(tmp_path / "dashboard_state.json"))
            kwargs.setdefault("attach_bridge", False)
            kwargs.setdefault("dialog_executor", lambda dialog: True)
            kwargs.setdefault("open_path", lambda path: True)
            kwargs.setdefault("native_sender", lambda title, message: None)
            kwargs.setdefault("reduced_motion_probe", lambda: False)
            kwargs.setdefault("quit_callback", lambda: quits.append(1))
            controller = DashboardController(**kwargs)
            created.append(controller)
            return controller

        make.quits = quits
        make.env = env
        yield make
        for controller in created:
            controller.shutdown()
        pump(app, 0.05)


def test_controller_is_light_until_opened(make_controller):
    controller = make_controller()
    assert controller.window is None
    assert controller.hub.started
    stats = controller.performance_snapshot()
    assert stats["widgets"] == 0 and stats["active_timers"] == 0 and not stats["resource_thread"]


def test_open_hide_toggle_and_page_navigation(app, make_controller):
    controller = make_controller()
    controller.open("memoire")
    pump(app, 0.1)
    assert controller.is_open()
    assert controller.window.current_page() == "memoire"
    controller.open("tâches")  # alias vocal
    assert controller.window.current_page() == "taches"
    controller.hide_window()
    assert not controller.is_open() and controller.window is not None
    controller.toggle()
    assert controller.is_open()
    for page in [p.id for p in controller.store.config.layout.pages]:
        assert controller.window.open_page(page, animate=False)


def test_bridge_command_from_another_thread(app, make_controller):
    from UI.dashboard_bridge import DASHBOARD

    controller = make_controller(attach_bridge=True)
    thread = threading.Thread(target=lambda: DASHBOARD.request_open("systeme"))
    thread.start()
    thread.join()
    assert wait_until(app, controller.is_open, 3)
    assert controller.window.current_page() == "systeme"
    assert DASHBOARD.state()["visible"] is True
    DASHBOARD.request_close()
    assert wait_until(app, lambda: not controller.is_open(), 3)
    assert DASHBOARD.state()["visible"] is False


def test_switching_interfaces_never_touches_engine_conversation_or_mic(app, make_controller):
    import concurrent.futures

    from src.conversation import get_default_conversation_context
    from src.runtime_status import RUNTIME, EngineState
    from UI import menu_state

    RUNTIME.reset()
    RUNTIME.set_engine_state(EngineState.READY)
    handler = lambda text: concurrent.futures.Future()  # noqa: E731
    RUNTIME.register_text_handler(handler)
    context = get_default_conversation_context()
    context.start_new_conversation("test")
    context.add_user_message("avant bascule")
    conversation_id = context.conversation_id
    mic_before = menu_state.LIVE.get_mic_enabled()
    revision = RUNTIME.snapshot()["revision"]
    interface = FakeInterfaceController()
    controller = make_controller(interface_controller=interface)
    controller.open()
    controller.set_interface_mode("desktop")
    controller.hide_window()
    controller.open()
    controller.set_interface_mode("blob")
    controller.window.close()  # mode persistant
    pump(app, 0.1)
    assert interface.calls == ["desktop", "blob"]
    assert controller.hub.interface_mode() == "blob"
    assert context.conversation_id == conversation_id, "aucune nouvelle conversation"
    assert [m.text for m in context.get_messages()] == ["avant bascule"]
    assert menu_state.LIVE.get_mic_enabled() == mic_before, "micro jamais interrompu"
    snap = RUNTIME.snapshot()
    assert snap["revision"] == revision and snap["engine_state"] == EngineState.READY, "moteur jamais redémarré"
    assert snap["text_input"] is True
    RUNTIME.reset()


def test_close_mode_persistent_hides_and_keeps_jarvis(app, make_controller):
    controller = make_controller()
    controller.open()
    event = QCloseEvent()
    controller.window.closeEvent(event)
    assert not event.isAccepted()
    assert not controller.is_open() and controller.window is not None
    assert make_controller.quits == []


def test_close_mode_interactive_reduces_non_essential_services(app, make_controller):
    controller = make_controller()
    config = controller.store.config
    config.close.mode = "interactive"
    config.close.interactive.update(suspend_monitoring=True, suspend_activity=True, release_window=True)
    controller.open()
    controller.window.closeEvent(QCloseEvent())
    pump(app, 0.05)
    assert controller.window is None, "fenêtre et widgets libérés"
    assert controller.hub.resources.suspended
    assert controller.hub.logs.paused
    assert set(controller.closed_reductions()) == {"monitoring", "activity", "window"}
    assert make_controller.quits == [], "Jarvis continue"
    controller.open()
    assert not controller.hub.resources.suspended and not controller.hub.logs.paused
    assert controller.closed_reductions() == {}


def test_close_mode_shutdown_explains_and_requires_reinforced_confirmation(app, make_controller):
    from src.background_tasks import manager as manager_module

    seen = {}

    def executor(dialog):
        seen["check"] = dialog.check
        seen["quit_enabled"] = dialog.quit_button.isEnabled()
        dialog.choice = "quit"
        return "quit"

    block = threading.Event()
    task_manager = make_task_manager(os.path.join(make_controller.env["root"], "tm"), block=block)
    previous = manager_module._DEFAULT
    manager_module._DEFAULT = task_manager
    try:
        controller = make_controller(dialog_executor=executor)
        controller.store.config.close.mode = "shutdown"
        task_manager.create_task("Longue", "x")
        assert wait_until(app, lambda: controller.active_task_titles() == ["Longue"], 3)
        controller.open()
        controller.window.closeEvent(QCloseEvent())
        assert seen["check"] is not None and seen["quit_enabled"] is False
        assert make_controller.quits == [1]
    finally:
        block.set()
        manager_module._DEFAULT = previous
        task_manager.close(wait=True)


def test_fullscreen_toggle_and_restore(app, make_controller):
    controller = make_controller()
    controller.open()
    window = controller.window
    window.toggle_fullscreen()
    pump(app, 0.05)
    assert window.isFullScreen() and controller.store.config.window.state == "fullscreen"
    window.toggle_fullscreen()
    pump(app, 0.05)
    assert not window.isFullScreen() and controller.store.config.window.state == "normal"


def test_window_preferences_survive_a_restart(app, make_controller):
    """Géométrie, OnePage et dernière page relus par un NOUVEAU contrôleur."""
    controller = make_controller()
    controller.open("taches")
    window = controller.window
    # Taille contenue dans l'écran hors écran (800×800) : Qt recadre une
    # géométrie restaurée plus grande que l'écran disponible (voulu).
    window.resize(760, 560)
    pump(app, 0.1)
    controller.set_view_mode("onepage")
    pump(app, 0.1)
    controller.hide_window()  # enregistre la géométrie
    assert controller.flush()
    controller.shutdown()
    again = make_controller()
    assert again.store.load_status == "loaded"
    assert again.store.config.layout.view_mode == "onepage"
    assert again.store.config.window.last_page == "taches"
    again.open()
    pump(app, 0.1)
    assert (again.window.width(), again.window.height()) == (760, 560)
    assert again.window.onepage_order()[-1] == "systeme"


@pytest.mark.parametrize("width,size_class,sidebar", [(800, "narrow", 64), (1250, "normal", 196), (1700, "wide", 196)])
def test_responsive_states(app, make_controller, width, size_class, sidebar):
    controller = make_controller()
    controller.open()
    controller.window.resize(width, 900)
    pump(app, 0.15)
    assert controller.window.size_class() == size_class
    assert controller.window.sidebar.width() == sidebar
    canvas = controller.window._current_canvas()
    assert canvas.columns == {"narrow": 4, "normal": 8, "wide": 12}[size_class]


def test_onepage_puts_diagnostics_last_and_activates_visible_widgets_only(app, make_controller):
    controller = make_controller()
    controller.open()
    controller.window.resize(1500, 900)
    controller.set_view_mode("onepage")
    pump(app, 0.3)
    order = controller.window.onepage_order()
    assert order[-1] == "systeme" and order[-2] == "parametres"
    stats = controller.window.widget_stats()
    assert 0 < stats["active_widgets"] < stats["widgets"], "seuls les widgets visibles sont actifs"
    assert controller.store.config.layout.view_mode == "onepage"
    controller.set_view_mode("multipage")
    pump(app, 0.1)
    assert controller.window.onepage_order() == []


def test_edit_mode_add_move_hide_remove_cancel_and_save(app, make_controller, tmp_path):
    from UI.dashboard.layout_model import has_overlap

    controller = make_controller()
    controller.open()
    window = controller.window
    window.resize(1600, 950)
    pump(app, 0.1)
    page = controller.store.config.layout.page("accueil")
    before = [w.to_dict() for w in page.widgets]
    window.set_edit_mode(True)
    placement = window.add_widget("custom.panel")
    assert placement is not None
    canvas = window._current_canvas()
    assert canvas.move_placement(placement.id, 0, 0)
    assert not has_overlap(page.widgets)
    window._hide_widget(canvas, page.widgets[1])
    window._remove_widget(canvas, page.widgets[2])
    window.set_edit_mode(False, save=False)  # Annuler
    restored = controller.store.config.layout.page("accueil")
    assert [w.to_dict() for w in restored.widgets] == before
    window.set_edit_mode(True)
    added = window.add_widget("custom.panel")
    window.set_edit_mode(False, save=True)
    assert controller.store.config.layout.preset == "personnalisee"
    assert controller.flush()
    # Redémarrage : un nouveau contrôleur relit la même disposition.
    controller.shutdown()
    again = make_controller()
    assert any(w.id == added.id for w in again.store.config.layout.page("accueil").widgets)


def test_widget_hidden_or_removed_never_deletes_business_data(app, make_controller):
    controller = make_controller()
    memory = make_controller.env["memory"]
    memory.create_memory_entry("Donnée métier", "fact", 3)
    controller.open("memoire")
    window = controller.window
    canvas = window._current_canvas()
    placement = canvas.page.widgets[0]
    window._remove_widget(canvas, placement)
    assert memory.memory_stats()["total"] == 1


def test_theme_live_switch_is_persisted(app, make_controller):
    controller = make_controller()
    controller.open()
    for theme in ("edith", "friday", "jarvis"):
        controller.theme_manager.update(theme=theme)
        pump(app, 0.05)
        assert controller.theme_manager.theme.spec.id == theme
        assert theme_marker(controller.window.styleSheet(), controller.theme_manager.theme)
    controller.theme_manager.update(theme="edith", accent="#00ff88", glow=10, panel_opacity=60, density="compact",
                                    animation_level="leger")
    controller.flush()
    from UI.dashboard.config import load_config

    saved = load_config(controller.store.path).config.appearance
    assert (saved.theme, saved.accent, saved.glow, saved.panel_opacity, saved.density, saved.animation_level) == (
        "edith", "#00ff88", 10, 60, "compact", "leger")


def theme_marker(stylesheet: str, theme) -> bool:
    return theme.accent.name() in stylesheet


def test_hidden_window_has_no_active_widgets_or_timers(app, make_controller):
    controller = make_controller()
    controller.open()
    pump(app, 0.2)
    assert controller.window.widget_stats()["active_widgets"] > 0
    controller.hide_window()
    pump(app, 0.05)
    stats = controller.window.widget_stats()
    assert stats["active_widgets"] == 0 and stats["active_timers"] == 0
    assert not controller.hub.resources.running()


def test_light_animation_level_uses_no_animation_timer(app, make_controller):
    controller = make_controller()
    controller.theme_manager.update(animation_level="leger")
    controller.open()
    pump(app, 0.1)
    assert not controller.window.root.timer.isActive()
    controller.theme_manager.update(animation_level="pousse")
    assert controller.window.root.timer.isActive()
    controller.theme_manager.update(animation_level="normal", respect_system_motion=True)


def test_system_reduced_motion_forces_light_level(make_controller):
    controller = make_controller(reduced_motion_probe=lambda: True)
    controller.theme_manager.update(animation_level="pousse", respect_system_motion=True)
    assert controller.theme_manager.theme.animation_level == "leger"
    assert controller.theme_manager.theme.reduced_by_system
    controller.theme_manager.update(respect_system_motion=False)
    assert controller.theme_manager.theme.animation_level == "pousse"


def test_corrupted_preferences_never_block_launch(app, tmp_path, make_controller):
    path = tmp_path / "broken.json"
    path.write_text("{oups", encoding="utf-8")
    controller = make_controller(state_path=str(path))
    controller.open()
    assert controller.is_open()
    assert controller.store.load_status == "recovered"
    assert any(n.category == "intervention" for n in controller.hub.notifications.items())


def test_quick_action_interface_switch_goes_through_controller(app, make_controller):
    from UI.dashboard.actions import ActionHost, build_actions

    interface = FakeInterfaceController()
    controller = make_controller(interface_controller=interface)
    host = ActionHost(set_interface_mode=controller.set_interface_mode,
                      current_interface_mode=controller.current_interface_mode, open_path=lambda p: True)
    actions = {a.key: a for a in build_actions(host)}
    assert actions["interface.blob"].availability()[0] is False  # déjà actif
    result = actions["interface.desktop"].run()
    assert result.success and interface.mode == "desktop"
    missing_folder = actions["folder.results"].availability()
    assert missing_folder[0] is False and "pas encore" in missing_folder[1]


def _conversation_widget(window):
    from UI.dashboard.widgets.conversation_widget import ConversationWidget

    for canvas in window.all_canvases():
        for frame in canvas.frames.values():
            if isinstance(frame.widget, ConversationWidget):
                return frame.widget
    return None


def test_closing_the_window_mid_send_keeps_the_turn_and_shows_it_on_reopen(app, make_controller, caplog):
    """Audit pré-fusion : fermeture (mode interactif, fenêtre LIBÉRÉE) pendant un envoi."""
    import logging

    from PySide6.QtCore import QCoreApplication, QEvent

    from src.conversation import get_default_conversation_context
    from src.runtime_status import RUNTIME, EngineState
    from tests.dashboard_support import FakeTextEngine

    controller = make_controller()
    config = controller.store.config
    config.close.mode = "interactive"
    config.close.interactive.update(release_window=True)
    context = get_default_conversation_context()
    context.start_new_conversation("test fermeture pendant envoi")
    engine = FakeTextEngine(context, mode="hold")
    RUNTIME.reset()
    RUNTIME.set_engine_state(EngineState.READY)
    RUNTIME.register_text_handler(engine)
    try:
        controller.open("accueil")
        conversation = _conversation_widget(controller.window)
        assert conversation is not None, "la page Accueil affiche la conversation"
        assert wait_until(app, conversation.send_button.isEnabled, 2)
        conversation.input.setPlainText("Écrit puis fenêtre fermée")
        conversation.send()
        assert conversation._pending
        controller.window.closeEvent(QCloseEvent())
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        pump(app, 0.05)
        assert controller.window is None, "fenêtre et widgets réellement détruits"
        assert make_controller.quits == [], "Jarvis continue"

        caplog.set_level(logging.ERROR)

        def engine_loop_thread():
            context.add_user_message("Écrit puis fenêtre fermée")
            engine.release(ok=True)
            context.add_assistant_message("Réponse reçue fenêtre fermée.")

        worker = threading.Thread(target=engine_loop_thread)
        worker.start()
        worker.join()
        pump(app, 0.2)
        errors = [f"{r.name}: {r.getMessage()}" for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors == [], errors

        controller.open("accueil")
        conversation = _conversation_widget(controller.window)
        assert wait_until(app, lambda: "Réponse reçue fenêtre fermée." in conversation.view.toPlainText(), 3)
        assert not conversation._pending and conversation.send_button.isEnabled()
    finally:
        RUNTIME.reset()


def test_released_window_no_longer_reacts_to_engine_changes(app, make_controller, monkeypatch):
    """Audit pré-fusion : aucune connexion du hub (durable) vers une fenêtre libérée.

    Avant correctif, une lambda connectée à ``hub.engine_changed`` survivait à
    la fenêtre : chaque changement d'état du moteur (reconnexion…) levait
    « Internal C++ object (StatusPill) already deleted » et retenait l'objet.
    """
    import sys

    from PySide6.QtCore import QCoreApplication, QEvent

    from src.runtime_status import RUNTIME, EngineState

    raised = []
    monkeypatch.setattr(sys, "excepthook", lambda *exc: raised.append(exc[1]))
    controller = make_controller()
    controller.store.config.close.mode = "interactive"
    controller.store.config.close.interactive.update(release_window=True)
    RUNTIME.reset()
    try:
        for _cycle in range(2):
            controller.open()
            controller.window.closeEvent(QCloseEvent())
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            pump(app, 0.05)
            assert controller.window is None
        RUNTIME.set_engine_state(EngineState.RECONNECTING, "test")
        RUNTIME.set_engine_state(EngineState.READY)
        pump(app, 0.1)
        assert raised == [], [repr(e) for e in raised]
    finally:
        RUNTIME.reset()
        pump(app, 0.05)


def test_voice_open_dashboard_reports_the_page_really_shown(app, make_controller):
    """Audit pré-fusion : l'outil vocal n'annonce jamais une page qui n'est pas affichée.

    Avant correctif : ``open_dashboard("page-inexistante")`` répondait
    ``page="page-inexistante"`` (« Dashboard ouvert ») alors que la fenêtre
    restait sur la page précédente ; et, Dashboard déjà visible, la
    confirmation pouvait précéder le traitement réel de la demande.
    """
    from src import tools
    from UI.dashboard_bridge import DASHBOARD

    controller = make_controller(attach_bridge=True)
    results = {}

    def voice_tool(key, *args):
        worker = threading.Thread(target=lambda: results.__setitem__(key, tools.open_dashboard(*args)))
        worker.start()
        assert wait_until(app, lambda: not worker.is_alive(), 5)
        return results[key]

    try:
        first = voice_tool("memoire", "mémoire")
        assert first["success"] and first.get("confirmed", True) is True
        assert first["page"] == "memoire" and controller.window.current_page() == "memoire"

        unknown = voice_tool("inconnue", "page-qui-n-existe-pas")
        assert unknown["success"] and unknown["page_found"] is False
        assert unknown["page"] == "memoire" == controller.window.current_page()
        assert "introuvable" in unknown["message"]

        # Dashboard DÉJÀ visible : la réponse attend le traitement de CETTE demande.
        switched = voice_tool("taches", "tâches")
        assert switched["page"] == "taches" == controller.window.current_page()
        assert "page_found" not in switched

        closer = threading.Thread(target=lambda: results.__setitem__("close", tools.close_dashboard()))
        closer.start()
        assert wait_until(app, lambda: not closer.is_alive(), 5)
        assert results["close"]["success"] and not controller.window.isVisible()
        assert make_controller.quits == [], "fermer par la voix ne quitte jamais Jarvis"
    finally:
        DASHBOARD.set_handler(None)
