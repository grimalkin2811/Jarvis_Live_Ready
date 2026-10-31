"""Shared runtime wired to real GeminiLive and the in-process fake Live server."""
import asyncio
from dataclasses import replace
from unittest.mock import patch

from src.conversation import ConversationContext
from src.core.runtime import CoreRuntime
from src.gemini_live import AuthError, GeminiLive, SessionRecoveryExhausted
from src.memory import MemoryManager
from src.runtime_status import EngineState, RuntimeStatusBoard
from tests.live_harness import FakeClient, FakeLiveServer
from tests.test_core_runtime import Audio, config, factory_set


def test_runtime_routes_voice_and_dashboard_text_through_same_session(tmp_path):
    async def scenario():
        server = FakeLiveServer("2.x")
        server.text_turns_use_oracle = True
        context = ConversationContext()
        cfg = config()
        cfg.model = "gemini-2.5-flash-native-audio-preview-12-2025"
        cfg.memory_database_path = str(tmp_path / "memory.db")

        def make_provider(*args, **kwargs):
            with patch("src.gemini_live.genai.Client", return_value=FakeClient(server)):
                return GeminiLive(*args, **kwargs)

        factories = replace(factory_set([]), audio=Audio, provider=make_provider,
                            memory=MemoryManager, conversation=lambda: context,
                            auth_error=AuthError, recovery_error=SessionRecoveryExhausted)
        runtime = CoreRuntime(cfg, factories=factories, status=RuntimeStatusBoard())
        task = asyncio.create_task(runtime.run())

        async def wait_for(predicate):
            async with asyncio.timeout(5):
                while not predicate():
                    await asyncio.sleep(0.005)

        try:
            await wait_for(lambda: runtime.status.snapshot()["engine_state"] == EngineState.READY)
            provider = runtime.provider
            await wait_for(provider.can_send)
            session = server.current
            session.queue_utterance("Je m'appelle Ada", chunks=3)
            for _ in range(3):
                runtime.audio.mic(b"\x01\x02" * 160)
                await asyncio.sleep(0.02)
            await wait_for(lambda: context.turn_count() >= 1 and provider.can_send())
            result = await asyncio.wrap_future(runtime.status.submit_text("Quel est mon prénom ?"))
            assert result["accepted"]
            await wait_for(lambda: context.turn_count() >= 2 and provider.can_send())
            messages = context.get_messages()
            assistant_messages = [m.text for m in messages if m.role == "assistant"]
            assert len(assistant_messages) >= 2 and "Ada" in assistant_messages[-1]
            assert any(e.kind == "realtime_audio" for e in server.wire)
            assert any(e.kind == "client_content" for e in server.wire)
            assert len(server.sessions) == 1
        finally:
            runtime.request_stop()
            await asyncio.wait_for(task, 5)
    asyncio.run(scenario())
