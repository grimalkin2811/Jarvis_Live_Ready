"""Microphone send coroutines must finish before the session closes."""
import asyncio

from src.core.runtime import CoreRuntime
from src.runtime_status import RuntimeStatusBoard
from tests.test_core_runtime import Provider, config, factory_set


def test_pending_audio_send_is_cancelled_before_provider_close():
    class BlockingSend(Provider):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.sending = asyncio.Event()
            self.drained = False
            self.capture = None
        async def send_audio(self, pcm, **kwargs):
            self.capture = (pcm, kwargs)
            self.sending.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.drained = True
        async def close(self):
            assert self.drained
            await super().close()

    async def scenario():
        runtime = CoreRuntime(config(), factories=factory_set([], provider=BlockingSend), status=RuntimeStatusBoard())
        task = asyncio.create_task(runtime.run())
        while runtime.provider is None:
            await asyncio.sleep(0)
        provider = runtime.provider
        await provider.ready.wait()
        runtime.audio.mic(b"pcm")
        await asyncio.wait_for(provider.sending.wait(), 2)
        assert provider.capture == (b"pcm", {"capture_generation": 1, "capture_turn_epoch": 0})
        runtime.request_stop()
        await asyncio.wait_for(task, 2)
        assert provider.drained
        assert provider.closed == 1
        assert not runtime._mic_tasks
    asyncio.run(scenario())
