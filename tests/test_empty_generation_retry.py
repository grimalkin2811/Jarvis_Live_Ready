"""Régression v1.7.5 bis — S6 : génération vide confirmée côté serveur Gemini.

Contexte (voir docs/RAPPORT_RECONNEXION_PAR_TOUR_v1.7.4.md et son addendum
v1.7.5) : la validation réelle (Windows, vraie clé Gemini) a montré un
scénario S6 où Jarvis reste MUET après une VRAIE reconnexion alors que
``context_seeded``/``context_seed_confirmed`` sont corrects et que la
transcription utilisateur ("Quel est mon prénom ?") est bien reçue :
``turn_complete`` arrive sans la moindre miette de réponse (ni audio, ni
``output_transcription``). Cause racine confirmée par Google comme un bug
serveur connu, pas un défaut de construction du rejeu de contexte côté
Jarvis (issue googleapis/python-genai#2117 : « model sometimes returns an
empty turn_complete with no content »; pattern similaire déjà contourné par
``livekit/agents#4249``/``agents-js#1450``).

Correctif (``GeminiLive._receive_one_turn_cycle``) : une génération
totalement vide pour un VRAI tour utilisateur (texte transcrit non vide)
n'est plus acceptée comme réponse finale — elle est relancée en ré-envoyant
le même texte utilisateur déjà transcrit, borné par
``EMPTY_GENERATION_MAX_RETRIES``. Le tour de rejeu de contexte lui-même
(``_await_seed_commit``) n'a jamais de transcription utilisateur associée et
n'est donc jamais concerné par cette relance — une génération vide y reste
acceptée telle quelle (comportement 2.x documenté, cf.
``TestSeedCommitTimesOutWithoutBlockingForever``).

Ces tests utilisent un faux serveur MINIMAL et volontairement fidèle au
vrai SDK confirmé par Google (issue googleapis/python-genai#1224) :
``session.receive()`` se termine NATURELLEMENT dès qu'un message
``turn_complete`` a été délivré (un appel ``receive()`` = au plus un tour).
Chaque relance doit donc se traduire par un NOUVEL appel à ``receive()``,
pas par la poursuite de la boucle ``async for`` déjà épuisée -- exactement
ce que ce module vérifie.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

from tests.live_harness import (  # noqa: E402
    msg_model_transcript,
    msg_turn_complete,
    msg_user_transcript,
)

from src.conversation import ROLE_ASSISTANT, ROLE_USER, ConversationContext  # noqa: E402
from src.gemini_live import EMPTY_GENERATION_MAX_RETRIES, GeminiLive  # noqa: E402


class _ScriptedSession:
    """Un ``receive()`` = un appel = UN batch scripté, fidèle à la fin
    naturelle de ``session.receive()`` au ``turn_complete`` (confirmé par
    Google, issue googleapis/python-genai#1224) : chaque relance DOIT donc
    obtenir un nouveau batch via un nouvel appel à ``receive()``.
    """

    def __init__(self, batches: list[list[object]]) -> None:
        self._batches = list(batches)
        self.sent: list[tuple[object, bool]] = []
        self.receive_calls = 0

    async def send_client_content(self, turns=None, turn_complete: bool = True) -> None:
        self.sent.append((turns, turn_complete))

    def receive(self):
        self.receive_calls += 1
        batch = self._batches.pop(0) if self._batches else []

        async def gen():
            for message in batch:
                yield message

        return gen()


def _make_gemini(seed_messages: bool = False) -> GeminiLive:
    conversation = ConversationContext(max_turns=20, max_tokens=4096)
    gemini = GeminiLive(
        key="test-key",
        model="gemini-2.5-flash-native-audio-preview-12-2025",
        user="Test",
        on_audio=lambda pcm: None,
        conversation=conversation,
    )
    return gemini


class EmptyGenerationRetrySucceedsTests(unittest.IsolatedAsyncioTestCase):
    """Cas nominal S6 : la première génération est vide, la relance réussit."""

    async def test_retry_is_sent_and_the_real_answer_is_eventually_delivered(self) -> None:
        session = _ScriptedSession([
            # 1er appel receive() : transcription utilisateur réelle, puis
            # turn_complete SANS la moindre miette de réponse (bug serveur).
            [msg_user_transcript("Quel est mon prénom ?"), msg_turn_complete()],
            # 2e appel receive() (après la relance) : réponse réelle.
            [msg_model_transcript("Tu t'appelles Simon.", audio=b"\x01\x02"),
             msg_turn_complete()],
        ])
        gemini = _make_gemini()
        gemini.session = session

        turn_complete_calls = []
        gemini.on_turn_complete = lambda: turn_complete_calls.append(1)

        received_any = await gemini._receive_one_turn_cycle()

        self.assertTrue(received_any)
        self.assertEqual(session.receive_calls, 2,
                          "la relance doit rouvrir un NOUVEL appel receive(), "
                          "pas réutiliser la boucle déjà épuisée")

        # Une seule relance a été envoyée, avec le texte déjà transcrit.
        self.assertEqual(len(session.sent), 1)
        turns, turn_complete_flag = session.sent[0]
        self.assertTrue(turn_complete_flag)
        self.assertEqual(turns, [{"role": "user", "parts": [{"text": "Quel est mon prénom ?"}]}])

        kinds = [e["kind"] for e in gemini.trace_events()]
        self.assertEqual(kinds.count("EMPTY_GENERATION_RETRY"), 1)
        self.assertEqual(kinds.count("TURN_COMPLETE"), 1,
                          "la génération vide ne doit JAMAIS produire son "
                          "propre TURN_COMPLETE — seule la relance réussie "
                          "doit clore le tour")

        # on_turn_complete n'est appelé qu'une fois, pour la VRAIE réponse.
        self.assertEqual(turn_complete_calls, [1])

        # Le contexte local ne contient que l'échange réel, sans trace de
        # la tentative avortée.
        messages = gemini.conversation.get_messages()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].role, ROLE_USER)
        self.assertEqual(messages[0].text, "Quel est mon prénom ?")
        self.assertEqual(messages[1].role, ROLE_ASSISTANT)
        self.assertEqual(messages[1].text, "Tu t'appelles Simon.")


class EmptyGenerationRetryBoundedTests(unittest.IsolatedAsyncioTestCase):
    """Le bug serveur peut persister : la relance ne doit JAMAIS boucler
    indéfiniment — elle est bornée par ``EMPTY_GENERATION_MAX_RETRIES`` puis
    le tour est clos (dégradé, réponse vide) comme avant le correctif."""

    async def test_retries_are_capped_and_the_turn_eventually_closes(self) -> None:
        # 1 batch initial + EMPTY_GENERATION_MAX_RETRIES relances, TOUS vides.
        batches = [[msg_user_transcript("Quelle heure est-il ?"), msg_turn_complete()]]
        batches += [[msg_turn_complete()] for _ in range(EMPTY_GENERATION_MAX_RETRIES)]
        session = _ScriptedSession(batches)
        gemini = _make_gemini()
        gemini.session = session

        turn_complete_calls = []
        gemini.on_turn_complete = lambda: turn_complete_calls.append(1)

        received_any = await gemini._receive_one_turn_cycle()

        self.assertTrue(received_any)
        self.assertEqual(session.receive_calls, 1 + EMPTY_GENERATION_MAX_RETRIES)
        self.assertEqual(len(session.sent), EMPTY_GENERATION_MAX_RETRIES,
                          "exactement EMPTY_GENERATION_MAX_RETRIES relances, "
                          "jamais plus (pas de boucle infinie)")

        kinds = [e["kind"] for e in gemini.trace_events()]
        self.assertEqual(kinds.count("EMPTY_GENERATION_RETRY"), EMPTY_GENERATION_MAX_RETRIES)
        self.assertEqual(kinds.count("TURN_COMPLETE"), 1,
                          "le tour finit par se clore UNE fois, en dégradé")
        self.assertEqual(turn_complete_calls, [1])

        # Dégradé : la question est quand même versée au contexte (comme
        # avant ce correctif), mais sans réponse assistant inventée.
        messages = gemini.conversation.get_messages()
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].role, ROLE_USER)


class SeedReplayTurnIsNeverRetriedTests(unittest.IsolatedAsyncioTestCase):
    """Le tour de rejeu de contexte (``_await_seed_commit``) n'a jamais de
    transcription utilisateur associée : une génération vide y reste
    acceptée telle quelle, sans déclencher la moindre relance."""

    async def test_empty_turn_without_user_transcript_is_not_retried(self) -> None:
        session = _ScriptedSession([[msg_turn_complete()]])
        gemini = _make_gemini()
        gemini.session = session

        turn_complete_calls = []
        gemini.on_turn_complete = lambda: turn_complete_calls.append(1)

        received_any = await gemini._receive_one_turn_cycle()

        self.assertTrue(received_any)
        self.assertEqual(session.receive_calls, 1)
        self.assertEqual(session.sent, [], "aucune relance ne doit être envoyée")

        kinds = [e["kind"] for e in gemini.trace_events()]
        self.assertNotIn("EMPTY_GENERATION_RETRY", kinds)
        self.assertEqual(kinds.count("TURN_COMPLETE"), 1)
        self.assertEqual(turn_complete_calls, [1])


class EmptyGenerationRetrySkippedDuringInterruptionTests(unittest.IsolatedAsyncioTestCase):
    """Si l'utilisateur a déjà recommencé à parler (interruption locale
    active), relancer l'ancienne question n'a plus de sens — le tour se clôt
    normalement, sans relance, pour laisser la place au nouveau tour."""

    async def test_no_retry_while_a_local_interruption_is_active(self) -> None:
        session = _ScriptedSession([
            [msg_user_transcript("Quelle heure est-il ?"), msg_turn_complete()],
        ])
        gemini = _make_gemini()
        gemini.session = session
        gemini.request_interrupt()
        self.assertTrue(gemini.interrupt_active())

        received_any = await gemini._receive_one_turn_cycle()

        self.assertTrue(received_any)
        self.assertEqual(session.sent, [])
        kinds = [e["kind"] for e in gemini.trace_events()]
        self.assertNotIn("EMPTY_GENERATION_RETRY", kinds)
        self.assertEqual(kinds.count("TURN_COMPLETE"), 1)


if __name__ == "__main__":
    unittest.main()
