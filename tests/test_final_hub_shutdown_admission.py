"""Real executor admission and shutdown interleavings for the dashboard Hub."""
import threading

import pytest
from PySide6.QtCore import QCoreApplication

from UI.dashboard.async_jobs import JobRunner


def test_shutdown_is_serialized_with_executor_submission():
    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    runner = JobRunner()
    entered, release = threading.Event(), threading.Event()
    shutdown_started, shutdown_done = threading.Event(), threading.Event()
    performed, results, errors = threading.Event(), [], []
    submit = runner._executor.submit

    def delayed_submit(fn):
        entered.set()
        if not release.wait(5):
            raise AssertionError("submission was not released")
        return submit(fn)

    runner._executor.submit = delayed_submit

    def run_submission():
        try:
            results.append(runner.submit(performed.set))
        except BaseException as exc:
            errors.append(exc)

    def close():
        shutdown_started.set()
        try:
            runner.shutdown()
        finally:
            shutdown_done.set()

    submitter = threading.Thread(target=run_submission)
    closer = threading.Thread(target=close)
    submitter.start()
    try:
        assert entered.wait(2)
        closer.start()
        assert shutdown_started.wait(2)
        # Hub shutdown must not finish while an admitted submit has yet to
        # create its worker; no new submit may get through once closed.
        assert not shutdown_done.wait(.1)
        release.set()
        submitter.join(3)
        closer.join(3)
        assert not submitter.is_alive() and not closer.is_alive()
        assert not errors and results[0] != 0
        assert shutdown_done.is_set()
        assert runner.submit(lambda: None) == 0
        assert runner._executor._threads
        for worker in tuple(runner._executor._threads):
            worker.join(3)
            assert not worker.is_alive()
        runner.shutdown()
    finally:
        release.set()
        submitter.join(3)
        if closer.ident is not None:
            closer.join(3)
        runner.shutdown()


def test_executor_error_clears_callback_and_does_not_spawn_worker():
    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    runner = JobRunner()
    def fail(_fn):
        raise ValueError("submit failed")
    runner._executor.submit = fail
    try:
        with pytest.raises(ValueError, match="submit failed"):
            runner.submit(lambda: None, on_done=lambda _value: None)
        assert runner.pending() == 0
        assert not runner._executor._threads
    finally:
        runner.shutdown()
        runner.shutdown()


@pytest.mark.parametrize("started", [False, True])
def test_stopped_hub_cannot_restart_jobs_or_resource_sampler(started):
    from UI.dashboard.config import ConfigStore
    from UI.dashboard.hub import DashboardHub
    from UI.dashboard.resources import ResourceMonitor
    from src.runtime_status import RuntimeStatusBoard

    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    store = ConfigStore(None)
    store.config.monitoring.enabled = True
    monitor = ResourceMonitor(interval=0.5)
    hub = DashboardHub(store, runtime=RuntimeStatusBoard(), resource_monitor=monitor)
    try:
        if started:
            hub.start()
            assert hub.started
        hub.shutdown()
        hub.shutdown()
        hub.start()
        assert not hub.started
        assert hub.subscribe_resources("late") is False
        hub.apply_monitoring_prefs()
        assert hub.resource_consumers() == 0
        assert not monitor.running()
        assert hub.jobs.submit(lambda: None) == 0
        assert not hub.jobs._executor._threads
    finally:
        hub.shutdown()
        monitor.stop()
