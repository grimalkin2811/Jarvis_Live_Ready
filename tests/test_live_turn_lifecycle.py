"""Cycle de vie d'un tour Gemini Live face aux séquences RÉELLES du protocole.

Correctif PR #50 (validation Windows réelle du 2026-10-16, commit 28234b0) :
E01, E07, E09 et E10 échouaient par « aucune fin de tour 90 s après
l'énoncé ». Trace réelle : tour ouvert par la transcription, ``INTERRUPTION``
du serveur alors que Jarvis ne parlait pas, tour fermé,
``EMPTY_GENERATION_RETRY``, porte micro retenue, ``RESPONSE_STALLED``, reprise
de session, aucune réponse. Tous les énoncés en échec contenaient DEUX phrases
(« Quelle est la capitale de l'Espagne ? Réponds en un mot. ») : la VAD du
serveur clôt la parole à la pause, commence une génération, puis la deuxième
phrase l'interrompt — « interrupted > turn_complete » (référence Live API,
``generationComplete``). Jarvis prenait ce ``turn_complete``, qui appartient à
la génération ANNULÉE, pour une génération vide du tour en cours : relance
(``clientContent`` qui interrompt à son tour le serveur), micro fermé en pleine
phrase, serveur muet, chien de garde.

Ces tests utilisent les VRAIS types du SDK et sa VRAIE règle d'arrêt de
``receive()`` (``tests/sdk_live_fake.py``). Ils échouent sur 28234b0.
"""

from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest import mock

os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

from src import gemini_live as gl  # noqa: E402
from src.conversation import ROLE_ASSISTANT, ROLE_USER, ConversationContext  # noqa: E402
from tests import sdk_live_fake as fake  # noqa: E402

MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"


class _Lab:
    """Une ``GeminiLive`` réelle branchée sur une session factice, callbacks journalisés."""

    def __init__(self) -> None:
        self.events: list[str] = []
        self.turns: list[dict] = []
        self.current = {"user": "", "assistant": ""}
        self.conversation = ConversationContext(max_turns=40, max_tokens=8192)
        self.gemini = gl.GeminiLive(
            "cle-de-test", MODEL, "Test",
            on_audio=lambda pcm: None,
            on_turn_complete=self._complete,
            on_interrupted=lambda: self.events.append("interrupted"),
            on_turn_open=lambda: self.events.append("open"),
            on_turn_resolved=lambda: self.events.append("resolved"),
            on_user_transcript=lambda text, final=False: self.current.__setitem__("user", text),
            on_assistant_transcript=lambda text: self.current.__setitem__("assistant", text),
            conversation=self.conversation,
        )

    def _complete(self) -> None:
        self.events.append("complete")
        self.turns.append(dict(self.current))
        self.current = {"user": "", "assistant": ""}

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.gemini.trace_events()]

    def texts(self) -> list[tuple[str, str]]:
        return [(m.role, m.text) for m in self.conversation.get_messages()]


async def _until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return bool(predicate())


class _SessionTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.lab = _Lab()
        self.session = fake.SdkLikeSession()
        self.lab.gemini.session = self.session
        self.lab.gemini._loop = asyncio.get_running_loop()
        self.task = asyncio.create_task(self.lab.gemini._receive_loop())

    async def asyncTearDown(self) -> None:
        self.session.close()
        try:
            await asyncio.wait_for(self.task, 2)
        except Exception:
            self.task.cancel()
        await self.lab.gemini.close()

    async def settle(self) -> None:
        for _ in range(20):
            await asyncio.sleep(0.005)


