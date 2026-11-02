"""Concurrent tracked calls finish before Core releases its services."""
import asyncio
import threading
from dataclasses import replace

from src.core import runtime as runtime_module
from src.core.runtime import CoreRuntime
from src.runtime_status import EngineState, RuntimeStatusBoard
from tests.test_core_runtime import config, factory_set
from tests.test_core_runtime_tool_bindings import Activity, Mode


def test_multiple_workers_one_fails_and_async_loop_keeps_running(monkeypatch):
    monkeypatch.setattr(runtime_module, '_default_modes', Mode)
    monkeypatch.setattr(runtime_module, '_default_activity', Activity)
    entered = threading.Barrier(3)
    release = threading.Event()
    closed = []
    results = []

    class Memory:
        enabled = available = True
        def __init__(self, *args, **kwargs):
            pass
        def add_memory(self, content, **kwargs):
            entered.wait(3)
            release.wait(4)
            if content == 'fail':
                raise RuntimeError('expected')
            return {'success': True}

    def work(fn, value):
        try:
            results.append(fn(content=value))
        except RuntimeError as exc:
            results.append(str(exc))

    async def scenario():
        core = CoreRuntime(config(), factories=replace(factory_set(closed), memory=Memory),
                           status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        workers = []
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            fn = core.provider.kwargs['tool_functions']['remember']
            workers = [threading.Thread(target=work, args=(fn, value)) for value in ('ok', 'fail')]
            for worker in workers:
                worker.start()
            await asyncio.to_thread(entered.wait, 3)
            core.request_stop()
            core.request_stop()
            await asyncio.sleep(0.05)  # draining does not block the event loop
            assert not task.done() and not closed
            assert core.services.require('memory') is core.provider.kwargs['memory_manager']
            release.set()
            await asyncio.wait_for(task, 5)
            for worker in workers:
                worker.join(2)
                assert not worker.is_alive()
            assert {'success': True} in results and 'expected' in results
            assert closed == ['tasks', 'scheduler']
            assert core.status.snapshot()['engine_state'] == EngineState.STOPPED
        finally:
            release.set()
            for worker in workers:
                worker.join(3)
            if not task.done():
                core.request_stop()
                await asyncio.wait_for(task, 8)
            core.complete_deferred_cleanup()
    asyncio.run(scenario())
