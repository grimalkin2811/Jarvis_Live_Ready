"""Centre de notifications du Dashboard (préférences, anti-doublon, natif)."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from UI.dashboard.config import NotificationPrefs  # noqa: E402
from UI.dashboard.notification_center import (  # noqa: E402
    CATEGORY_JARVIS,
    NotificationCenter,
    notification_for_task,
)


def _center(prefs=None, foreground=False, sender=None):
    prefs = prefs or NotificationPrefs()
    sent = []
    center = NotificationCenter(lambda: prefs, native_sender=sender or (lambda t, m: sent.append((t, m))),
                                dashboard_foreground=lambda: foreground)
    return center, sent, prefs


def test_native_titles_are_labelled_dashboard_and_feed_keeps_plain_title():
    center, sent, _ = _center()
    item = center.notify("task_completed", "Tâche terminée", "J'ai terminé : X", key="task:1:COMPLETED")
    assert item.native_sent
    assert sent == [("Dashboard — Tâche terminée", "J'ai terminé : X")]
    assert center.items()[0].title == "Tâche terminée"


def test_per_category_channels():
    prefs = NotificationPrefs()
    prefs.categories["task_failed"] = {"dashboard": False, "native": True}
    prefs.categories["network"] = {"dashboard": True, "native": False}
    prefs.categories["audio"] = {"dashboard": False, "native": False}
    center, sent, _ = _center(prefs)
    center.notify("task_failed", "Tâche échouée", "x", key="a")
    center.notify("network", "Réseau", "y", key="b")
    assert center.notify("audio", "Audio", "z", key="c") is None
    assert [n.title for n in center.items()] == ["Réseau"]
    assert [t for t, _m in sent] == ["Dashboard — Tâche échouée"]


def test_duplicates_are_suppressed():
    center, sent, _ = _center()
    assert center.notify("task_completed", "T", "m", key="task:1:COMPLETED") is not None
    assert center.notify("task_completed", "T", "m", key="task:1:COMPLETED") is None
    assert len(center.items()) == 1 and len(sent) == 1
    assert center.suppressed_duplicates == 1


def test_no_native_duplicate_when_dashboard_is_in_foreground():
    center, sent, _ = _center(foreground=True)
    item = center.notify("task_completed", "T", "m", key="k")
    assert sent == [] and not item.native_sent
    assert "premier plan" in item.native_error
    assert len(center.items()) == 1


def test_native_api_failure_is_graceful():
    def broken(title, message):
        raise OSError("zone de notification indisponible")

    center, _sent, _ = _center(sender=broken)
    item = center.notify("intervention", "Clé refusée", "m", key="k")
    assert item is not None and not item.native_sent
    assert "indisponible" in item.native_error
    assert center.native_failures == 1
    assert len(center.items()) == 1


def test_unknown_category_is_ignored_and_jarvis_category_never_native():
    center, sent, _ = _center()
    assert center.notify("inconnue", "x", "y") is None
    item = center.notify(CATEGORY_JARVIS, "Rappel", "Boire de l'eau")
    assert item is not None and not item.native_requested and sent == []


def test_unread_and_clear():
    center, _sent, _ = _center()
    center.notify("network", "a", "b", key="1")
    center.notify("network", "c", "d", key="2")
    assert center.unread_count() == 2
    assert center.mark_all_read() == 2 and center.unread_count() == 0
    assert center.clear() == 2 and center.items() == []


@pytest.mark.parametrize("status,expected", [
    ("COMPLETED", "task_completed"), ("FAILED", "task_failed"),
    ("RUNNING", None), ("QUEUED", None), ("CANCELLED", None), ("BIZARRE", None),
])
def test_only_real_final_statuses_produce_notifications(status, expected):
    task = SimpleNamespace(id="t1", title="Rapport", status=SimpleNamespace(value=status),
                           error="api_key=" + "AIza" + "SECRET" * 6)  # clé factice construite à l'exécution
    mapped = notification_for_task(task)
    if expected is None:
        assert mapped is None
    else:
        assert mapped[0] == expected
        assert "SECRETSECRET" not in mapped[2]


def test_hub_routes_real_task_hooks_and_bridge_filter_avoids_legacy_duplicate(tmp_path):
    from PySide6.QtWidgets import QApplication

    from tests.dashboard_support import isolated_jarvis, make_task_manager, wait_task, wait_until
    from UI.dashboard.controller import DashboardController

    app = QApplication.instance() or QApplication([])
    sent = []
    with isolated_jarvis() as env:
        from src.background_tasks import manager as manager_module

        task_manager = make_task_manager(os.path.join(env["root"], "tasks"), fail_titles=("Échec",))
        previous = manager_module._DEFAULT
        manager_module._DEFAULT = task_manager
        try:
            controller = DashboardController(state_path=str(tmp_path / "s.json"), attach_bridge=False,
                                             native_sender=lambda t, m: sent.append(t),
                                             dialog_executor=lambda d: True)
            assert wait_until(app, controller.hub.tasks_available, 3)
            done = task_manager.create_task("Rapport", "Écrire")
            failed = task_manager.create_task("Échec", "Échouer")
            wait_task(task_manager, done.id, ["COMPLETED"])
            wait_task(task_manager, failed.id, ["FAILED"])
            assert wait_until(app, lambda: len(controller.hub.notifications.items()) >= 2, 3)
            titles = sorted(n.title for n in controller.hub.notifications.items())
            assert titles == ["Tâche terminée", "Tâche échouée"]
            assert sorted(sent) == ["Dashboard — Tâche terminée", "Dashboard — Tâche échouée"]
            # Le relais natif historique (« Tâche terminée ») est filtré : pas de doublon.
            assert controller._native_filter("Tâche terminée", "x") is False
            assert controller._native_filter("Rappel", "x") is True
            # Fenêtre jamais ouverte : les notifications fonctionnent Dashboard fermé.
            assert controller.window is None
            controller.shutdown()
        finally:
            manager_module._DEFAULT = previous
            task_manager.close(wait=True)


def test_notification_bridge_filter_suppresses_display_only():
    from PySide6.QtWidgets import QApplication

    from UI.notification_bridge import NotificationBridge

    QApplication.instance() or QApplication([])
    bridge = NotificationBridge(None)
    try:
        bridge.add_filter(lambda title, message: title != "Tâche terminée")
        bridge._receive("Tâche terminée", "x")
        assert len(bridge.pending) == 0 and bridge.popup is None
        bridge._receive("Rappel", "boire")
        assert bridge.popup is not None and "Rappel" in bridge.popup.text()
    finally:
        bridge.close()
