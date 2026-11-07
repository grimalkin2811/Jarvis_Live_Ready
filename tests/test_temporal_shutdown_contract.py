"""Force shutdown interleavings against real Python worker creation."""
import threading

import pytest

from src.core.activity_boundary import ActivityBoundary


def test_stop_intent_rejects_new_admission_while_prior_start_finishes():
    boundary = ActivityBoundary()
    entered, release, stop_done = threading.Event(), threading.Event(), threading.Event()
    worker_entered, worker_release = threading.Event(), threading.Event()
    events, workers = [], []
    lock = threading.Lock()
    def record(name):
        with lock:
            events.append(name)
    def worker():
        record("callback")
        worker_entered.set()
        assert worker_release.wait(5)
    def start():
        record("start_entered")
        entered.set()
        assert release.wait(5)
        thread = threading.Thread(target=worker, name="temporal-proof")
        workers.append(thread)
        thread.start()
        record("thread_started")
    def launch():
        with boundary.activity("accepted"):
            assert boundary.launch(start)
    def shutdown():
        boundary.reject_new()
        record("stopping")
        stop_done.set()
    submitter, stopper = threading.Thread(target=launch), threading.Thread(target=shutdown)
    submitter.start()
    try:
        assert entered.wait(2)
        stopper.start()
        assert boundary._stop_intent.wait(2)
        record("stop_requested")
        assert boundary.phase() == "STOP_REQUESTED" and not stop_done.is_set()
        with pytest.raises(RuntimeError, match="arrêt"):
            with boundary.activity("late"):
                pytest.fail("late activity admitted")
        assert not boundary.launch(lambda: pytest.fail("late thread created"))
        release.set()
        submitter.join(3)
        stopper.join(3)
        assert not submitter.is_alive() and not stopper.is_alive()
        assert stop_done.is_set() and boundary.phase() == "STOPPING"
        assert worker_entered.wait(2) and len(workers) == 1 and workers[0].is_alive()
        assert events.index("stop_requested") < events.index("thread_started") < events.index("stopping")
        assert not boundary.launch(lambda: pytest.fail("thread created during STOPPING"))
        worker_release.set()
        workers[0].join(3)
        assert not workers[0].is_alive() and boundary.drain(1)
    finally:
        release.set()
        worker_release.set()
        submitter.join(3)
        if stopper.ident is not None:
            stopper.join(3)
        for thread in workers:
            thread.join(3)


def test_hub_nonblocking_request_does_not_wait_for_inflight_executor_submit():
    from PySide6.QtCore import QCoreApplication
    from UI.dashboard.config import ConfigStore
    from UI.dashboard.hub import DashboardHub
    from src.runtime_status import RuntimeStatusBoard

    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    hub = DashboardHub(ConfigStore(None), runtime=RuntimeStatusBoard())
    entered, release, ran = threading.Event(), threading.Event(), threading.Event()
    errors, result = [], []
    real_submit = hub.jobs._executor.submit
    def paused_submit(fn):
        entered.set()
        if not release.wait(5):
            raise AssertionError("submission not released")
        return real_submit(fn)
    hub.jobs._executor.submit = paused_submit
    def submitter():
        try:
            result.append(hub.jobs.submit(ran.set))
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=submitter)
    thread.start()
    try:
        assert entered.wait(2)
        hub.shutdown(False)
        assert not release.is_set() and not hub.jobs.shutdown_requested()
        assert not hub.shutdown_status()["complete"]
        assert hub.jobs.submit(lambda: pytest.fail("late submit")) == 0
        release.set()
        thread.join(3)
        assert not thread.is_alive() and not errors and result[0]
        assert ran.wait(2) and hub.jobs.shutdown_requested()
        hub.shutdown(True, timeout=3)
        workers = tuple(hub.jobs._executor._threads)
        assert workers and all(not worker.is_alive() for worker in workers)
        assert hub.shutdown_status()["complete"]
    finally:
        release.set()
        thread.join(5)
        hub.shutdown(True, timeout=3)


def test_runtime_bound_task_manager_cannot_start_after_stop_intent(tmp_path):
    from src.background_tasks.manager import TaskManager

    boundary = ActivityBoundary()
    manager = TaskManager("fake", storage_path=tmp_path / "tasks.json", auto_start=False,
                          client_factory=lambda: object())
    manager.bind_runtime()
    manager.set_runtime_start_gate(boundary.launch)
    boundary.begin_stop()
    with pytest.raises(RuntimeError, match="arrêt"):
        manager.start()
    assert manager._thread is None
    boundary.reject_new()
    manager.close(wait=True)


