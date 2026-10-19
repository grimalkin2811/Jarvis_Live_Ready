"""Audit pré-fusion (PR #50) : entrée TEXTE du Dashboard × conversation VOCALE.

FAUX SERVEUR UNIQUEMENT (``tests/live_harness``, ``tests/voice_harness``) :
aucun appel réseau, aucune clé API. Ces tests vérifient la logique du client
(``GeminiLive``) — ils ne valident PAS le comportement du vrai service Gemini
Live, qui reste à valider avec une clé réelle (voir
``docs/dashboard/AUDIT_PRE_FUSION.md``).

Le ``VoiceHarness`` exécute le VRAI ``GeminiLive`` avec la boucle
connect → receive_loop → close → reconnexion de ``src/ui.py`` ; la voix passe
par ``send_audio`` (canal audio réel) et le texte par ``submit_text``
(chemin du Dashboard). Scénarios couverts :

* une seule session et un seul contexte partagés voix → texte → voix ;
* refus « occupé » pendant un tour vocal (transcription puis réponse) ;
* micro retenu pendant qu'un texte attend sa réponse (aucun audio mêlé),
  interruption réelle toujours prioritaire ;
* relance de génération vide d'un tour texte (micro retenu, pas de doublon) ;
* interruption serveur, appel d'outil, coupure + reconnexion, nouvelle
  conversation pendant un texte en attente ;
* course du filet de 30 s (watchdog) entre deux messages texte ;
* annulation de l'envoi ; envois simultanés depuis deux threads ;
* non-régression : un tour purement vocal ne touche jamais au chemin texte.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.gemini_live as gemini_module  # noqa: E402
from src.gemini_live import TextSubmitError  # noqa: E402
from tests.live_harness import (  # noqa: E402
    FakeLiveServer,
    WireEvent,
    _as_content_dicts,
    msg_model_transcript,
    msg_turn_complete,
    msg_user_transcript,
)
from tests.voice_harness import VoiceHarness  # noqa: E402

PCM = b"\x01\x02" * 160


class _Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeLiveServer(semantics="2.x")
        self.server.text_turns_use_oracle = True
        self.harness = VoiceHarness(self.server)
        self.gemini = self.harness.gemini
        await self.harness.start()
        await self.harness.wait_ready()

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def _wait(self, predicate, timeout: float = 5.0) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.005)
        return bool(predicate())

    async def _text_turn(self, text: str) -> str:
        """Envoie un message écrit et attend la fin du tour (réponse complète)."""
        before = len(self.harness.turns)
        result = await self.gemini.submit_text(text)
        self.assertTrue(result["accepted"])
        self.assertTrue(await self._wait(lambda: len(self.harness.turns) > before), "réponse au texte attendue")
        await self._wait(lambda: not self.gemini._text_turn_pending, 1.0)
        return self.harness.turns[-1].assistant_text

    def _swallow_text(self) -> None:
        """Le serveur reçoit (et commite) le texte mais ne répond jamais."""
        session = self.server.current

        async def swallow(turns=None, turn_complete=True):
            contents = _as_content_dicts(turns)
            self.server.wire.append(WireEvent(session_id=session.session_id, generation=session.generation,
                                              kind="client_content", turn_complete=turn_complete,
                                              contents=contents))
            session.committed.extend(contents)

        session.send_client_content = swallow

    def _user_texts(self) -> list[str]:
        return [m.text for m in self.harness.conversation.get_messages() if m.role == "user"]


class SharedSessionAndContextTests(_Base):
    async def test_voice_text_voice_share_one_session_and_one_context(self):
        generation = self.gemini.session_generation
        sessions = len(self.server.sessions)
        await self.harness.speak("Bonjour, je m'appelle Ada")
        # Le message ÉCRIT voit le tour VOCAL (même session, même contexte).
        self.assertEqual(await self._text_turn("Comment je m'appelle ?"), "Ton prénom est Ada.")
        # Et la voix continue ensuite normalement, dans la même session.
        self.assertEqual(await self.harness.speak("Quel est mon prénom ?"), "Ton prénom est Ada.")
        messages = self.harness.conversation.get_messages()
        self.assertEqual([m.role for m in messages], ["user", "assistant"] * 3)
        self.assertEqual(self._user_texts()[1], "Comment je m'appelle ?")
        self.assertEqual(self._user_texts().count("Comment je m'appelle ?"), 1, "aucun doublon du message écrit")
        self.assertEqual(self.gemini.session_generation, generation, "aucune reconnexion")
        self.assertEqual(len(self.server.sessions), sessions, "aucune nouvelle session")
        self.assertTrue(self.gemini.can_send(), "micro rendu après la réponse au texte")
        self.assertFalse(self.gemini._text_turn_pending)

    async def test_text_is_refused_while_a_voice_turn_is_transcribed_then_answered(self):
        session = self.server.current
        await session._emit(msg_user_transcript("je suis en train de"))
        self.assertTrue(await self._wait(lambda: bool(self.gemini._turn_user_text)))
        with self.assertRaises(TextSubmitError) as ctx:
            await self.gemini.submit_text("Message écrit concurrent")
        self.assertEqual(ctx.exception.code, "busy")
        # Jarvis répond à la VOIX : toujours occupé, aucune réponse concurrente.
        await session._emit(msg_model_transcript("Je t'écoute", audio=b"\x00\x01" * 32))
        self.assertTrue(await self._wait(lambda: self.gemini.speaking))
        with self.assertRaises(TextSubmitError) as ctx:
            await self.gemini.submit_text("Message écrit concurrent")
        self.assertEqual(ctx.exception.code, "busy")
        await session._emit(msg_turn_complete())
        self.assertTrue(await self._wait(lambda: not self.gemini.speaking and self.gemini._turn_closed))
        self.assertNotIn("Message écrit concurrent", self._user_texts(), "un refus n'entre jamais dans le contexte")
        # Tour vocal clos : le texte part normalement.
        self.assertTrue(await self._text_turn("Et maintenant ?"))
        self.assertEqual(self._user_texts(), ["je suis en train de", "Et maintenant ?"])


class MicGateTests(_Base):
    async def test_no_microphone_audio_is_mixed_with_a_pending_text_turn(self):
        await self.harness.speak("Bonjour")
        self._swallow_text()
        await self.gemini.submit_text("Réponds-moi par écrit")
        sent_at = len(self.server.wire)
        self.assertFalse(self.gemini.can_send())
        self.assertEqual(self.gemini._can_send_now()[1], "message texte en cours")
        for _ in range(5):
            await self.gemini.send_audio(PCM, capture_generation=self.gemini.session_generation,
                                         capture_turn_epoch=self.gemini.turn_epoch)
        self.assertEqual([e for e in self.server.wire[sent_at:] if e.kind == "realtime_audio"], [],
                         "aucun bloc micro ne part pendant que le texte attend sa réponse")
        # Une interruption RÉELLE de l'utilisateur reste toujours possible.
        self.gemini.request_interrupt()
        self.assertTrue(self.gemini.can_send())
        self.gemini._clear_interrupt()
        # La réponse (tardive) arrive : le tour se clôt et le micro est rendu.
        await self.server.current._emit(msg_model_transcript("Voilà ma réponse.", audio=b"\x00\x01" * 32))
        await self.server.current._emit(msg_turn_complete())
        self.assertTrue(await self._wait(lambda: not self.gemini._text_turn_pending and self.gemini.can_send()))
        self.assertEqual(self.harness.conversation.get_messages()[-1].text, "Voilà ma réponse.")

    async def test_empty_generation_on_a_text_turn_is_retried_with_the_mic_held(self):
        await self.harness.speak("Je m'appelle Ada")
        session = self.server.current
        original = session.send_client_content
        observed = []

        async def spy(turns=None, turn_complete=True):
            observed.append((self.gemini._text_turn_pending, self.gemini._regeneration_pending,
                             self.gemini.can_send()))
            return await original(turns=turns, turn_complete=turn_complete)

        session.send_client_content = spy
        self.server.empty_generations_remaining = 1
        self.assertEqual(await self._text_turn("Quel est mon prénom ?"), "Ton prénom est Ada.")
        kinds = [e["kind"] for e in self.gemini.trace_events()]
        self.assertIn("EMPTY_GENERATION_RETRY", kinds)
        self.assertEqual(len(observed), 2, "envoi initial + une relance")
        self.assertEqual(observed[1], (True, True, False), "micro retenu pendant la relance du texte")
        self.assertEqual(self._user_texts().count("Quel est mon prénom ?"), 1, "la relance ne duplique pas le message")
        self.assertTrue(self.gemini.can_send())
        self.assertFalse(self.gemini._regeneration_pending or self.gemini._text_turn_pending)


class TurnOutcomeTests(_Base):
    async def test_server_interruption_of_a_text_answer_releases_the_mic(self):
        await self.harness.speak("Bonjour")
        self.server.interrupt_next_turn = True
        await self._text_turn("Raconte-moi une longue histoire")
        self.assertTrue(self.harness.last_turn.interrupted)
        self.assertFalse(self.gemini._text_turn_pending)
        self.assertTrue(self.gemini.can_send())
        roles = [m.role for m in self.harness.conversation.get_messages()]
        self.assertEqual(roles[-2:], ["user", "assistant"], "réponse partielle conservée, tour clos")

    async def test_tool_call_during_a_text_turn_keeps_the_mic_closed_until_the_answer(self):
        await self.harness.speak("Bonjour")
        release = threading.Event()
        during = {}

        def probe_tool(**_kwargs):
            during["can_send"] = self.gemini.can_send()
            during["text_pending"] = self.gemini._text_turn_pending
            release.wait(5)
            return {"success": True, "message": "sonde exécutée"}

        self.server.next_tool_script = (r"sonde", "audit_probe_tool", {})
        self.server.tool_continuation = "La sonde a répondu."
        with mock.patch.dict(gemini_module.TOOL_FUNCTIONS, {"audit_probe_tool": probe_tool}):
            await self.gemini.submit_text("Lance la sonde")
            self.assertTrue(await self._wait(lambda: "can_send" in during))
            # Pendant l'outil : micro fermé, et un second texte est refusé.
            self.assertFalse(self.gemini.can_send())
            with self.assertRaises(TextSubmitError) as ctx:
                await self.gemini.submit_text("Encore un message")
            self.assertEqual(ctx.exception.code, "busy")
            release.set()
            self.assertTrue(await self._wait(lambda: not self.gemini._text_turn_pending and self.gemini.can_send()))
        self.assertEqual(during, {"can_send": False, "text_pending": True})
        tail = [(m.role, m.kind) for m in self.harness.conversation.get_messages()][-4:]
        self.assertEqual([role for role, _kind in tail], ["user", "tool", "tool", "assistant"])
        self.assertEqual(self.harness.conversation.get_messages()[-1].text, "La sonde a répondu.")

    async def test_connection_drop_during_a_text_turn_then_reconnection(self):
        await self.harness.speak("Je m'appelle Ada")
        generation = self.gemini.session_generation
        self._swallow_text()
        await self.gemini.submit_text("Message perdu dans la coupure")
        self.server.drop_connection()
        self.assertTrue(await self._wait(lambda: self.gemini.session_generation > generation))
        await self.harness.wait_ready()
        self.assertFalse(self.gemini._text_turn_pending, "la porte micro du texte ne survit pas à la coupure")
        self.assertTrue(self.gemini.can_send())
        lost = [m for m in self.harness.conversation.get_messages() if m.text == "Message perdu dans la coupure"]
        self.assertEqual(len(lost), 1)
        self.assertTrue(lost[0].failed, "le message est conservé et marqué « sans réponse »")
        # Nouvelle session : la conversation (voix + texte) continue avec le contexte.
        self.assertEqual(await self._text_turn("Comment je m'appelle ?"), "Ton prénom est Ada.")

    async def test_new_conversation_while_a_text_turn_is_pending(self):
        await self.harness.speak("Bonjour")
        generation = self.gemini.session_generation
        self._swallow_text()
        await self.gemini.submit_text("Texte en attente")
        self.assertTrue(self.gemini._text_turn_pending)
        self.harness.conversation.start_new_conversation(reason="test audit")
        self.assertTrue(await self._wait(lambda: self.gemini.session_generation > generation))
        await self.harness.wait_ready()
        self.assertFalse(self.gemini._text_turn_pending)
        self.assertTrue(self.gemini.can_send())
        self.assertEqual(self.harness.conversation.get_messages(), ())


class WatchdogAndCancellationTests(_Base):
    async def test_watchdog_of_an_answered_text_never_releases_the_next_text_early(self):
        """Course démontrée par l'audit : époque capturée APRÈS l'envoi."""
        await self.harness.speak("Bonjour")
        session = self.server.current
        original = session.send_client_content

        async def slow_send(turns=None, turn_complete=True):
            await original(turns=turns, turn_complete=turn_complete)
            await asyncio.sleep(0.05)  # écriture réseau encore en cours pendant la réponse

        session.send_client_content = slow_send
        loop = asyncio.get_running_loop()
        timeout = 1.5
        with mock.patch.object(gemini_module, "TEXT_TURN_TIMEOUT_SECONDS", timeout):
            epoch = self.gemini.turn_epoch
            await self.gemini.submit_text("Premier message")
            first_deadline = loop.time() + timeout
            self.assertGreater(self.gemini.turn_epoch, epoch, "la réponse a été traitée pendant l'envoi")
            self.assertFalse(self.gemini._text_turn_pending)
            await asyncio.sleep(0.6)
            self._swallow_text()
            await self.gemini.submit_text("Second message, sans réponse")
            second_deadline = loop.time() + timeout
            # Après l'échéance du PREMIER filet, avant celle du second.
            await asyncio.sleep(max(0.0, first_deadline + 0.2 - loop.time()))
            self.assertLess(loop.time(), second_deadline - 0.1, "machine trop lente pour ce scénario temporisé")
            self.assertTrue(self.gemini._text_turn_pending,
                            "le filet du premier message ne doit pas rouvrir le micro du second")
            self.assertFalse(self.gemini.can_send())
            # Le filet du second message, lui, finit bien par rendre le micro.
            self.assertTrue(await self._wait(lambda: not self.gemini._text_turn_pending, 3.0))
        # Serveur resté muet depuis l'envoi : la session est relancée (chien de
        # garde, PR #50) — le micro revient dès la nouvelle session prête.
        self.assertTrue(await self._wait(self.gemini.can_send, 3.0))
        self.assertIn("TEXT_TURN_TIMEOUT", [e["kind"] for e in self.gemini.trace_events()])

    async def test_cancelled_submission_never_leaves_the_mic_closed(self):
        await self.harness.speak("Bonjour")
        session = self.server.current
        gate = asyncio.Event()

        async def stuck_send(turns=None, turn_complete=True):
            await gate.wait()

        session.send_client_content = stuck_send
        task = asyncio.ensure_future(self.gemini.submit_text("Envoi annulé"))
        self.assertTrue(await self._wait(lambda: self.gemini._text_turn_pending))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.gemini._text_turn_pending, "porte micro rouverte après annulation")
        self.assertTrue(self.gemini.can_send())
        message = [m for m in self.harness.conversation.get_messages() if m.text == "Envoi annulé"]
        self.assertTrue(message and message[0].failed, "message conservé et marqué en échec")

    async def test_simultaneous_submissions_from_two_threads(self):
        await self.harness.speak("Bonjour")
        self._swallow_text()
        barrier = threading.Barrier(2)
        futures = {}

        def worker(name):
            barrier.wait()
            futures[name] = self.gemini.submit_text_threadsafe(f"Message {name}")

        threads = [threading.Thread(target=worker, args=(name,)) for name in ("A", "B")]
        for thread in threads:
            thread.start()
        while any(t.is_alive() for t in threads):
            await asyncio.sleep(0.005)
        results = await asyncio.gather(*(asyncio.wrap_future(f) for f in futures.values()), return_exceptions=True)
        accepted = [r for r in results if isinstance(r, dict)]
        refused = [r for r in results if isinstance(r, TextSubmitError)]
        self.assertEqual((len(accepted), len(refused)), (1, 1))
        self.assertEqual(refused[0].code, "busy")
        self.assertEqual(len([t for t in self._user_texts() if t.startswith("Message ")]), 1)


class VoiceOnlyNonRegressionTests(_Base):
    async def test_voice_only_turns_never_touch_the_text_path(self):
        reasons = []
        original = self.gemini._can_send_now

        def recording():
            allowed, reason = original()
            reasons.append(reason)
            return allowed, reason

        self.gemini._can_send_now = recording
        try:
            await self.harness.speak("Bonjour Jarvis")
            await self.harness.speak("Quel temps fait-il")
        finally:
            self.gemini._can_send_now = original
        self.assertEqual(len(self.harness.turns), 2)
        self.assertNotIn("message texte en cours", reasons)
        self.assertFalse(self.gemini._text_turn_pending)
        self.assertEqual([e for e in self.gemini.trace_events() if e["kind"].startswith("TEXT_")], [])
        self.assertEqual([e for e in self.server.client_content_events(self.gemini.session_generation)
                          if e.contents], [], "aucun clientContent émis par un tour purement vocal")


if __name__ == "__main__":
    unittest.main()
