"""Points d'entrée du Dashboard : voix (outils), pont, tray, raccourci de l'orbe."""

from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src import tools  # noqa: E402
from tests.dashboard_support import pump  # noqa: E402
from UI.dashboard_bridge import DASHBOARD, DashboardBridge, resolve_page  # noqa: E402


def test_page_aliases():
    assert resolve_page("Mémoire") == "memoire"
    assert resolve_page("tasks") == "taches"
    assert resolve_page("diagnostic") == "systeme"
    assert resolve_page("") is None and resolve_page("inconnue") is None


def test_bridge_without_consumer_reports_false():
    bridge = DashboardBridge()
    assert bridge.request_open() is False
    assert bridge.state()["attached"] is False


def test_bridge_handler_errors_never_reach_the_caller():
    bridge = DashboardBridge()
    bridge.set_handler(lambda command, payload: (_ for _ in ()).throw(RuntimeError("UI")))
    assert bridge.request_open("accueil") is False


def test_tools_are_declared_and_honest_without_ui():
    names = {d["name"] for d in tools.TOOL_DECLARATIONS}
    assert {"open_dashboard", "close_dashboard"} <= names
    DASHBOARD.set_handler(None)
    result = tools.open_dashboard("memoire")
    assert result["success"] is False and "pas lancée" in result["error"]


def test_open_dashboard_tool_waits_for_real_confirmation():
    """Le succès n'est annoncé qu'une fois l'affichage RAPPORTÉ par l'interface."""
    commands = []

    def handler(command, payload):
        commands.append((command, payload))
        # Simule le thread Qt qui confirme l'affichage un peu plus tard.
        threading.Timer(0.1, lambda: DASHBOARD.report_state(visible=command == "open")).start()

    DASHBOARD.set_handler(handler)
    try:
        opened = tools.open_dashboard("tâches")
        assert opened["success"] and opened.get("confirmed", True) is True
        assert commands[0][0] == "open" and commands[0][1]["page"] == "taches"
        assert commands[0][1]["request_id"] >= 1, "demande identifiée (suivi par l'interface)"
        closed = tools.close_dashboard()
        assert closed["success"] and closed.get("confirmed", True) is True
    finally:
        DASHBOARD.set_handler(None)


def test_unconfirmed_open_is_reported_as_such():
    DASHBOARD.set_handler(lambda command, payload: None)
    DASHBOARD.report_state(visible=False)
    try:
        result = tools.open_dashboard()
        assert result["success"] and result["confirmed"] is False
        assert "non encore confirmé" in result["message"]
    finally:
        DASHBOARD.set_handler(None)


def test_tray_menu_exposes_dashboard_entry(monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon

    from src.ui import SessionVisibility, _build_tray_icon

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    opened = []
    tray = _build_tray_icon(lambda: None, lambda: None, on_hide=lambda: None, visibility=SessionVisibility(),
                            mode_getter=lambda: "blob", on_mode_change=lambda m: None,
                            on_dashboard=lambda: opened.append(1))
    assert tray is not None
    labels = [a.text() for a in tray._jarvis_menu.actions()]
    assert "Ouvrir le Dashboard" in labels
    tray._dashboard_action.trigger()
    assert opened == [1]
    tray.hide()


def test_tray_without_dashboard_keeps_historic_menu(monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon

    from src.ui import _build_tray_icon

    QApplication.instance() or QApplication([])
    monkeypatch.setattr(QSystemTrayIcon, "isSystemTrayAvailable", staticmethod(lambda: True))
    tray = _build_tray_icon(lambda: None, lambda: None)
    assert tray._dashboard_action is None
    assert "Ouvrir le Dashboard" not in [a.text() for a in tray._jarvis_menu.actions()]
    tray.hide()


def test_orb_ctrl_d_opens_dashboard_through_the_bridge():
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent

    from UI.jarvis_menu import MorphingOrbWidget

    app = QApplication.instance() or QApplication([])
    commands = []
    DASHBOARD.set_handler(lambda command, payload: commands.append(command))
    orb = MorphingOrbWidget()
    try:
        orb.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_D, Qt.ControlModifier, "d"))
        assert commands == ["open"]
        assert getattr(orb, "debug_mode", False) is False, "Ctrl+D ne bascule plus le mode debug"
        assert orb._menu_action_flash == "Dashboard"
        DASHBOARD.set_handler(None)
        orb.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_D, Qt.ControlModifier, "d"))
        assert orb._menu_action_flash == "Dashboard indisponible"
    finally:
        DASHBOARD.set_handler(None)
        orb.timer.stop()
        orb.close()
        pump(app, 0.02)


def test_fanout_isolates_dashboard_failures():
    from src.ui import _fanout

    seen = []

    def broken(*args):
        raise RuntimeError("dashboard en panne")

    def primary(state):
        seen.append(state)

    hook = _fanout(primary, broken)
    hook("listening")
    assert seen == ["listening"]
    assert _fanout(primary, None) is primary


def test_cli_flag_is_parsed():
    from src.main import build_parser

    args = build_parser().parse_args(["--ui", "--dashboard"])
    assert args.ui and args.dashboard
    args = build_parser().parse_args(["--dashboard"])
    assert args.dashboard and not args.ui
