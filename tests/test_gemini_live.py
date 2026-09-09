"""Tests de GeminiLive sans réseau (mocks du client genai).

Vérifie les améliorations de fluidité/robustesse :
* erreur d'authentification détectée -> AuthError (pas de retry infini) ;
* les outils s'exécutent dans un thread et tool_active est toujours
  réinitialisé, même si l'outil échoue ;
* le changement de voix déclenche une reconnexion douce (sans exception
  remontée à la boucle externe).
"""

import asyncio
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from src.gemini_live import AuthError, GeminiLive, _is_auth_error  # noqa: E402


class _FakeClient:
    """Remplace genai.Client : live.connect() renvoie le contexte fourni."""

    def __init__(self, live):
        self.aio = type("FakeAio", (), {"live": live})()


def _make_gemini(**kwargs):
    gemini = GeminiLive(
        key="test-key",
        model="test-model",
        user="Test",
        on_audio=lambda pcm: None,
        **kwargs,
    )
    return gemini


class AuthDetectionTests(unittest.TestCase):
    def test_is_auth_error_markers(self) -> None:
        for message in (
            "API key not valid",
            "401 Unauthorized",
            "PERMISSION_DENIED: foo",
            "unauthenticated request",
            "api_key invalid",
        ):
            self.assertTrue(_is_auth_error(Exception(message)), message)

    def test_other_errors_are_not_auth(self) -> None:
        for message in ("connection reset", "timeout 500", "quota exceeded 429"):
            self.assertFalse(_is_auth_error(Exception(message)), message)

    def test_connect_raises_auth_error_on_401(self) -> None:
        gemini = _make_gemini()

        class _FailingCtx:
            async def __aenter__(self):
                raise Exception("401 API key not valid. Please pass a valid key")

            async def __aexit__(self, *args):
                return False

        class _FakeLive:
            def connect(self, model, config):
                return _FailingCtx()

        gemini.client = _FakeClient(_FakeLive())

        with self.assertRaises(AuthError):
            asyncio.run(gemini.connect())


class ToolExecutionTests(unittest.TestCase):
    def test_tool_runs_in_thread_and_always_reactivates_mic(self) -> None:
        from src import gemini_live as gl

        executed = []

        def slow_tool(**kwargs):
            executed.append("done")

        # Un outil qui lève ne doit pas laisser tool_active à True.
        def failing_tool(**kwargs):
            raise RuntimeError("boom")

        original = dict(gl.TOOL_FUNCTIONS)
        gl.TOOL_FUNCTIONS["fake_slow"] = slow_tool
        gl.TOOL_FUNCTIONS["fake_fail"] = failing_tool
        try:
            gemini = _make_gemini()

            class _FakeSession:
                def __init__(self):
                    self.sent = []

                async def send_tool_response(self, function_responses):
                    self.sent.append(function_responses)

                def __aiter__(self):
                    return self

                async def __anext__(self):
                    raise StopAsyncIteration

            session = _FakeSession()
            gemini.session = session

            class _Call:
                def __init__(self, name, cid, args=None):
                    self.name = name
                    self.id = cid
                    self.args = args or {}

            class _ToolCall:
                def __init__(self, calls):
                    self.function_calls = calls

            class _Msg:
                def __init__(self, tc):
                    self.tool_call = tc

            msg = _Msg(_ToolCall([
                _Call("fake_slow", "1"),
                _Call("fake_fail", "2"),
                _Call("outil_inconnu", "3"),
            ]))

            async def run():
                # receive_loop itère sur session.receive() ; on fournit un
                # seul message (appel d'outils) puis StopAsyncIteration.
                session = _FakeSession()

                async def fake_receive():
                    yield msg

                session.receive = fake_receive
                gemini.session = session
                await gemini.receive_loop()
                gemini.session = session  # garder la référence des réponses

            asyncio.run(run())
            self.assertEqual(executed, ["done"])
            self.assertFalse(gemini.tool_active)
            responses = gemini.session.sent[0]
            self.assertEqual(len(responses), 3)
            self.assertFalse(responses[1].response["success"])
            self.assertEqual(responses[1].response["error"], "boom")
            self.assertFalse(responses[2].response["success"])
        finally:
            gl.TOOL_FUNCTIONS.clear()
            gl.TOOL_FUNCTIONS.update(original)


class VoiceReconnectTests(unittest.TestCase):
    def test_voice_change_triggers_soft_reconnect(self) -> None:
        version = {"v": 0}
        gemini = _make_gemini(
            voice_provider=lambda: "Kore",
            voice_version_provider=lambda: version["v"],
        )

        class _FakeCtx:
            def __init__(self):
                self.exited = False

            async def __aenter__(self):
                return session

            async def __aexit__(self, *args):
                self.exited = True
                return False

        class _FakeSession:
            def __init__(self):
                self.receive_calls = 0

            def receive(self):
                self.receive_calls += 1

                async def gen():
                    await asyncio.sleep(0.05)
                    yield {"server_content": None}

                return gen()

        session = _FakeSession()
        ctx = _FakeCtx()

        class _FakeLive:
            def connect(self, model, config):
                return ctx

        gemini.client = _FakeClient(_FakeLive())

        async def scenario():
            await gemini.connect()
            self.assertFalse(gemini.reconnect_requested)
            version["v"] = 1  # l'utilisateur change de voix
            await asyncio.sleep(0.8)
            self.assertTrue(gemini.reconnect_requested)
            self.assertTrue(ctx.exited)
            self.assertIsNone(gemini.session)

        try:
            asyncio.run(scenario())
        finally:
            task = gemini._watcher_task
            if task is not None and not task.done():
                task.cancel()


if __name__ == "__main__":
    unittest.main()
