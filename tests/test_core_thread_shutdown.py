"""Bounded joins must not be reported as successful cleanup while alive."""
import asyncio
import threading
from dataclasses import replace

from src.core.runtime import CoreRuntime
from src.runtime_status import EngineState, RuntimeStatusBoard
from src.scheduler import Scheduler
from tests.test_core_runtime import Resource, config, factory_set


class StuckThread:
    def __init__(self):
        self.alive = True
        self.done = threading.Event()
    def is_alive(self):
        return self.alive
    def join(self, timeout=None):
        if timeout is None:
            self.done.wait(5)
    def release(self):
        self.alive = False
        self.done.set()


def test_runtime_detects_worker_still_alive_after_stop():
    class StuckScheduler(Resource):
        def __init__(self, closed):
            super().__init__("scheduler", closed)
            self._thread = StuckThread()
        def stop(self):
            super().stop()
            self._thread = None  # captures workers even if a service drops its reference

    async def scenario():
        closed = []
        factories = replace(factory_set(closed), scheduler=lambda: StuckScheduler(closed))
        runtime = CoreRuntime(config(), factories=factories, status=RuntimeStatusBoard())
        task = asyncio.create_task(runtime.run())
        while runtime.provider is None:
            await asyncio.sleep(0)
        await runtime.provider.ready.wait()
        runtime.request_stop()
        await task
        assert closed == []  # workers may still be using these resources
        assert runtime.status.snapshot()["engine_state"] == EngineState.FAILED
        assert "scheduler" in runtime.status.snapshot()["last_error"]
        assert CoreRuntime._owner is runtime
        runtime._scheduler._thread.release()
        assert await asyncio.to_thread(runtime.complete_deferred_cleanup)
        assert closed == ["tasks", "scheduler"]
        assert CoreRuntime._owner is None
    asyncio.run(scenario())


def test_scheduler_does_not_spawn_another_worker_after_join_timeout():
    scheduler = Scheduler.__new__(Scheduler)
    scheduler._lock = threading.RLock()
    scheduler._stop = threading.Event()
    worker = StuckThread()
    scheduler._thread = worker
    scheduler._ready = lambda: True
    scheduler.stop()
    assert scheduler._thread is worker
    assert scheduler.start() is False
    assert scheduler._stop.is_set()
    worker.alive = False
    scheduler.stop()
    assert scheduler._thread is None