def test_task_manager_stop_between_start_and_actual_thread_creation(tmp_path):
    from src.background_tasks.manager import TaskManager

    boundary = ActivityBoundary()
    manager = TaskManager("fake", storage_path=tmp_path / "tasks.json", auto_start=False,
                          client_factory=lambda: object())
    entered, release = threading.Event(), threading.Event()
    errors = []
    def gate(start):
        entered.set()
        assert release.wait(5)
        return boundary.launch(start)
    manager.bind_runtime()
    manager.set_runtime_start_gate(gate)
    def start_manager():
        try:
            manager.start()
        except RuntimeError as exc:
            errors.append(str(exc))
    starter = threading.Thread(target=start_manager)
    starter.start()
    try:
        assert entered.wait(2)
        pending_worker = manager._thread
        assert pending_worker is not None and pending_worker.ident is None
        boundary.reject_new()
        release.set()
        starter.join(3)
        assert not starter.is_alive() and errors and "arrêt" in errors[0]
        assert pending_worker.ident is None and not pending_worker.is_alive()
        assert manager._thread is None
    finally:
        release.set()
        starter.join(3)
        manager.close(wait=True)


def test_custom_scheduler_factory_can_create_worker_after_core_stopping():
    """Characterize an UNSUPPORTED factory, not a successful boundary proof."""
    import asyncio
    from dataclasses import replace
    from src.core.runtime import CoreRuntime
    from src.runtime_status import RuntimeStatusBoard
    from tests.test_core_runtime import config, factory_set

    entered, resume, created, worker_exit, stopped = (threading.Event() for _ in range(5))
    observations, errors = [], []
    class ExternalScheduler:
        def __init__(self):
            self._thread = threading.Thread(target=worker_exit.wait, name="factory-worker")
        def stop(self):
            worker_exit.set()
            self._thread.join(3)
            stopped.set()
    scheduler = ExternalScheduler()
    core = None
    def factory():
        entered.set()
        assert resume.wait(5)
        observations.append(core.activity_boundary.phase())
        scheduler._thread.start()
        created.set()
        return scheduler
    core = CoreRuntime(config(), factories=replace(factory_set([]), scheduler=factory), status=RuntimeStatusBoard())
    def run():
        try:
            asyncio.run(core.run())
        except BaseException as exc:
            errors.append(exc)
    runtime_thread = threading.Thread(target=run)
    runtime_thread.start()
    try:
        assert entered.wait(3)
        core.request_stop()
        assert core.activity_boundary.phase() == "STOPPING"
        resume.set()
        assert created.wait(3)
        assert observations == ["STOPPING"] and scheduler._thread.ident is not None
        runtime_thread.join(8)
        assert not runtime_thread.is_alive() and scheduler._thread.is_alive() and not errors
        assert core._deferred_cleanup and CoreRuntime._owner is core
        worker_exit.set()
        scheduler._thread.join(3)
        assert stopped.wait(3) and core.complete_deferred_cleanup()
    finally:
        resume.set()
        worker_exit.set()
        runtime_thread.join(8)
        if scheduler._thread.ident is not None:
            scheduler._thread.join(3)


def test_runner_deferred_stop_survives_intent_between_check_and_unlock():
    from PySide6.QtCore import QCoreApplication
    from UI.dashboard.async_jobs import JobRunner

    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    runner = JobRunner()
    holder_entered, leave_body, check_entered, leave_check = (threading.Event() for _ in range(4))
    real_event = runner._stop_intent
    class PausedCheck:
        def set(self):
            real_event.set()
        def is_set(self):
            value = real_event.is_set()
            if threading.current_thread() is holder and not value and leave_body.is_set():
                check_entered.set()
                assert leave_check.wait(5)
                return value
            return value
    runner._stop_intent = PausedCheck()
    def hold():
        with runner._guard():
            holder_entered.set()
            assert leave_body.wait(5)
    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert holder_entered.wait(2)
        leave_body.set()
        assert check_entered.wait(2)
        runner.request_shutdown()
        assert not runner.shutdown_requested()
        leave_check.set()
        holder.join(3)
        assert not holder.is_alive() and runner.shutdown_requested()
        assert runner.shutdown_complete() and runner.submit(lambda: None) == 0
    finally:
        leave_body.set()
        leave_check.set()
        holder.join(3)
        runner.shutdown()
