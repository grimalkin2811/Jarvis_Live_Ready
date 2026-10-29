"""Chien de garde « serveur muet » et filet texte (correctif PR #50).

Validation Windows réelle du 2026-10-16 (commit 398faf3) : après un premier
tour vocal resté sans fin de tour, Jarvis refusait l'audio (« session non
prête à recevoir de l'audio ») et le texte du Dashboard (« Jarvis est
occupé ») pour TOUS les scénarios suivants, jusqu'à une coupure réseau.

Défaut reproduit ici de façon déterministe : quand le serveur se tait alors
qu'une réponse est due (défaut serveur documenté, googleapis/python-genai#2117 :
l'audio cesse sans ``turn_complete`` ni erreur, WebSocket ouvert), les drapeaux
``speaking`` / ``_regeneration_pending`` / tour transcrit restaient vrais
indéfiniment. Le filet texte de 30 s, lui, rouvrait le micro mais laissait le
tour ouvert : chaque message écrit suivant était refusé avec un motif inexact
(« Une demande vocale est en cours de traitement »).

FAUX SERVEUR UNIQUEMENT (``tests/live_harness``) : aucune clé, aucun réseau.
Ces tests vérifient la logique du client — pas le comportement du vrai
service, validé séparément par ``scripts/validation/gemini_reel_dashboard.py``.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.gemini_live as gemini_module  # noqa: E402
from src.gemini_live import TextSubmitError  # noqa: E402
from tests.live_harness import (  # noqa: E402
    FakeLiveServer,
    WireEvent,
    _as_content_dicts,
    _Message,
    _ResumptionUpdate,
    _ServerContent,
    msg_turn_complete,
    msg_user_transcript,
)
from tests.voice_harness import VoiceHarness  # noqa: E402

STALL = 0.4  # chien de garde accéléré pour les tests (30 s en production)


class _Base(unittest.IsolatedAsyncioTestCase):
    stall_timeout = STALL
    text_timeout = 30.0

    async def asyncSetUp(self) -> None:
        self._patches = [
            mock.patch.object(gemini_module, "RESPONSE_STALL_TIMEOUT_SECONDS", self.stall_timeout),
            mock.patch.object(gemini_module, "TEXT_TURN_TIMEOUT_SECONDS", self.text_timeout),
        ]
        for patcher in self._patches:
            patcher.start()
        self.server = FakeLiveServer(semantics="2.x")
        self.server.text_turns_use_oracle = True
        # Le « modèle » ne connaît le code que s'il figure dans le contexte qu'il voit.
        self.server.oracle.register(
            r"quel code",
            lambda visible: "Le code est ORION-4." if any("ORION-4" in text for _role, text in visible)
            else "Je ne connais aucun code.",
        )
        self.harness = VoiceHarness(self.server)
        self.gemini = self.harness.gemini
        self.resolved: list[float] = []
        self.interrupted: list[float] = []
        self.gemini.on_turn_resolved = lambda: self.resolved.append(time.monotonic())
        harness_interrupted = self.gemini.on_interrupted

        def on_interrupted() -> None:
            self.interrupted.append(time.monotonic())
            harness_interrupted()

        self.gemini.on_interrupted = on_interrupted
        await self.harness.start()
        await self.harness.wait_ready()

    async def asyncTearDown(self) -> None:
        await self.harness.stop()
        for patcher in self._patches:
            patcher.stop()

    async def _wait(self, predicate, timeout: float = 5.0) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if predicate():
                return True
            await asyncio.sleep(0.01)
        return bool(predicate())

    async def _text_turn(self, text: str) -> str:
        before = len(self.harness.turns)
        result = await self.gemini.submit_text(text)
        self.assertTrue(result["accepted"])
        self.assertTrue(await self._wait(lambda: len(self.harness.turns) > before), "réponse au texte attendue")
        return self.harness.turns[-1].assistant_text

    def _swallow_client_content(self) -> None:
        """La session COURANTE reçoit les clientContent mais n'y répond jamais."""
        session = self.server.current

        async def swallow(turns=None, turn_complete=True):
            contents = _as_content_dicts(turns)
            self.server.wire.append(WireEvent(session_id=session.session_id, generation=session.generation,
                                              kind="client_content", turn_complete=turn_complete,
                                              contents=contents))
            session.committed.extend(contents)

        session.send_client_content = swallow

    def _kinds(self) -> list[str]:
        return [e["kind"] for e in self.gemini.trace_events()]

    def _stalls(self) -> list[dict]:
        return [e for e in self.gemini.trace_events() if e["kind"] == "RESPONSE_STALLED"]

    def _messages(self) -> list[tuple[str, str, bool]]:
        return [(m.role, m.text, bool(m.failed)) for m in self.harness.conversation.get_messages()]

    async def _assert_recovered(self, generation: int) -> None:
        """Après la reprise : nouvelle session prête, micro et texte de nouveau disponibles."""
        self.assertTrue(await self._wait(lambda: self.gemini.session_generation > generation and self.gemini.can_send()),
                        f"session relancée attendue (état : {self.gemini.engine_snapshot()})")
        snapshot = self.gemini.engine_snapshot()
        for flag in ("speaking", "tool_active", "regeneration_pending", "text_turn_pending", "turn_open"):
            self.assertFalse(snapshot[flag], f"{flag} doit être remis à zéro : {snapshot}")


