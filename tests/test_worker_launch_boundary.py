"""Deterministic admission/stop/worker-creation interleavings."""
import threading
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication

from src.core.activity_boundary import ActivityBoundary
from src import protocols
from UI.dashboard.async_jobs import JobRunner


def delayed_gate(boundary):
    entered, release = threading.Event(), threading.Event()
    launch = boundary.launch
    def delayed(start):
        entered.set()
        assert release.wait(5)
        return launch(start)
    boundary.launch = delayed
    return entered, release


def test_protocol_admitted_before_stop_does_not_create_worker_after_stop(monkeypatch):
    boundary = ActivityBoundary()
    entered, release = delayed_gate(boundary)
    created, result = [], []
    class Worker(threading.Thread):
        def start(self):
            created.append(self)
            super().start()
    monkeypatch.setattr(protocols, "threading", SimpleNamespace(Thread=Worker, Event=threading.Event))
    services = SimpleNamespace(require=lambda name: SimpleNamespace(boundary=boundary))
    protocol = protocols.Protocol("gate", "gate", "gate", "gate", "done", ())
    launcher = threading.Thread(target=lambda: result.append(protocols.start_protocol(protocol, services=services)))
    launcher.start()
    try:
        assert entered.wait(2)  # admitted, but not at actual start yet
        assert boundary.snapshot()["active"] == ("protocol.run",)
        boundary.reject_new()
        release.set()
        launcher.join(3)
        assert not launcher.is_alive()
        assert result == [None] and created == []
        assert boundary.drain(1) and not boundary.snapshot()["active"]
    finally:
        release.set()
        launcher.join(3)


def test_dashboard_admitted_before_stop_does_not_create_worker_after_stop():
    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    boundary = ActivityBoundary()
    entered, release = delayed_gate(boundary)
    runner = JobRunner(activity_boundary=boundary)
    result, ran = [], threading.Event()
    launcher = threading.Thread(target=lambda: result.append(runner.submit(ran.set)))
    launcher.start()
    try:
        assert entered.wait(2)
        assert boundary.snapshot()["active"] == ("dashboard.submit",)
        boundary.reject_new()
        release.set()
        launcher.join(3)
        assert not launcher.is_alive()
        assert result == [0] and not ran.is_set()
        assert not runner._executor._threads
        assert boundary.drain(1) and not boundary.snapshot()["active"]
    finally:
        release.set()
        launcher.join(3)
        runner.shutdown()


def test_ui_worker_admitted_before_stop_is_not_started_and_reservation_is_released():
    boundary = ActivityBoundary()
    entered, release = delayed_gate(boundary)
    result, ran = [], threading.Event()
    launcher = threading.Thread(target=lambda: result.append(
        boundary.start_thread(ran.set, name="jarvis-catalogue-routine")))
    launcher.start()
    try:
        assert entered.wait(2)
        assert boundary.snapshot()["active"] == ("jarvis-catalogue-routine",)
        boundary.reject_new()
        release.set()
        launcher.join(3)
        assert result == [None] and not ran.is_set() and not launcher.is_alive()
        assert boundary.drain(1) and not boundary.snapshot()["active"]
    finally:
        release.set()
        launcher.join(3)
