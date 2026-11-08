"""Observable cleanup states never infer termination from a stop request."""
import threading

from src.core.services import ServiceRegistry


def test_cleanup_pending_running_done_and_repeated():
    registry = ServiceRegistry()
    entered, release = threading.Event(), threading.Event()
    results = []
    def close():
        entered.set()
        assert release.wait(5)
    registry.register("resource", object(), close=close)
    assert registry.cleanup_status() == {"state": "CLEANUP_PENDING", "failed": ()}
    thread = threading.Thread(target=lambda: results.append(registry.close()))
    thread.start()
    try:
        assert entered.wait(2)
        assert registry.cleanup_status() == {"state": "CLEANUP_RUNNING", "failed": ()}
        assert registry.close() == ["cleanup_in_progress"]
        release.set()
        thread.join(3)
        assert not thread.is_alive() and results == [[]]
        assert registry.cleanup_status() == {"state": "CLEANUP_DONE", "failed": ()}
        assert registry.close() == []
    finally:
        release.set()
        thread.join(3)


def test_cleanup_failure_is_observable_and_not_retried():
    registry = ServiceRegistry()
    calls = []
    def fail():
        calls.append(1)
        raise RuntimeError("cleanup failed")
    registry.register("resource", object(), close=fail)
    assert registry.close() == ["resource"]
    assert registry.cleanup_status() == {"state": "CLEANUP_FAILED", "failed": ("resource",)}
    assert registry.close() == ["resource"] and calls == [1]


def test_provider_cleanup_running_done_and_registry_are_separate():
    import asyncio
    from src.core.runtime import CoreRuntime
    from src.runtime_status import RuntimeStatusBoard
    from tests.test_core_runtime import Provider, config, factory_set

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        class ProviderWithSlowShutdown(Provider):
            async def shutdown(self):
                entered.set()
                await release.wait()
        core = CoreRuntime(config(), factories=factory_set([], provider=ProviderWithSlowShutdown),
                           status=RuntimeStatusBoard())
        assert core.cleanup_status()["provider"] == "CLEANUP_PENDING"
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            core.request_stop()
            await asyncio.wait_for(entered.wait(), 3)
            assert core.cleanup_status()["provider"] == "CLEANUP_RUNNING"
            assert core.cleanup_status()["registry"]["state"] == "CLEANUP_PENDING"
            assert CoreRuntime._owner is core and not task.done()
            release.set()
            await asyncio.wait_for(task, 5)
            assert core.cleanup_status()["provider"] == "CLEANUP_DONE"
            assert core.cleanup_status()["registry"]["state"] == "CLEANUP_DONE"
        finally:
            release.set()
            if not task.done():
                core.request_stop()
                await asyncio.wait_for(task, 8)
    asyncio.run(scenario())


def test_provider_failure_is_visible_after_cleanup():
    import asyncio
    from src.core.runtime import CoreRuntime
    from src.runtime_status import RuntimeStatusBoard
    from tests.test_core_runtime import Provider, config, factory_set

    class FailedProvider(Provider):
        async def shutdown(self):
            raise RuntimeError("provider shutdown failed")
    async def scenario():
        core = CoreRuntime(config(), factories=factory_set([], provider=FailedProvider),
                           status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            core.request_stop()
            await asyncio.wait_for(task, 5)
            assert core.cleanup_status()["provider"] == "CLEANUP_FAILED"
            assert core.cleanup_status()["registry"]["state"] == "CLEANUP_DONE"
            assert not core.complete_deferred_cleanup()
        finally:
            if not task.done():
                core.request_stop()
                await asyncio.wait_for(task, 8)
    asyncio.run(scenario())


def test_cleanup_interruption_is_reported_as_failure_and_propagated():
    import pytest
    registry = ServiceRegistry()
    def interrupt():
        raise KeyboardInterrupt()
    registry.register("resource", object(), close=interrupt)
    with pytest.raises(KeyboardInterrupt):
        registry.close()
    assert registry.cleanup_status() == {"state": "CLEANUP_FAILED", "failed": ("resource",)}
    assert registry.close() == ["resource"]