class PauseBetweenTwoSentencesTests(_SessionTest):
    """E01/E07/E09/E10 : la VAD clôt la parole à la pause entre deux phrases."""

    async def _two_sentences(self, stale_status) -> None:
        self.session.push(fake.user("Quelle est la capitale de l'Espagne ?"))
        await self.settle()
        # La deuxième phrase commence : la génération (encore muette) est annulée.
        self.session.push(fake.interrupted(), fake.turn_complete(stale_status))
        await self.settle()
        self.session.push(fake.user("Réponds en un mot."))
        await self.settle()
        self.session.push(*fake.answer("Madrid."))
        await _until(lambda: "complete" in self.lab.events)
        await self.settle()

    async def test_no_regeneration_and_one_complete_turn_with_in_progress(self) -> None:
        await self._two_sentences(fake.IN_PROGRESS)
        self.assertNotIn("client_content", self.session.kinds(), "relance parasite envoyée au serveur")
        self.assertNotIn("EMPTY_GENERATION_RETRY", self.lab.kinds())
        self.assertNotIn("interrupted", self.lab.events, "rien n'était lu : aucune interruption à signaler")
        self.assertEqual(self.lab.events.count("complete"), 1)
        self.assertEqual(self.lab.turns[-1]["assistant"], "Madrid.")
        self.assertEqual(
            self.lab.texts(),
            [(ROLE_USER, "Quelle est la capitale de l'Espagne ? Réponds en un mot."), (ROLE_ASSISTANT, "Madrid.")],
        )
        snapshot = self.lab.gemini.engine_snapshot()
        self.assertFalse(snapshot["turn_open"])
        self.assertFalse(snapshot["regeneration_pending"])
        self.assertTrue(self.lab.gemini.can_send())

    async def test_same_without_interaction_status(self) -> None:
        """Serveur sans ``interaction_status`` : ``turn_complete`` clôt ``receive()``."""
        await self._two_sentences(None)
        self.assertNotIn("client_content", self.session.kinds())
        self.assertEqual(self.lab.events.count("complete"), 1)
        self.assertEqual(self.lab.turns[-1]["assistant"], "Madrid.")

    async def test_interruption_is_classified_in_the_trace(self) -> None:
        await self._two_sentences(fake.IN_PROGRESS)
        interruption = next(e for e in self.lab.gemini.trace_events() if e["kind"] == "INTERRUPTION")
        self.assertEqual(interruption["classification"], "generation_annulee_avant_reponse")
        self.assertEqual(interruption["origin"], "activite_vocale_serveur")
        last = self.lab.gemini.engine_snapshot()["last_interruption"]
        self.assertEqual(last["classification"], "generation_annulee_avant_reponse")


class RealBargeInTests(_SessionTest):
    async def test_new_turn_transcript_before_the_stale_turn_complete(self) -> None:
        """Barge-in réel : la transcription du nouveau tour précède le ``turn_complete``
        de la génération annulée — il ne doit ni la relancer ni clore le nouveau tour."""
        self.session.push(fake.user("Raconte une histoire."), fake.model_text("Il était une fois"),
                          fake.model_audio())
        await _until(lambda: self.lab.gemini.speaking)
        self.lab.gemini.request_interrupt()  # barge-in local (AudioIO)
        self.session.push(fake.interrupted(), fake.user("Stop."), fake.turn_complete(fake.IN_PROGRESS))
        await self.settle()
        self.session.push(*fake.answer("D'accord."))
        await _until(lambda: "complete" in self.lab.events)
        await self.settle()
        self.assertNotIn("client_content", self.session.kinds())
        self.assertEqual(self.lab.events.count("interrupted"), 1)
        self.assertEqual(self.lab.events.count("complete"), 1)
        self.assertEqual(self.lab.turns[-1], {"user": "Stop.", "assistant": "D'accord."})
        self.assertEqual(
            self.lab.texts(),
            [(ROLE_USER, "Raconte une histoire."), (ROLE_ASSISTANT, "Il était une fois"),
             (ROLE_USER, "Stop."), (ROLE_ASSISTANT, "D'accord.")],
        )
        interruption = next(e for e in self.lab.gemini.trace_events() if e["kind"] == "INTERRUPTION")
        self.assertEqual(interruption["classification"], "interruption_reponse")
        self.assertEqual(interruption["origin"], "barge_in_local")


