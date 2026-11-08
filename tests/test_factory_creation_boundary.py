"""Construction admission and an explicit external-delegation counterexample."""
import asyncio
import threading
from dataclasses import replace

import pytest

from src.core.activity_boundary import ActivityBoundary
from src.core.runtime import CoreRuntime
from src.runtime_status import EngineState, RuntimeStatusBoard
from tests.test_core_runtime import config, factory_set


def test_multiple_admitted_factories_delay_stopping_and_late_factory_is_refused():
    boundary = ActivityBoundary()
    entered = [threading.Event(), threading.Event()]
    release = [threading.Event(), threading.Event()]
    completed = [threading.Event(), threading.Event()]
    stop_done = threading.Event()
    results = []

    def construct(index):
        entered[index].set()
        assert release[index].wait(5)
        completed[index].set()
        return index

    threads = [threading.Thread(target=lambda i=i: results.append(boundary.create(lambda: construct(i))))
               for i in range(2)]
    stopper = threading.Thread(target=lambda: (boundary.reject_new(), stop_done.set()))
    for thread in threads:
        thread.start()
    try:
        assert all(event.wait(2) for event in entered)
        stopper.start()
        assert boundary._stop_intent.wait(2)
        assert boundary.phase() == "STOP_REQUESTED"
        assert boundary.create(lambda: pytest.fail("late factory invoked")) == (False, None)
        release[0].set()
        assert completed[0].wait(2)
        threads[0].join(2)
        assert not stop_done.is_set() and boundary.phase() == "STOP_REQUESTED"
        release[1].set()
        assert completed[1].wait(2)
        for thread in threads:
            thread.join(2)
        stopper.join(2)
        assert stop_done.is_set() and boundary.phase() == "STOPPING"
        assert sorted(results) == [(True, 0), (True, 1)]
        assert boundary.create(lambda: pytest.fail("factory after stop")) == (False, None)
    finally:
        for event in release:
            event.set()
        for thread in threads:
            thread.join(3)
        if stopper.ident is not None:
            stopper.join(3)


def test_factory_failure_releases_creation_admission():
    boundary = ActivityBoundary()
    def fail():
        raise ValueError("factory failed")
    with pytest.raises(ValueError, match="factory failed"):
        boundary.create(fail)
    boundary.reject_new()
    assert boundary.phase() == "STOPPING"


def test_external_factory_can_delegate_post_stop_creation_to_untracked_thread():
    """Demonstrate the remaining unsupported case, not a guarantee of safety."""
    release, dispatcher_started, created, worked = (threading.Event() for _ in range(4))
    created_workers, phases = [], []
    core = None

    def dispatch():
        dispatcher_started.set()
        if not release.wait(8):
            return
        phases.append(core.activity_boundary.phase())
        worker = threading.Thread(target=worked.set, name="external-late-worker")
        created_workers.append(worker)
        worker.start()
        created.set()

    dispatcher = threading.Thread(target=dispatch, name="external-factory-dispatcher")
    factories = factory_set([])
    original_scheduler = factories.scheduler
    def custom_factory():
        dispatcher.start()
        return original_scheduler()

    async def scenario():
        nonlocal core
        core = CoreRuntime(config(), factories=replace(factories, scheduler=custom_factory),
                           status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            assert dispatcher_started.is_set()
            core.request_stop()
            await asyncio.wait_for(task, 5)
            assert core.activity_boundary.phase() == "STOPPING"
            assert core.status.snapshot()["engine_state"] is EngineState.STOPPED
            assert CoreRuntime._owner is None
            assert not created.is_set()
            release.set()
            assert await asyncio.to_thread(created.wait, 3)
            assert phases == ["STOPPING"]
            assert created_workers[0].ident is not None and worked.wait(2)
        finally:
            release.set()
            if not task.done():
                core.request_stop()
                await asyncio.wait_for(task, 8)
            dispatcher.join(3)
            for worker in created_workers:
                worker.join(3)
    asyncio.run(scenario())
    assert not dispatcher.is_alive()
    assert created_workers and all(not worker.is_alive() for worker in created_workers)


def test_runtime_cleanup_can_start_asyncio_executor_worker_after_stopping(monkeypatch):
    """Characterize cleanup infrastructure outside the admission gate."""
    import concurrent.futures

    observed = []
    original_start = threading.Thread.start
    core = CoreRuntime(config(), factories=factory_set([]), status=RuntimeStatusBoard())
    def traced_start(thread):
        if thread.name.startswith("cleanup-observation"):
            observed.append((thread, core.activity_boundary.phase()))
        return original_start(thread)
    monkeypatch.setattr(threading.Thread, "start", traced_start)

    async def scenario():
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="cleanup-observation")
        asyncio.get_running_loop().set_default_executor(executor)
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            core.request_stop()
            await asyncio.wait_for(task, 5)
            assert observed and observed[0][1] == "STOPPING"
            assert observed[0][0].ident is not None
        finally:
            if not task.done():
                core.request_stop()
                await asyncio.wait_for(task, 8)
    asyncio.run(scenario())
    assert all(not thread.is_alive() for thread, _phase in observed)
