"""Regressions around interrupted startup and resource ownership."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.core.runtime import CoreRuntime
from src.runtime_status import EngineState, RuntimeStatusBoard
from tests.test_core_runtime import Audio, Provider, config, factory_set


def test_stop_during_audio_start_closes_provider_before_return():
    runtime_holder = []

    class StopsDuringStart(Audio):
        def start(self):
            runtime_holder[0].request_stop()

    async def scenario():
        runtime = CoreRuntime(config(), factories=factory_set([], audio=StopsDuringStart), status=RuntimeStatusBoard())
        runtime_holder.append(runtime)
        await runtime.run()
        assert runtime.provider.closed == 1
        assert runtime.audio.stopped
        assert runtime.status.snapshot()["engine_state"] == EngineState.STOPPED
        # No queued cancellation may leak into the enclosing coroutine.
        await asyncio.sleep(0)
    asyncio.run(scenario())


def test_provider_cleanup_runs_even_if_text_registration_fails():
    class BrokenBoard(RuntimeStatusBoard):
        def register_text_handler(self, handler):
            raise RuntimeError("registration failure")

    async def scenario():
        runtime = CoreRuntime(config(), factories=factory_set([]), status=BrokenBoard())
        with pytest.raises(RuntimeError, match="registration failure"):
            await runtime.run()
        assert runtime.provider.closed == 1
        assert runtime.audio.stopped
    asyncio.run(scenario())


def test_provider_disposal_once_after_session_close():
    class DisposableProvider(Provider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.disposed = 0
        async def shutdown(self):
            self.disposed += 1

    async def scenario():
        runtime = CoreRuntime(config(), factories=factory_set([], provider=DisposableProvider), status=RuntimeStatusBoard())
        task = asyncio.create_task(runtime.run())
        while runtime.provider is None:
            await asyncio.sleep(0)
        await runtime.provider.ready.wait()
        provider = runtime.provider
        runtime.request_stop()
        await task
        assert provider.closed == 1
        assert provider.disposed == 1
    asyncio.run(scenario())


def test_cancellation_during_cleanup_waits_for_disposal_and_releases_owner():
    async def scenario():
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowDispose(Provider):
            async def shutdown(self):
                entered.set()
                await release.wait()

        runtime = CoreRuntime(config(), factories=factory_set([], provider=SlowDispose), status=RuntimeStatusBoard())
        task = asyncio.create_task(runtime.run())
        while runtime.provider is None:
            await asyncio.sleep(0)
        await runtime.provider.ready.wait()
        runtime.request_stop()
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert runtime.audio.stopped
        assert CoreRuntime._owner is None
    asyncio.run(scenario())


def test_gemini_client_shutdown_is_separate_from_session_close():
    from src.gemini_live import GeminiLive

    async def scenario():
        gemini = GeminiLive(key="test", model="fake", user="test", on_audio=lambda pcm: None)
        close_client = AsyncMock()
        gemini.client = SimpleNamespace(aio=SimpleNamespace(aclose=close_client))
        await gemini.close()
        close_client.assert_not_awaited()
        await gemini.shutdown()
        close_client.assert_awaited_once()
    asyncio.run(scenario())


def test_status_subscriber_error_is_logged_and_other_subscribers_run(caplog):
    board = RuntimeStatusBoard()
    received = []
    def broken(snapshot):
        raise RuntimeError("broken status subscriber")
    board.add_hook(broken)
    board.add_hook(received.append)
    board.set_engine_state(EngineState.STARTING)
    assert received[-1]["engine_state"] == EngineState.STARTING
    assert "broken status subscriber" in caplog.text
