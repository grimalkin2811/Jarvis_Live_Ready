"""Core lifecycle tests use no Qt, devices, credentials or external service."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from src.core.events import EventBus, RuntimeEvent
from src.core.runtime import CoreRuntime, RuntimeFactories, RuntimeHooks
from src.core.services import ServiceRegistry
from src.runtime_status import RuntimeStatusBoard, EngineState


class AuthFailure(Exception):
    pass


class RecoveryFailure(Exception):
    pass


class Memory:
    enabled = True
    available = True

    def __init__(self, *args, **kwargs):
        pass


class Conversation:
    def __init__(self):
        self.resets = []

    def start_new_conversation(self, reason):
        self.resets.append(reason)


class Resource:
    def __init__(self, name, closed):
        self.name, self.closed = name, closed

    def stop(self):
        self.closed.append(self.name)

    def close(self, wait=False):
        self.closed.append(self.name)


class Audio:
    def __init__(self, mic, **kwargs):
        self.mic, self.kwargs = mic, kwargs
        self.wake_model = None
        self.wake_framework = ""
        self.awake = False
        self.stopped = False

    def start(self):
        pass

    def stop(self):
        self.stopped = True

    def play(self, *args): pass
    def extend_listening(self): pass
    def clear_output(self): pass
    def begin_speaking(self): pass
    def note_turn_open(self): pass
    def note_turn_resolved(self): pass
    def stop_speaking(self): pass


class Provider:
    def __init__(self, key, model, user, **kwargs):
        self.model, self.kwargs = model, kwargs
        self.ready = asyncio.Event()
        self.reconnect_requested = False
        self.session_generation = 1
        self.turn_epoch = 0
        self.closed = 0

    def notify_mic_paused(self): pass
    def submit_text_threadsafe(self, text): return text
    def can_send(self): return True
    def request_interrupt(self, **kwargs): pass
    def session_recovery_backoff_seconds(self): return 0
    async def send_audio(self, pcm, **kwargs): pass
    async def connect(self): pass
    async def receive_loop(self):
        self.ready.set()
        await asyncio.Event().wait()
    async def close(self):
        self.closed += 1


def factory_set(closed, *, audio=Audio, provider=Provider):
    return RuntimeFactories(audio, provider, Memory, lambda memory: None, Conversation,
                            lambda: Resource("scheduler", closed),
                            lambda key: Resource("tasks", closed), AuthFailure, RecoveryFailure)


def config():
    return SimpleNamespace(api_key="test", model="fake", user="tester", memory_database_path=":memory:",
                           memory_enabled=True, memory_max_results=5, memory_min_importance=1)


def test_events_isolate_failures_and_unsubscribe(caplog):
    bus = EventBus()
    received = []
    unsubscribe = bus.subscribe(lambda event: received.append(event.kind))
    def bad(event):
        raise RuntimeError("subscriber failed")
    bus.subscribe(bad)
    bus.publish(RuntimeEvent("start"))
    unsubscribe()
    unsubscribe()
    bus.publish(RuntimeEvent("stop"))
    assert received == ["start"]
    assert "subscriber failed" in caplog.text


def test_registry_rejects_duplicates_and_closes_in_reverse_order():
    registry = ServiceRegistry()
    closed = []
    registry.register("one", 1, close=lambda: closed.append("one"))
    registry.register("two", 2, close=lambda: closed.append("two"))
    with pytest.raises(ValueError):
        registry.register("one", 3)
    with pytest.raises(LookupError):
        registry.require("missing")
    registry.close()
    registry.close()
    assert closed == ["two", "one"]


def test_runtime_shared_lifecycle_and_cancellation():
    async def scenario():
        closed = []
        status = RuntimeStatusBoard()
        runtime = CoreRuntime(config(), RuntimeHooks(reason="test"), factories=factory_set(closed), status=status)
        events = []
        runtime.events.subscribe(lambda event: events.append(event.kind))
        task = asyncio.create_task(runtime.run())
        while runtime.provider is None:
            await asyncio.sleep(0)
        await runtime.provider.ready.wait()
        provider, audio = runtime.provider, runtime.audio
        assert status.snapshot()["engine_state"] == EngineState.READY
        assert status.submit_text("bonjour") == "bonjour"
        assert runtime.services.require("conversation").resets == ["test"]
        other = CoreRuntime(config(), factories=factory_set([]), status=RuntimeStatusBoard())
        with pytest.raises(RuntimeError, match="already active"):
            await other.run()
        # Exercise the same thread-safe stop path used by the Qt frontend.
        thread = threading.Thread(target=runtime.request_stop)
        thread.start()
        thread.join()
        await asyncio.wait_for(task, 2)
        assert provider.closed == 1
        assert audio.stopped
        assert closed == ["tasks", "scheduler"]
        assert status.snapshot()["engine_state"] == EngineState.STOPPED
        assert not status.snapshot()["text_input"]
        assert events == ["starting", "audio_started", "connected", "stopped"]
    asyncio.run(scenario())


def test_failed_start_cleans_resources_and_preserves_failed_status():
    class BrokenAudio(Audio):
        def start(self):
            raise OSError("device unavailable")

    async def scenario():
        closed = []
        status = RuntimeStatusBoard()
        runtime = CoreRuntime(config(), factories=factory_set(closed, audio=BrokenAudio), status=status)
        with pytest.raises(OSError, match="device unavailable"):
            await runtime.run()
        assert runtime.audio.stopped
        assert runtime.provider.closed == 1
        assert closed == ["tasks", "scheduler"]
        assert status.snapshot()["engine_state"] == EngineState.FAILED
        assert status.snapshot()["audio_state"] == "failed"
        assert not status.snapshot()["text_input"]
    asyncio.run(scenario())


def test_failed_cleanup_is_visible():
    class BrokenStop(Audio):
        def stop(self):
            raise RuntimeError("cannot stop device")

    async def scenario():
        runtime = CoreRuntime(config(), factories=factory_set([], audio=BrokenStop), status=RuntimeStatusBoard())
        task = asyncio.create_task(runtime.run())
        while runtime.provider is None:
            await asyncio.sleep(0)
        await runtime.provider.ready.wait()
        runtime.request_stop()
        await task
        assert runtime.status.snapshot()["engine_state"] == EngineState.FAILED
    asyncio.run(scenario())


def test_stop_before_start_never_constructs_backend():
    async def scenario():
        runtime = CoreRuntime(config(), factories=factory_set([]), status=RuntimeStatusBoard())
        runtime.request_stop()
        await runtime.run()
        assert runtime.provider is None
    asyncio.run(scenario())
