"""Runtime retry/terminal-error behaviour, without a real Live session."""
import asyncio

from src.core.runtime import CoreRuntime
from src.runtime_status import EngineState, RuntimeStatusBoard
from tests.test_core_runtime import AuthFailure, Provider, config, factory_set


def test_reconnect_closes_each_session_and_preserves_runtime():
    class Flaky(Provider):
        async def connect(self):
            self.attempts = getattr(self, "attempts", 0) + 1
            if self.attempts == 1:
                raise ConnectionError("temporary")

    async def scenario():
        runtime = CoreRuntime(config(), factories=factory_set([], provider=Flaky), status=RuntimeStatusBoard())
        events = []
        runtime.events.subscribe(lambda event: events.append(event.kind))
        task = asyncio.create_task(runtime.run())
        while runtime.provider is None:
            await asyncio.sleep(0)
        provider = runtime.provider
        await asyncio.wait_for(provider.ready.wait(), 3)
        assert provider.attempts == 2
        assert "reconnecting" in events
        runtime.request_stop()
        await asyncio.wait_for(task, 2)
        assert provider.closed == 2
        assert runtime.status.snapshot()["engine_state"] == EngineState.STOPPED
    asyncio.run(scenario())


def test_auth_failure_is_terminal_and_visible():
    class Rejected(Provider):
        async def connect(self):
            raise AuthFailure("invalid key")

    async def scenario():
        runtime = CoreRuntime(config(), factories=factory_set([], provider=Rejected), status=RuntimeStatusBoard())
        await asyncio.wait_for(runtime.run(), 2)
        assert runtime.provider.closed == 1
        assert runtime.status.snapshot()["engine_state"] == EngineState.AUTH_ERROR
        assert runtime.audio.stopped
    asyncio.run(scenario())