class EmptyTurnCompleteTests(_SessionTest):
    async def test_in_progress_means_more_output_follows(self) -> None:
        self.session.push(fake.user("Combien font deux et deux ?"), fake.turn_complete(fake.IN_PROGRESS))
        await self.settle()
        self.assertNotIn("client_content", self.session.kinds())
        self.assertTrue(self.lab.gemini.engine_snapshot()["turn_open"], "la réponse est encore attendue")
        self.session.push(*fake.answer("Quatre."))
        await _until(lambda: "complete" in self.lab.events)
        self.assertNotIn("EMPTY_GENERATION_RETRY", self.lab.kinds())
        self.assertEqual(self.lab.turns, [{"user": "Combien font deux et deux ?", "assistant": "Quatre."}])

    async def test_waiting_for_input_does_not_force_an_answer(self) -> None:
        self.session.push(fake.user("Je voudrais"), fake.turn_complete(fake.IDLE, waiting_for_input=True))
        await _until(lambda: "complete" in self.lab.events)
        await self.settle()
        self.assertNotIn("client_content", self.session.kinds(), "le modèle attend la suite : pas de relance")
        self.assertFalse(self.lab.gemini.engine_snapshot()["turn_open"])
        self.assertTrue(self.lab.gemini.can_send())

    async def test_real_empty_generation_is_still_retried_after_ending_the_mic_stream(self) -> None:
        """Défaut serveur documenté (génération vide, IDLE) : la relance reste, et le
        flux micro est clos AVANT (``audio_stream_end``) pour ne pas laisser la VAD
        du serveur au milieu d'une activité."""
        await self.lab.gemini.send_audio(b"\x00\x00" * 1280)
        self.session.push(fake.user("Quel temps fait-il ?"), fake.turn_complete(fake.IDLE))
        await _until(lambda: "client_content" in self.session.kinds())
        kinds = self.session.kinds()
        self.assertIn("audio_stream_end", kinds)
        self.assertLess(kinds.index("audio_stream_end"), kinds.index("client_content"))
        self.assertEqual(self.session.client_texts(), ["Quel temps fait-il ?"])
        self.session.push(*fake.answer("Il fait beau."))
        await _until(lambda: "complete" in self.lab.events)
        self.assertEqual(self.lab.turns[-1]["assistant"], "Il fait beau.")
        self.assertFalse(self.lab.gemini.engine_snapshot()["regeneration_pending"])


class TextTurnTests(_SessionTest):
    async def test_text_ends_the_open_mic_stream_before_its_client_content(self) -> None:
        await self.lab.gemini.send_audio(b"\x00\x00" * 1280)
        await self.lab.gemini.submit_text("Bonjour")
        kinds = self.session.kinds()
        self.assertEqual(kinds[-2:], ["audio_stream_end", "client_content"])

    async def test_text_turn_survives_the_interruption_its_own_client_content_causes(self) -> None:
        """Le ``clientContent`` du texte interrompt une génération en cours (protocole) :
        le tour écrit reste ouvert et reçoit SA réponse."""
        await self.lab.gemini.submit_text("Rappelle-moi le code.")
        self.session.push(fake.interrupted(), fake.turn_complete(fake.IN_PROGRESS))
        await self.settle()
        self.assertTrue(self.lab.gemini.engine_snapshot()["text_turn_pending"])
        self.session.push(*fake.answer("Le code est ORION-4."))
        await _until(lambda: "complete" in self.lab.events)
        await self.settle()
        self.assertEqual(self.session.client_texts(), ["Rappelle-moi le code."], "aucune relance")
        self.assertEqual(self.lab.turns, [{"user": "Rappelle-moi le code.", "assistant": "Le code est ORION-4."}])
        self.assertNotIn("interrupted", self.lab.events)

    async def test_text_watchdog_of_a_previous_session_never_aborts_a_new_text(self) -> None:
        gemini = self.lab.gemini
        with mock.patch.object(gl, "TEXT_TURN_TIMEOUT_SECONDS", 0.3):
            await gemini.submit_text("Premier message")  # filet armé à 0,3 s
        # La session meurt avant de répondre ; une nouvelle session prend le relais.
        gemini._fail_open_turn("erreur de session")
        gemini.session_generation += 1
        await asyncio.sleep(0.1)
        await gemini.submit_text("Second message")  # filet normal (30 s)
        await asyncio.sleep(0.35)  # le filet du PREMIER message expire ici
        self.assertTrue(gemini.engine_snapshot()["text_turn_pending"], "le second message a été clos à tort")
        self.assertNotIn("RESPONSE_STALLED", self.lab.kinds())
        self.assertNotIn("TEXT_TURN_TIMEOUT", self.lab.kinds())


class TurnStateAfterSessionLossTests(_SessionTest):
    async def test_first_voice_turn_of_the_next_session_is_opened_and_watched(self) -> None:
        gemini = self.lab.gemini
        self.session.push(fake.user("Raconte une histoire."), fake.model_text("Il était"), fake.model_audio())
        await _until(lambda: gemini.speaking)
        gemini._fail_open_turn("erreur de session")  # coupure en pleine réponse
        self.lab.events.clear()
        self.session.push(fake.user("Tu m'entends ?"))
        await self.settle()
        self.assertEqual(self.lab.events, ["open"], "le nouveau tour n'a pas été annoncé")
        self.assertIn("TURN_OPEN", [e["kind"] for e in gemini.trace_events()][-3:])
        self.assertEqual(gemini._pending_response_reason(), "demande vocale sans réponse",
                         "une demande bloquée après reconnexion échapperait au chien de garde")