class StalledResponseTests(_Base):
    async def test_voice_answer_stalled_mid_stream_recovers_with_context(self):
        """R1 : la réponse vocale démarre puis le serveur se tait (ni fin de tour, ni erreur)."""
        self.assertEqual(await self.harness.speak("Bonjour, je m'appelle Ada"), "D'accord.")
        generation = self.gemini.session_generation
        self.assertTrue(self.gemini.resumption_handle, "un handle de reprise existe après un tour complet")
        session = self.server.current
        await session._emit(msg_user_transcript("Raconte-moi une histoire"))
        await session._emit(_Message(_ServerContent(model_text="Il était une fois", audio=b"\x00\x01" * 64)))
        self.assertTrue(await self._wait(lambda: self.gemini.speaking))
        # Pendant le blocage : comportement « occupé » explicite (inchangé)...
        self.assertEqual(self.gemini._can_send_now(), (False, "jarvis parle"))
        with self.assertRaises(TextSubmitError) as ctx:
            await self.gemini.submit_text("Tu es là ?")
        self.assertEqual(ctx.exception.code, "busy")
        # ... mais borné : reprise automatique après le délai du chien de garde.
        await self._assert_recovered(generation)
        stalls = self._stalls()
        self.assertEqual(len(stalls), 1)
        self.assertEqual(stalls[0]["reason"], "réponse vocale arrêtée en cours de route")
        self.assertGreaterEqual(stalls[0]["silence_s"], STALL - 0.05)
        self.assertEqual(self.gemini.stalls_recovered, 1)
        self.assertIn("TURN_CLOSE", self._kinds())
        self.assertTrue(self.interrupted, "l'interface quitte l'état « Jarvis parle » (clear_output)")
        self.assertTrue(self.resolved, "le minuteur de conversation d'AudioIO est relâché")
        # Contexte cohérent : demande conservée UNE fois, réponse partielle conservée.
        messages = self._messages()
        self.assertEqual([t for r, t, _f in messages if r == "user"].count("Raconte-moi une histoire"), 1)
        self.assertIn(("assistant", "Il était une fois", False), messages)
        # Le checkpoint du handle précède le tour interrompu : une session
        # fraîche doit donc recevoir le contexte local avant de reprendre.
        self.assertFalse(self.server.current.resumed)
        self.assertTrue(self.gemini.context_seeded)
        seed_events = [
            event for event in self.server.wire
            if event.generation == self.gemini.session_generation and event.kind == "client_content"
        ]
        self.assertEqual(len(seed_events), 1, "le checkpoint périmé exige un rejeu unique")
        # ... et la voix comme le texte fonctionnent de nouveau, avec le contexte.
        self.assertEqual(await self.harness.speak("Quel est mon prénom ?"), "Ton prénom est Ada.")
        self.assertEqual(await self._text_turn("Comment je m'appelle ?"), "Ton prénom est Ada.")
        self.assertEqual(len(self._stalls()), 1, "aucune reprise supplémentaire")

    async def test_regeneration_without_any_answer_recovers(self):
        """R3 : génération vide confirmée, puis la relance reste sans réponse."""
        generation = self.gemini.session_generation
        session = self.server.current
        self._swallow_client_content()
        await session._emit(msg_user_transcript("Quelle heure est-il ?"))
        await session._emit(msg_turn_complete())  # génération vide -> relance
        self.assertTrue(await self._wait(lambda: self.gemini._regeneration_pending))
        self.assertEqual(self.gemini._can_send_now(), (False, "relance de generation en cours"))
        await self._assert_recovered(generation)
        self.assertEqual(self._stalls()[0]["reason"], "relance de génération sans réponse")
        self.assertIn(("user", "Quelle heure est-il ?", True), self._messages(),
                      "demande conservée et marquée en échec")
        self.assertEqual(await self._text_turn("Comment je m'appelle ?"), "Je ne sais pas, tu ne me l'as pas dit.")

    async def test_transcribed_voice_request_without_any_answer_recovers(self):
        """Demande vocale transcrite, puis plus rien : le texte n'est plus refusé indéfiniment."""
        generation = self.gemini.session_generation
        await self.server.current._emit(msg_user_transcript("Bonjour Jarvis, je m'appelle Ada"))
        self.assertTrue(await self._wait(lambda: bool(self.gemini._turn_user_text)))
        with self.assertRaises(TextSubmitError) as ctx:
            await self.gemini.submit_text("Tu m'entends ?")
        self.assertEqual(str(ctx.exception), "Une demande vocale est en cours de traitement.")
        await self._assert_recovered(generation)
        self.assertEqual(self._stalls()[0]["reason"], "demande vocale sans réponse")
        self.assertIn(("user", "Bonjour Jarvis, je m'appelle Ada", True), self._messages())
        self.assertFalse(self.interrupted, "aucune réponse n'avait commencé : pas d'évènement « interrompu »")
        # Contexte rejoué dans la nouvelle session (pas de handle) : le prénom est connu.
        self.assertEqual(await self._text_turn("Comment je m'appelle ?"), "Ton prénom est Ada.")


