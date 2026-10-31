"""A model cannot grant its own confirmation through tool arguments."""
import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from src.core.action_boundary import ActionProposal, reject_unverified_action
from src.gemini_live import GeminiLive
from src import gemini_live


def test_rejects_model_confirm_and_unconfirmed_legacy_delete():
    assert reject_unverified_action(ActionProposal("sleep_pc", {"confirm": True}))
    assert reject_unverified_action(ActionProposal("sleep_pc", {"confirm": "false"}))
    assert reject_unverified_action(ActionProposal("delete_memory", {"memory_id": 2}))
    assert reject_unverified_action(ActionProposal("sleep_pc", {"confirm": False})) is None
    assert reject_unverified_action(ActionProposal("calculate", {})) is None


def test_real_live_dispatch_never_invokes_confirmed_tool():
    called = []
    gemini = GeminiLive(key="test", model="fake", user="test", on_audio=lambda pcm: None)
    call = SimpleNamespace(name="shutdown_pc", id="1", args={"confirm": True})
    msg = SimpleNamespace(tool_call=SimpleNamespace(function_calls=[call]), server_content=None,
                          session_resumption_update=None)

    class Session:
        def __init__(self):
            self.sent = []
            self.round = 0

        async def send_tool_response(self, function_responses):
            self.sent.extend(function_responses)

        async def receive(self):
            self.round += 1
            if self.round == 1:
                yield msg
            else:
                gemini.session = None

    async def scenario():
        session = Session()
        gemini.session = session
        with patch.dict(gemini_live.TOOL_FUNCTIONS, {"shutdown_pc": lambda **kw: called.append(kw)}):
            await gemini.receive_loop()
        assert not called
        assert session.sent[0].response["authorization_required"] is True
        assert session.sent[0].response["success"] is False
    asyncio.run(scenario())
