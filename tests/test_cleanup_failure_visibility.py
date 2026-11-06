"""Failed and in-progress cleanup must not be reported as successful."""
import asyncio
import threading

from src.core.services import ServiceRegistry
from src.core.runtime import CoreRuntime
from src.runtime_status import EngineState, RuntimeStatusBoard
from tests.test_core_runtime import Audio, config, factory_set


def test_registry_failed_callback_is_not_retried_or_reported_as_success():
    registry = ServiceRegistry()
    calls = []
    def fail():
        calls.append("close")
        raise RuntimeError("close failed")
    registry.register("resource", object(), close=fail)
    assert registry.close() == ["resource"]
    assert registry.close() == ["resource"]
    assert calls == ["close"]


def test_registry_in_progress_is_visible_to_a_concurrent_close():
    registry = ServiceRegistry()
    entered, release = threading.Event(), threading.Event()
    results = []
    def blocked():
        entered.set()
        assert release.wait(5)
    registry.register("resource", object(), close=blocked)
    thread = threading.Thread(target=lambda: results.append(registry.close()))
    thread.start()
    try:
        assert entered.wait(2)
        assert registry.close() == ["cleanup_in_progress"]
        release.set()
        thread.join(3)
        assert not thread.is_alive() and results == [[]]
        assert registry.close() == []
    finally:
        release.set()
        thread.join(3)


def test_runtime_does_not_report_cleanup_complete_after_callback_failure():
    class BrokenStop(Audio):
        def stop(self):
            raise RuntimeError("cannot stop")
    async def run():
        core = CoreRuntime(config(), factories=factory_set([], audio=BrokenStop), status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        while core.provider is None:
            await asyncio.sleep(0)
        await core.provider.ready.wait()
        core.request_stop()
        await asyncio.wait_for(task, 5)
        assert core.status.snapshot()["engine_state"] is EngineState.FAILED
        assert not core.complete_deferred_cleanup()
        assert not core.complete_deferred_cleanup()
        assert core.services.close() == ["audio"]
    asyncio.run(run())
