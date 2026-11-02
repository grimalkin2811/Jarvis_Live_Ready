"""Real scheduler/routine/protocol stores wired to one offline Core runtime."""
import asyncio
import datetime as dt
import threading
from dataclasses import replace

import pytest

from src.conversation import ConversationContext
from src.core import runtime as runtime_module
from src.core.runtime import CoreRuntime
from src.memory import MemoryManager
from src.protocols import Beat, Protocol, start_protocol
from src.routines import RoutineManager
from src.runtime_status import EngineState, RuntimeStatusBoard
from src.scheduler import Scheduler
from tests.test_core_runtime import config, factory_set
from tests.test_core_runtime_tool_bindings import Activity, Mode


def test_scheduled_routine_and_protocol_share_core_memory(tmp_path, monkeypatch):
    routine = RoutineManager(tmp_path / 'routines.json', include_presets=False)
    scheduler = Scheduler(tmp_path / 'schedule.db')
    context = ConversationContext()
    cfg = config()
    cfg.memory_database_path = str(tmp_path / 'memory.db')
    now = dt.datetime.now()
    schedule = f'tous les jours à {now.hour}h{now.minute:02}'
    assert routine.create_routine('demo', 'remember(content=violet)', schedule=schedule)['success']
    monkeypatch.setattr(runtime_module, '_default_modes', Mode)
    monkeypatch.setattr(runtime_module, '_default_activity', Activity)
    monkeypatch.setattr(runtime_module, '_default_routines', lambda: routine)
    monkeypatch.setattr('src.tools.get_default_memory_manager', lambda: pytest.fail('default memory used'))
    factories = replace(factory_set([]), memory=MemoryManager, conversation=lambda: context,
                        scheduler=lambda: scheduler)

    async def scenario():
        core = CoreRuntime(cfg, factories=factories, status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            assert scheduler._routines() is routine is core.services.require('routines')
            memory = core.services.require('memory')
            assert core.provider.kwargs['memory_manager'] is memory
            await asyncio.to_thread(scheduler._check_scheduled_routines)
            assert 'violet' in str(memory.list_memories())
            protocol = Protocol('test', 'test', 'test', 'test', 'done', (
                Beat('memory', 0, probe='memory_bank'),
                Beat('write', 0, tool='remember', args={'content': 'bleu'}),
            ))
            events = []
            run = start_protocol(protocol, events.append, fast=True, services=core.services)
            await asyncio.to_thread(run.join, 3)
            assert not run.running and run.result['success']
            assert 'bleu' in str(memory.list_memories())
            assert any('souvenir(s)' in event.get('detail', '') for event in events)
            assert [event['type'] for event in events][0] == 'start'
            assert events[-1]['type'] == 'done'
        finally:
            core.request_stop()
            await asyncio.wait_for(task, 8)
        assert scheduler._runtime_services is None
        assert core.status.snapshot()['engine_state'] == EngineState.STOPPED
    asyncio.run(scenario())


def test_scheduler_refuses_restart_after_runtime_stop_signal(tmp_path):
    scheduler = Scheduler(tmp_path / 'scheduler.db')
    scheduler.set_runtime_services(object())
    assert scheduler.start()
    scheduler.request_stop()
    scheduler.stop()
    assert not scheduler.start()
    scheduler.set_runtime_services(None)
    assert scheduler.start()
    scheduler.stop()


def test_timeout_automatically_cleans_after_last_worker_returns(monkeypatch):
    monkeypatch.setattr(runtime_module, '_default_modes', Mode)
    monkeypatch.setattr(runtime_module, '_default_activity', Activity)
    entered, release, cleanup = threading.Event(), threading.Event(), threading.Event()
    closed = []

    class SlowMemory:
        enabled = available = True
        def __init__(self, *args, **kwargs):
            pass
        def add_memory(self, *args, **kwargs):
            entered.set()
            release.wait(8)
            raise ValueError('expected worker failure')

    def worker(fn):
        try:
            fn(content='wait')
        except ValueError:
            cleanup.set()

    async def scenario():
        core = CoreRuntime(config(), factories=replace(factory_set(closed), memory=SlowMemory),
                           status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        thread = None
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            fn = core.provider.kwargs['tool_functions']['remember']
            thread = threading.Thread(target=worker, args=(fn,))
            thread.start()
            assert await asyncio.to_thread(entered.wait, 2)
            core.request_stop()
            await asyncio.wait_for(task, 7)
            assert core.status.snapshot()['engine_state'] == EngineState.FAILED
            assert not closed and CoreRuntime._owner is core
            release.set()
            assert await asyncio.to_thread(cleanup.wait, 3)
            thread.join(3)
            # The waiter performs cleanup automatically; do not manually retry.
            for _ in range(100):
                if CoreRuntime._owner is None:
                    break
                await asyncio.sleep(0.01)
            assert CoreRuntime._owner is None
            assert closed == ['tasks', 'scheduler']
            assert core.complete_deferred_cleanup()
            assert closed == ['tasks', 'scheduler']
        finally:
            release.set()
            if thread:
                thread.join(3)
            if not task.done():
                core.request_stop()
                await asyncio.wait_for(task, 7)
            core.complete_deferred_cleanup()
    asyncio.run(scenario())
