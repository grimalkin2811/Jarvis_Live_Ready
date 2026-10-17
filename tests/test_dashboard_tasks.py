"""Tâches d'arrière-plan dans le Dashboard : vrai Task Manager, faux modèles."""

from __future__ import annotations

import os
import threading

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.dashboard_support import isolated_jarvis, make_task_manager, wait_task, wait_until  # noqa: E402
from UI.dashboard.tasks_service import STATUS_LABELS, TasksService, supported_actions  # noqa: E402


@pytest.fixture
def manager(tmp_path):
    block = threading.Event()
    manager = make_task_manager(tmp_path / "tm", block=block, fail_titles=("Échec",))
    manager.test_block = block
    yield manager
    block.set()
    manager.close(wait=True)


def test_status_labels_cover_all_real_statuses():
    from src.background_tasks.models import TaskStatus

    assert set(STATUS_LABELS) == {status.value for status in TaskStatus}


def test_supported_actions_never_offer_pause_or_resume(manager):
    service = TasksService(lambda: manager)
    running = manager.create_task("Longue", "x")
    wait_task(manager, running.id, ["RUNNING"])
    assert supported_actions(manager.get_task(running.id)) == ["cancel"]
    manager.test_block.set()
    done = wait_task(manager, running.id, ["COMPLETED"])
    assert "view" in supported_actions(done) and "cancel" not in supported_actions(done)
    failed = manager.create_task("Échec", "x")
    failed = wait_task(manager, failed.id, ["FAILED"])
    assert set(supported_actions(failed)) == {"retry", "view"}
    for task in service.list_tasks():
        assert not {"pause", "resume"} & set(supported_actions(task))


def test_cancel_and_retry_report_service_results(manager):
    service = TasksService(lambda: manager)
    task = manager.create_task("Longue", "x")
    wait_task(manager, task.id, ["RUNNING", "QUEUED"])
    ok, message = service.cancel(task.id)
    assert ok
    wait_task(manager, task.id, ["CANCELLED"])
    ok, message = service.cancel(task.id)
    assert not ok and "plus être annulée" in message
    ok, message, new_task = service.retry(task.id)
    assert ok and new_task.id != task.id


def test_unavailable_manager_is_explained():
    def missing():
        raise RuntimeError("Le gestionnaire de tâches d'arrière-plan n'est pas initialisé.")

    service = TasksService(missing)
    assert service.manager() is None
    assert "initialisé" in service.unavailable_reason()
    assert service.list_tasks() == []
    assert service.cancel("x")[0] is False
    assert service.model_status()["available"] is False


def test_widget_reflects_real_hook_updates(manager, tmp_path):
    from UI.dashboard.config import ConfigStore
    from UI.dashboard.hub import DashboardHub
    from UI.dashboard.theme import ThemeManager
    from UI.dashboard.widget_api import WidgetContext
    from UI.dashboard.widgets.tasks_widgets import BackgroundTasksWidget

    app = QApplication.instance() or QApplication([])
    with isolated_jarvis():
        store = ConfigStore(tmp_path / "s.json")
        hub = DashboardHub(store, tasks_service=TasksService(lambda: manager))
        hub.start()
        confirmations = []
        widget = BackgroundTasksWidget(WidgetContext(
            hub=hub, theme_manager=ThemeManager(store), instance_id="w",
            confirm=lambda action, summary, details="": confirmations.append(action) or True,
            toast=lambda m, l="info": None))
        widget.show()
        widget.activate()
        try:
            task = manager.create_task("Recherche", "x")
            assert wait_until(app, lambda: widget.list.count() == 1 and "En cours" in widget.list.item(0).text(), 3)
            assert widget.cancel_button.isVisibleTo(widget) and not widget.retry_button.isVisibleTo(widget)
            widget.list.setCurrentRow(0)
            widget._cancel()
            assert confirmations == ["task.cancel"]
            assert wait_until(app, lambda: "Annulée" in widget.list.item(0).text(), 3)
            widget._show_detail()
            assert widget.retry_button.isVisibleTo(widget) and not widget.cancel_button.isVisibleTo(widget)
            assert manager.get_task(task.id).status.value == "CANCELLED"
        finally:
            widget.dispose()
            hub.shutdown()
