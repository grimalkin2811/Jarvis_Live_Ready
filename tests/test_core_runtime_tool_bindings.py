"""Offline integration of injected tools, real stores and runtime lifecycle."""
import asyncio
import inspect
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.conversation import ConversationContext
from src.core import runtime as runtime_module
from src.core.runtime import CoreRuntime
from src.core.services import ServiceRegistry
from src.memory import MemoryManager
from src.runtime_status import RuntimeStatusBoard, EngineState
from src.scheduler import Scheduler
from src.todo import TodoManager
from src.tools import RuntimeToolBindings
from tests.test_core_runtime import config, factory_set


class Mode:
    def block_for_tool(self, name, arguments):
        return None

    def system_instruction(self):
        return ""

    def should_suppress_notifications(self):
        return False


class Activity:
    def __init__(self):
        self.events = []

    def log(self, name, **kwargs):
        self.events.append((name, kwargs))


def test_running_core_tools_share_actual_stores_and_do_not_fall_back(tmp_path, monkeypatch):
    todo = TodoManager(tmp_path / 'todos.db')
    mode, activity = Mode(), Activity()
    monkeypatch.setattr(runtime_module, '_default_todo', lambda: todo)
    monkeypatch.setattr(runtime_module, '_default_modes', lambda: mode)
    monkeypatch.setattr(runtime_module, '_default_activity', lambda: activity)
    conversation = ConversationContext()
    scheduler = Scheduler(tmp_path / 'reminders.db')
    cfg = config()
    cfg.memory_database_path = str(tmp_path / 'memories.db')
    factories = replace(factory_set([]), memory=MemoryManager, conversation=lambda: conversation,
                        scheduler=lambda: scheduler)

    async def scenario():
        runtime = CoreRuntime(cfg, factories=factories, status=RuntimeStatusBoard())
        events = []
        runtime.events.subscribe(lambda event: events.append(event.kind))
        task = asyncio.create_task(runtime.run())
        try:
            while runtime.provider is None:
                await asyncio.sleep(0)
            await runtime.provider.ready.wait()
            tools = runtime.provider.kwargs['tool_functions']
            assert runtime.provider.kwargs['services'] is runtime.services
            assert runtime.provider.kwargs['memory_manager'] is runtime.services.require('memory')
            assert runtime.provider.kwargs['conversation'] is conversation
            assert tools['remember'](content='Ma couleur est indigo')['success']
            assert 'indigo' in str(runtime.services.require('memory').list_memories())
            assert tools['add_todo'](text='tester injection')['success']
            assert 'tester injection' in str(todo.list_tasks())
            assert tools['set_reminder'](text='tester rappel', when='dans 20 minutes')['success']
            assert 'tester rappel' in str(scheduler.list_reminders())
            conversation.add_user_message('Bonjour')
            assert tools['get_conversation_state']()['messages'] == conversation.snapshot()['messages']
            assert tools['get_background_quota']()['success'] is False  # fake task resource has no quota API
            assert tools['remember'](content='interdit', _services=object())['success'] is False
            assert '_services' not in inspect.signature(tools['remember']).parameters
            assert activity.events and 'connected' in events
        finally:
            runtime.request_stop()
            await asyncio.wait_for(task, 8)
        with pytest.raises(RuntimeError):
            runtime.services.require('memory')
        assert not tools['list_memories']()['success']
    asyncio.run(scenario())


def test_injected_missing_service_never_constructs_legacy_manager(monkeypatch):
    services = ServiceRegistry()
    services.register('modes', Mode())
    services.register('activity', Activity())
    monkeypatch.setattr('src.tools.get_default_memory_manager', lambda: pytest.fail('legacy getter called'))
    tools = RuntimeToolBindings(services).functions
    with pytest.raises(LookupError, match='memory'):
        tools['remember'](content='test')
    services.close()


def test_routine_invoked_by_tool_uses_runtime_bound_tools(tmp_path, monkeypatch):
    from src.routines import RoutineManager
    services = ServiceRegistry()
    services.register('modes', Mode())
    services.register('activity', Activity())
    memory = MemoryManager(tmp_path / 'memory.db')
    services.register('memory', memory)
    routine = RoutineManager(tmp_path / 'routines.json', include_presets=False)
    services.register('routines', routine)
    bindings = RuntimeToolBindings(services)
    services.register('tools', bindings)
    monkeypatch.setattr('src.tools.get_default_memory_manager', lambda: pytest.fail('legacy getter called'))
    assert routine.create_routine('demo', 'remember(content=indigo)')['success']
    result = bindings.functions['run_routine'](name='demo')
    assert result['success'], result
    assert 'indigo' in str(memory.list_memories())
    assert bindings.drain(0)
    services.close()