class ReconnectionContextTests(unittest.IsolatedAsyncioTestCase):
    """E08 : voix → coupure réseau (sans reprise) → texte, la réponse du modèle au
    rejeu de contexte arrivant APRÈS l'ouverture de la porte (historique long)."""

    async def asyncSetUp(self) -> None:
        self.lab = _Lab()
        self.server = fake.ProtocolServer(seed_reply_delay=0.5)
        self.lab.gemini.client = self.server.client()
        self.patch = mock.patch.object(gl, "SEED_COMMIT_TIMEOUT_SECONDS", 0.2)
        self.patch.start()
        self.task = None

    async def asyncTearDown(self) -> None:
        self.patch.stop()
        await self._stop()

    async def _start(self) -> None:
        await self.lab.gemini.connect()
        self.task = asyncio.create_task(self.lab.gemini.receive_loop())

    async def _stop(self) -> None:
        gemini = self.lab.gemini
        if gemini.session is not None:
            await gemini._shutdown_session()
        if self.task is not None:
            try:
                await asyncio.wait_for(self.task, 2)
            except BaseException:
                pass
            self.task = None
        await gemini.close()

    async def _cut(self, *, keep_handle: bool = False) -> None:
        if not keep_handle:
            self.lab.gemini.resumption_handle = None
        await self._stop()
        await self._start()

    def _answer_to(self, before: int, text: str) -> dict | None:
        # La réponse à une demande est le tour qui CONTIENT cette demande : la
        # réponse du modèle au rejeu de contexte (tour sans demande) n'en est
        # pas une, même si elle se termine pendant l'attente.
        return next((t for t in self.lab.turns[before:] if t["user"] == text), None)

    async def _voice(self, text: str) -> dict:
        before = len(self.lab.turns)
        assert await _until(self.lab.gemini.can_send, 5)
        self.server.voice_turn(self.lab.gemini.session, text)
        self.assertTrue(await _until(lambda: self._answer_to(before, text) is not None, 5),
                        f"pas de réponse à {text!r}")
        return self._answer_to(before, text)

    async def _text(self, text: str) -> dict:
        before = len(self.lab.turns)
        await self.lab.gemini.submit_text(text)
        self.assertTrue(await _until(lambda: self._answer_to(before, text) is not None, 5),
                        f"pas de réponse à {text!r}")
        return self._answer_to(before, text)

    async def test_voice_then_reconnection_then_text(self) -> None:
        await self._start()
        await self._voice("Retiens le code ORION-4.")
        await self._cut()
        turn = await self._text("Rappelle-moi le code.")
        self.assertEqual(turn["user"], "Rappelle-moi le code.")
        self.assertIn("ORION-4", turn["assistant"])
        session = self.server.sessions[-1]
        self.assertEqual(session.client_texts().count("Rappelle-moi le code."), 1, "texte relancé")
        self.assertEqual(self.server.interruptions_by_client, 0, "le texte a coupé la réponse au rejeu")

    async def test_text_then_reconnection_then_voice(self) -> None:
        await self._start()
        await self._text("Je m'appelle Ada.")
        await self._cut()
        turn = await self._voice("Comment je m'appelle ?")
        self.assertIn("Ada", turn["assistant"])

    async def test_several_successive_reconnections_keep_one_copy_of_each_message(self) -> None:
        await self._start()
        await self._voice("Retiens le code ORION-4.")
        await self._cut()
        intermediate = await self._text("Je m'appelle Ada.")
        self.assertEqual(intermediate, {"user": "Je m'appelle Ada.", "assistant": "D'accord."})
        await self._cut()
        await self._cut(keep_handle=True)
        turn = await self._text("Rappelle-moi le code.")
        self.assertIn("ORION-4", turn["assistant"])
        self.assertIn("Ada", (await self._voice("Comment je m'appelle ?"))["assistant"])
        users = [t for r, t in self.lab.texts() if r == ROLE_USER]
        self.assertEqual(users.count("Retiens le code ORION-4."), 1)
        self.assertEqual(users.count("Je m'appelle Ada."), 1)
        self.assertEqual(users.count("Rappelle-moi le code."), 1)
        self.assertFalse(self.lab.gemini.engine_snapshot()["turn_open"])


if __name__ == "__main__":
    unittest.main()