class NoFalseStallTests(_Base):
    async def test_long_local_tool_is_never_a_stall(self):
        calls: list[float] = []

        def slow_tool():
            calls.append(time.monotonic())
            time.sleep(STALL * 3)
            return {"success": True, "message": "terminé"}

        self.server.next_tool_script = (r"outil lent", "outil_lent", {})
        self.server.tool_continuation = "L'outil lent a terminé."
        generation = self.gemini.session_generation
        with mock.patch.dict(gemini_module.TOOL_FUNCTIONS, {"outil_lent": slow_tool}):
            answer = await self.harness.speak("Lance l'outil lent")
        self.assertEqual(len(calls), 1)
        self.assertEqual(answer, "L'outil lent a terminé.")
        await asyncio.sleep(STALL * 2)
        self.assertEqual(self._stalls(), [])
        self.assertEqual(self.gemini.session_generation, generation)

    async def test_slow_but_alive_answer_is_never_a_stall(self):
        generation = self.gemini.session_generation
        session = self.server.current
        before = len(self.harness.turns)
        await session._emit(msg_user_transcript("Raconte une longue histoire"))
        await session._emit(_Message(_ServerContent(model_text="Il était", audio=b"\x00\x01" * 64)))
        for index in range(10):  # 10 × 0,15 s = 1,5 s > délai, mais jamais 0,4 s de silence
            await asyncio.sleep(STALL * 0.375)
            await session._emit(_Message(_ServerContent(model_text=f"suite {index}", audio=b"\x00\x01" * 64)))
        await session._emit(msg_turn_complete())
        self.assertTrue(await self._wait(lambda: len(self.harness.turns) > before))
        self.assertEqual(self._stalls(), [])
        self.assertEqual(self.gemini.session_generation, generation)
        self.assertFalse(self.gemini.speaking)

    async def test_idle_session_is_never_restarted(self):
        generation = self.gemini.session_generation
        await self.harness.speak("Bonjour")
        await asyncio.sleep(STALL * 4)
        self.assertEqual(self._stalls(), [])
        self.assertEqual(self.gemini.session_generation, generation)
        self.assertTrue(self.gemini.can_send())


class TextTurnTimeoutTests(_Base):
    stall_timeout = 5.0  # isole le filet texte du chien de garde général
    text_timeout = 0.3

    async def test_unanswered_text_with_live_server_closes_the_turn_without_reconnecting(self):
        generation = self.gemini.session_generation
        session = self.server.current
        self._swallow_client_content()
        await self.gemini.submit_text("Retiens ce code : ORION-4")
        self.assertTrue(self.gemini._text_turn_pending)
        for _ in range(6):  # le serveur reste vivant (messages sans réponse au texte)
            await asyncio.sleep(0.1)
            await session._emit(_Message(session_resumption_update=_ResumptionUpdate(None, False)))
        self.assertTrue(await self._wait(lambda: not self.gemini._text_turn_pending, 2.0))
        self.assertTrue(self.gemini._turn_closed)
        self.assertFalse(self.harness.conversation.has_open_turn())
        self.assertIn(("user", "Retiens ce code : ORION-4", True), self._messages())
        self.assertTrue(self.resolved)
        self.assertIn("TEXT_TURN_TIMEOUT", self._kinds())
        self.assertEqual(self._stalls(), [])
        self.assertEqual(self.gemini.session_generation, generation, "aucune reconnexion : le serveur est vivant")
        self.assertTrue(self.gemini.can_send())
        # AVANT le correctif : refus « Une demande vocale est en cours de traitement ».
        result = await self.gemini.submit_text("Message suivant")
        self.assertTrue(result["accepted"])

    async def test_unanswered_text_with_silent_server_restarts_the_session(self):
        generation = self.gemini.session_generation
        self._swallow_client_content()
        await self.gemini.submit_text("Retiens ce code : ORION-4")
        await self._assert_recovered(generation)
        self.assertEqual(self._stalls()[0]["reason"], "message écrit sans réponse")
        self.assertIn(("user", "Retiens ce code : ORION-4", True), self._messages())
        # Le message reste référençable après la reprise (rejeu du contexte).
        self.assertEqual(await self._text_turn("Quel code t'ai-je donné ?"), "Le code est ORION-4.")


if __name__ == "__main__":
    unittest.main()