def test_simultaneous_invocations_resolve_lazy_service_once():
    services = ServiceRegistry()
    services.register('modes', Mode())
    services.register('activity', Activity())
    barrier = threading.Barrier(5)
    count = []

    class Memory:
        def add_memory(self, content, **kwargs):
            return {'success': True, 'content': content}
    memory = Memory()

    def factory():
        count.append(1)
        return memory
    services.register_lazy('memory', factory)
    bindings = RuntimeToolBindings(services)
    results = []

    def worker(i):
        barrier.wait()
        results.append(bindings.functions['remember'](content=str(i)))
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=3)
        assert not thread.is_alive()
    assert len(results) == 4 and all(result['success'] for result in results)
    assert count == [1] and services.require('memory') is memory
    assert bindings.drain(0)
    services.close()


def test_stop_during_tool_keeps_resources_and_owner_until_worker_finishes(monkeypatch):
    monkeypatch.setattr(runtime_module, '_default_modes', Mode)
    monkeypatch.setattr(runtime_module, '_default_activity', Activity)
    entered, release = threading.Event(), threading.Event()
    closed = []

    class SlowMemory:
        enabled = available = True
        def __init__(self, *args, **kwargs):
            pass
        def add_memory(self, *args, **kwargs):
            entered.set()
            assert release.wait(8)
            return {'success': True}

    async def scenario():
        runtime = CoreRuntime(config(), factories=replace(factory_set(closed), memory=SlowMemory),
                              status=RuntimeStatusBoard())
        task = asyncio.create_task(runtime.run())
        worker = None
        try:
            while runtime.provider is None:
                await asyncio.sleep(0)
            await runtime.provider.ready.wait()
            tools = runtime.provider.kwargs['tool_functions']
            worker = threading.Thread(target=lambda: tools['remember'](content='wait'))
            worker.start()
            assert await asyncio.to_thread(entered.wait, 2)
            runtime.request_stop()
            assert not tools['list_memories']()['success']
            await asyncio.wait_for(task, 6)
            assert runtime.status.snapshot()['engine_state'] == EngineState.FAILED
            assert not closed
            assert runtime.services.require('memory') is runtime.provider.kwargs['memory_manager']
            assert CoreRuntime._owner is runtime
            assert runtime.complete_deferred_cleanup() is False
        finally:
            release.set()
            if worker is not None:
                worker.join(timeout=3)
                assert not worker.is_alive()
            if not task.done():
                runtime.request_stop()
                await asyncio.wait_for(task, 6)
            assert runtime.complete_deferred_cleanup()
        assert closed == ['tasks', 'scheduler']
        assert CoreRuntime._owner is None
        with pytest.raises(RuntimeError):
            runtime.services.require('memory')
    asyncio.run(scenario())


def test_legacy_fixed_signature_provider_factory_remains_supported():
    from tests.test_core_runtime import Provider

    def old_factory(key, model, user, *, on_audio, on_turn_complete, on_interrupted,
                    on_turn_open, on_turn_resolved, on_speaking, on_thinking,
                    on_tool_start, on_tool_end, on_user_transcript,
                    on_assistant_transcript, memory_manager, conversation):
        return Provider(key, model, user)

    async def scenario():
        core = CoreRuntime(config(), factories=factory_set([], provider=old_factory),
                           status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        while core.provider is None:
            await asyncio.sleep(0)
        await core.provider.ready.wait()
        core.request_stop()
        await asyncio.wait_for(task, 5)
    asyncio.run(scenario())


def test_real_gemini_uses_injected_dispatch_for_model_and_dashboard_fallback():
    from src.gemini_live import GeminiLive

    called = []
    def remember(**kwargs):
        called.append(('remember', kwargs))
        return {'success': True}
    def open_dashboard(**kwargs):
        called.append(('open_dashboard', kwargs))
        return {'success': True}

    async def scenario():
        with patch('src.gemini_live.genai.Client'):
            provider = GeminiLive('test', 'model', 'user', lambda _: None,
                                  conversation=ConversationContext(),
                                  tool_functions={'remember': remember, 'open_dashboard': open_dashboard})
        class Session:
            def __init__(self):
                self.count = 0
                self.sent = []
            async def send_tool_response(self, function_responses):
                self.sent.append(function_responses)
            async def receive(self):
                self.count += 1
                if self.count == 1:
                    call = SimpleNamespace(name='remember', id='1', args={'content': 'texte ou voix'})
                    yield SimpleNamespace(tool_call=SimpleNamespace(function_calls=[call]),
                                          server_content=None, session_resumption_update=None)
                else:
                    provider.session = None
        session = Session()
        provider.session = session
        await provider.receive_loop()
        assert session.sent[0][0].response['success']
        provider._turn_tool_called = False
        assert await provider._maybe_run_dashboard_fallback('Ouvre le tableau de bord')
        assert called == [('remember', {'content': 'texte ou voix'}), ('open_dashboard', {})]
        provider.tool_functions = {}
        provider._turn_tool_called = False
        assert not await provider._maybe_run_dashboard_fallback('Ouvre le tableau de bord')
    asyncio.run(scenario())
