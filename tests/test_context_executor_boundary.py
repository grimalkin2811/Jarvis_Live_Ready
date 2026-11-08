"""Context executor submission must not create a worker after stop."""
import asyncio
import concurrent.futures

import pytest

from src.core.activity_boundary import ActivityBoundary
from src.core.context import ContextEngine, ContextRequest
from src.core.services import ServiceRegistry


def test_async_context_refuses_before_executor_submission_after_stop():
    async def scenario():
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="context-proof")
        asyncio.get_running_loop().set_default_executor(executor)
        boundary = ActivityBoundary()
        engine = ContextEngine(ServiceRegistry(), activity_boundary=boundary)
        boundary.reject_new()
        with pytest.raises(RuntimeError, match="arrêt"):
            await engine.build_async(ContextRequest(conversation=False, runtime=False))
        assert not executor._threads
        assert engine._async_gate.acquire(blocking=False)
        engine._async_gate.release()
    asyncio.run(scenario())


def test_async_context_preserves_contextvars_in_worker():
    import contextvars
    current = contextvars.ContextVar("context-test", default="missing")
    class Conversation:
        def get_messages(self, limit):
            assert current.get() == "caller"
            return ()
    async def scenario():
        services = ServiceRegistry()
        services.register("conversation", Conversation())
        boundary = ActivityBoundary()
        engine = ContextEngine(services, activity_boundary=boundary)
        token = current.set("caller")
        try:
            snapshot = await engine.build_async(ContextRequest(runtime=False))
            assert snapshot.instructions == ()
        finally:
            current.reset(token)
    asyncio.run(scenario())


def test_context_executor_start_inflight_completes_before_stopping():
    import threading

    entered, release, worker_created, stop_done = (threading.Event() for _ in range(4))
    boundary = ActivityBoundary()
    created_phases, failures = [], []
    original_start = threading.Thread.start

    class Executor(concurrent.futures.ThreadPoolExecutor):
        def submit(self, fn, /, *args, **kwargs):
            entered.set()
            assert release.wait(5)
            return super().submit(fn, *args, **kwargs)
        def _adjust_thread_count(self):
            # Observe actual Thread.start without intercepting unrelated threads.
            original = threading.Thread.start
            def trace(thread):
                if thread.name.startswith("context-inflight"):
                    created_phases.append(boundary.phase())
                    original_start(thread)
                    worker_created.set()
                else:
                    original(thread)
            threading.Thread.start = trace
            try:
                super()._adjust_thread_count()
            finally:
                threading.Thread.start = original

    def caller():
        async def scenario():
            executor = Executor(max_workers=1, thread_name_prefix="context-inflight")
            asyncio.get_running_loop().set_default_executor(executor)
            engine = ContextEngine(ServiceRegistry(), activity_boundary=boundary)
            try:
                await engine.build_async(ContextRequest(conversation=False, runtime=False))
            except RuntimeError as exc:
                failures.append(str(exc))
        asyncio.run(scenario())

    caller_thread = threading.Thread(target=caller)
    stopper = threading.Thread(target=lambda: (boundary.reject_new(), stop_done.set()))
    caller_thread.start()
    try:
        assert entered.wait(3)
        stopper.start()
        assert boundary._stop_intent.wait(2)
        assert not stop_done.is_set() and boundary.phase() == "STOP_REQUESTED"
        release.set()
        assert worker_created.wait(3)
        caller_thread.join(5)
        stopper.join(5)
        assert not caller_thread.is_alive() and not stopper.is_alive()
        assert stop_done.is_set() and created_phases == ["STOP_REQUESTED"]
        assert failures and "arrêt" in failures[0]
    finally:
        release.set()
        caller_thread.join(5)
        if stopper.ident is not None:
            stopper.join(5)
