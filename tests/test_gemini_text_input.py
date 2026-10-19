"""Entrée TEXTE du moteur Gemini Live (conversation écrite du Dashboard).

Faux serveur Live (``tests/live_harness``) : aucun appel réseau, aucune clé.
Vérifie que le texte devient un tour utilisateur ORDINAIRE de la même
session (contexte partagé, porte micro, concurrence) sans modifier le
chemin audio historique.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.live_harness import FakeClient, FakeLiveServer  # noqa: E402

from src.conversation import ConversationContext  # noqa: E402
from src.gemini_live import TEXT_INPUT_MAX_CHARS, GeminiLive, TextSubmitError  # noqa: E402


def _make(server, **kwargs) -> GeminiLive:
    gemini = GeminiLive(
        key="test-key",
        model="gemini-2.5-flash-native-audio-preview-12-2025",
        user="Test",
        on_audio=lambda pcm: None,
        conversation=ConversationContext(max_turns=20, max_tokens=8000),
        **kwargs,
    )
    gemini.client = FakeClient(server)
    return gemini


class TextInputTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeLiveServer(semantics="2.x")
        self.opened = []
        self.resolved = []
        self.transcripts = []
        self.gemini = _make(
            self.server,
            on_turn_open=lambda: self.opened.append(1),
            on_turn_resolved=lambda: self.resolved.append(1),
            on_user_transcript=lambda text, final=False: self.transcripts.append((text, final)),
        )
        await self.gemini.connect()
        self.receiver = asyncio.create_task(self.gemini.receive_loop())

    async def asyncTearDown(self) -> None:
        self.receiver.cancel()
        try:
            await self.receiver
        except BaseException:
            pass
        await self.gemini.close()

    async def _wait(self, predicate, timeout=3.0):
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.01)
        return predicate()

    async def test_text_becomes_an_ordinary_turn_of_the_same_session(self):
        generation = self.gemini.session_generation
        result = await self.gemini.submit_text("  Quelle heure est-il ?  ")
        self.assertTrue(result["accepted"])
        self.assertEqual(result["session_generation"], generation)
        events = [e for e in self.server.client_content_events() if e.contents]
        self.assertEqual(events[-1].contents[-1]["parts"][0]["text"], "Quelle heure est-il ?")
        self.assertTrue(events[-1].turn_complete)
        messages = self.gemini.conversation.get_messages()
        self.assertEqual(messages[0].role, "user")
        self.assertEqual(messages[0].text, "Quelle heure est-il ?")
        self.assertEqual(self.transcripts[-1], ("Quelle heure est-il ?", True))
        self.assertEqual(self.opened, [1])
        # La réponse arrive par le chemin normal ; le tour se clôt.
        self.assertTrue(await self._wait(lambda: any(m.role == "assistant" for m in
                                                      self.gemini.conversation.get_messages())))
        self.assertTrue(await self._wait(lambda: self.resolved == [1]))
        self.assertFalse(self.gemini.engine_snapshot()["text_turn_pending"])
        self.assertEqual(self.gemini.session_generation, generation, "aucune reconnexion")

    async def test_mic_is_held_while_text_awaits_its_answer(self):
        self.server.semantics = "3.x"  # commit silencieux : aucune réponse ne vient
        await self.gemini.submit_text("Bonjour")
        allowed, reason = self.gemini._can_send_now()
        self.assertFalse(allowed)
        self.assertEqual(reason, "message texte en cours")
        # Une interruption réelle reste prioritaire.
        self.gemini.request_interrupt()
        self.assertTrue(self.gemini._can_send_now()[0])

    async def test_watchdog_releases_mic_if_no_answer_ever_comes(self):
        import src.gemini_live as module

        self.server.semantics = "3.x"
        original = module.TEXT_TURN_TIMEOUT_SECONDS
        module.TEXT_TURN_TIMEOUT_SECONDS = 0.05
        try:
            await self.gemini.submit_text("Bonjour")
            self.assertTrue(self.gemini._text_turn_pending)
            self.assertTrue(await self._wait(lambda: not self.gemini._text_turn_pending, 1.0))
            kinds = [e["kind"] for e in self.gemini.trace_events()]
            self.assertIn("TEXT_TURN_TIMEOUT", kinds)
        finally:
            module.TEXT_TURN_TIMEOUT_SECONDS = original

    async def test_concurrent_submissions_one_accepted_one_busy(self):
        self.server.semantics = "3.x"
        results = await asyncio.gather(self.gemini.submit_text("Un"), self.gemini.submit_text("Deux"),
                                       return_exceptions=True)
        accepted = [r for r in results if isinstance(r, dict)]
        refused = [r for r in results if isinstance(r, TextSubmitError)]
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(refused), 1)
        self.assertEqual(refused[0].code, "busy")
        users = [m for m in self.gemini.conversation.get_messages() if m.role == "user"]
        self.assertEqual(len(users), 1, "le message refusé n'entre pas dans le contexte")

    async def test_busy_states_refuse_without_touching_context(self):
        for attribute, value in (("speaking", True), ("tool_active", True), ("_regeneration_pending", True)):
            with self.subTest(attribute=attribute):
                setattr(self.gemini, attribute, value)
                with self.assertRaises(TextSubmitError) as ctx:
                    await self.gemini.submit_text("Salut")
                self.assertEqual(ctx.exception.code, "busy")
                setattr(self.gemini, attribute, False)
        # Demande vocale en cours de transcription.
        self.gemini._begin_turn_if_needed()
        self.gemini._turn_user_text.append("je parle")
        with self.assertRaises(TextSubmitError) as ctx:
            await self.gemini.submit_text("Salut")
        self.assertEqual(ctx.exception.code, "busy")
        self.assertEqual(self.gemini.conversation.get_messages(), ())

    async def test_validation_errors(self):
        with self.assertRaises(TextSubmitError) as ctx:
            await self.gemini.submit_text("   ")
        self.assertEqual(ctx.exception.code, "empty")
        with self.assertRaises(TextSubmitError) as ctx:
            await self.gemini.submit_text("x" * (TEXT_INPUT_MAX_CHARS + 1))
        self.assertEqual(ctx.exception.code, "too_long")

    async def test_send_failure_marks_turn_failed_and_resets_state(self):
        session = self.gemini.session

        async def broken(*args, **kwargs):
            raise RuntimeError("websocket fermé")

        session.send_client_content = broken
        with self.assertRaises(TextSubmitError) as ctx:
            await self.gemini.submit_text("Message perdu ?")
        self.assertEqual(ctx.exception.code, "send_failed")
        messages = self.gemini.conversation.get_messages()
        self.assertTrue(messages[-1].failed, "le message est conservé mais marqué en échec")
        self.assertFalse(self.gemini._text_turn_pending)
        self.assertTrue(self.gemini._turn_closed)
        self.assertEqual(self.resolved, [1], "le tour annoncé ouvert est résolu (fenêtre audio libérée)")

    async def test_not_connected_and_not_ready(self):
        self.gemini._session_ready = False
        with self.assertRaises(TextSubmitError) as ctx:
            await self.gemini.submit_text("x")
        self.assertEqual(ctx.exception.code, "not_ready")
        self.gemini._session_ready = True
        session, self.gemini.session = self.gemini.session, None
        try:
            with self.assertRaises(TextSubmitError) as ctx:
                await self.gemini.submit_text("x")
            self.assertEqual(ctx.exception.code, "not_connected")
        finally:
            self.gemini.session = session

    async def test_threadsafe_entry_from_another_thread(self):
        box = {}

        def worker():
            future = self.gemini.submit_text_threadsafe("Depuis le thread Qt")
            box["result"] = future.result(timeout=3)

        thread = threading.Thread(target=worker)
        thread.start()
        while thread.is_alive():
            await asyncio.sleep(0.01)
        self.assertTrue(box["result"]["accepted"])

    async def test_audio_path_unchanged_when_no_text_is_sent(self):
        self.assertTrue(self.gemini._can_send_now()[0])
        self.assertFalse(self.gemini.engine_snapshot()["text_turn_pending"])


class ThreadsafeWithoutLoopTests(unittest.TestCase):
    def test_future_fails_cleanly_when_engine_loop_is_not_running(self):
        gemini = _make(FakeLiveServer())
        future = gemini.submit_text_threadsafe("x")
        with self.assertRaises(TextSubmitError) as ctx:
            future.result(timeout=1)
        self.assertEqual(ctx.exception.code, "loop_unavailable")


if __name__ == "__main__":
    unittest.main()
