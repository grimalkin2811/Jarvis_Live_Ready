"""Investigation trace-first — S6 reste FAIL malgré EMPTY_GENERATION_RETRY
(validation réelle post-v1.7.5 ter).

Résumé du run réel fourni par l'utilisateur :

* S5 confirme bien ``SEED_COMMIT_CONFIRMED`` puis ``SESSION_READY
  context_seeded=True context_seed_confirmed=True``.
* S6 reçoit correctement la transcription utilisateur « Quel est mon
  prénom ? ».
* ``EMPTY_GENERATION_RETRY`` se déclenche DEUX fois (1 tentative initiale +
  2 relances = ``EMPTY_GENERATION_MAX_RETRIES`` par défaut).
* Les trois tentatives produisent une génération vide. La troisième (la
  dernière relance, épuisée) finit par ``TTS_START`` (un ``model_turn`` est
  bien arrivé, donc ``on_speaking``/``self.speaking=True`` se déclenchent)
  puis ``TURN_COMPLETE``, mais SANS ``GEMINI_ASSISTANT_TRANSCRIPT`` ni le
  moindre octet audio — le ``model_turn`` reçu n'avait aucune part utile.
* S6 reçoit donc la question mais une réponse « (sans transcription) » et
  échoue.

Ce module ne prétend PAS corriger ce comportement : il prouve, par des
tests déterministes, que ce fallthrough est le comportement ATTENDU et
DÉJÀ CORRECT de ``_receive_one_turn_cycle`` étant donné trois générations
RÉELLEMENT vides consécutives (aucun bug de bookkeeping des relances,
aucune confusion de ``turn_id``/``session_generation`` entre tentatives) —
cf. ``docs/RAPPORT_S6_PERSISTANCE_v1.7.5_quater.md`` pour l'investigation
complète (comparaison g1-t3 vs g2-t8, facteurs aggravants côté serveur
Gemini documentés par Google, et pourquoi « augmenter le nombre de
relances » ne réglerait rien).
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

from tests.live_harness import (  # noqa: E402
    _ModelTurn,
    _ServerContent,
    _Message,
    msg_turn_complete,
    msg_user_transcript,
)

from src.conversation import ConversationContext  # noqa: E402
from src.gemini_live import EMPTY_GENERATION_MAX_RETRIES, GeminiLive  # noqa: E402


class _ScriptedSession:
    """Un ``receive()`` = un appel = UN batch scripté (fidèle au vrai SDK,
    cf. ``tests/test_empty_generation_retry.py``)."""

    def __init__(self, batches: list[list[object]]) -> None:
        self._batches = list(batches)
        self.sent: list[tuple[object, bool]] = []
        self.receive_calls = 0
        # session_generation/turn identité : capturés à CHAQUE appel pour
        # vérifier qu'aucune relance ne change de session/tour en cours de
        # route (investigation point 5 : association turn_id/session).
        self.receive_call_contexts: list[tuple[int, int]] = []

    async def send_client_content(self, turns=None, turn_complete: bool = True) -> None:
        self.sent.append((turns, turn_complete))

    def receive(self):
        self.receive_calls += 1
        batch = self._batches.pop(0) if self._batches else []

        async def gen():
            for message in batch:
                yield message

        return gen()


def _make_gemini(**callbacks) -> GeminiLive:
    conversation = ConversationContext(max_turns=20, max_tokens=4096)
    return GeminiLive(
        key="test-key",
        model="gemini-2.5-flash-native-audio-preview-12-2025",
        user="Test",
        on_audio=lambda pcm: None,
        conversation=conversation,
        **callbacks,
    )


def msg_empty_model_turn() -> _Message:
    """Un ``model_turn`` arrive (déclenche ``TTS_START``/``on_speaking``)
    mais ses ``parts`` ne contiennent ni audio (``inline_data``) ni texte
    exploitable — exactement le signal observé sur la 3e tentative du run
    réel (``TTS_START`` puis ``TURN_COMPLETE`` sans
    ``GEMINI_ASSISTANT_TRANSCRIPT``)."""
    content = _ServerContent()
    content.model_turn = _ModelTurn([])
    return _Message(content)


class ThreeConsecutiveEmptyGenerationsMatchTheRealTraceTests(
    unittest.IsolatedAsyncioTestCase
):
    """Reproduit PRÉCISÉMENT le cycle g2-t8 observé en validation réelle :
    3 tentatives, toutes vides, la dernière avec un ``model_turn`` creux.
    """

    async def test_exact_real_trace_signature_g2_t8(self) -> None:
        batches = [
            # Tentative 1 (originale) : transcription utilisateur réelle,
            # puis turn_complete SANS la moindre miette de réponse.
            [msg_user_transcript("Quel est mon prénom ?"), msg_turn_complete()],
            # Tentative 2 (1re relance) : toujours rien.
            [msg_turn_complete()],
            # Tentative 3 (2e relance, dernière autorisée) : un model_turn
            # CREUX arrive (TTS_START se déclenche) puis turn_complete,
            # mais sans transcription ni audio -- signature exacte du run
            # réel fourni par l'utilisateur.
            [msg_empty_model_turn(), msg_turn_complete()],
        ]
        session = _ScriptedSession(batches)
        gemini = _make_gemini()
        gemini.session = session

        events: list[str] = []
        gemini.on_turn_complete = lambda: events.append("turn_complete")
        gemini.on_speaking = lambda: events.append("speaking")

        received_any = await gemini._receive_one_turn_cycle()

        self.assertTrue(received_any)
        # Exactement 1 tentative initiale + EMPTY_GENERATION_MAX_RETRIES
        # relances -- aucune relance supplémentaire au-delà de la borne,
        # même si le serveur reste systématiquement vide.
        self.assertEqual(session.receive_calls, 1 + EMPTY_GENERATION_MAX_RETRIES)
        self.assertEqual(len(session.sent), EMPTY_GENERATION_MAX_RETRIES)

        kinds = [e["kind"] for e in gemini.trace_events()]
        self.assertEqual(
            kinds.count("EMPTY_GENERATION_RETRY"), EMPTY_GENERATION_MAX_RETRIES
        )
        # TTS_START se déclenche bien sur la tentative finale (model_turn
        # creux reçu) -- EXACTEMENT comme dans le run réel -- sans que cela
        # ne soit jamais pris pour du contenu réel.
        self.assertIn("speaking", events)
        self.assertNotIn("GEMINI_ASSISTANT_TRANSCRIPT", kinds)

        # Le tour finit par se clore UNE fois, en dégradé : c'est le
        # comportement ATTENDU (pas un bug de bookkeeping) étant donné
        # trois générations RÉELLEMENT vides côté serveur. on_turn_complete
        # se déclenche car la question de l'utilisateur, elle, était bien
        # réelle (``pending_user_text`` non vide) : un consommateur (comme
        # ``RealVoiceLab``) reçoit donc ce tour avec une réponse vide --
        # EXACTEMENT le « (sans transcription) » constaté par S6, pas une
        # régression du filtre ``had_content`` (bug B) qui, lui, protège
        # contre les tours fantômes SANS question réelle.
        self.assertEqual(kinds.count("TURN_COMPLETE"), 1)
        self.assertEqual(events.count("turn_complete"), 1)

        messages = gemini.conversation.get_messages()
        self.assertEqual(len(messages), 1, "la question est versée, sans réponse inventée")

    async def test_turn_identity_is_stable_across_all_three_attempts(self) -> None:
        """Investigation point 5 : la relance ne doit JAMAIS réouvrir un
        nouveau tour ni changer de génération de session en cours de
        route -- les trois tentatives appartiennent au MÊME tour logique
        (g2-t8 dans le vocabulaire du run réel), confirmant qu'il ne
        s'agit pas d'une confusion turn_id/session_generation côté
        client."""
        batches = [
            [msg_user_transcript("Quel est mon prénom ?"), msg_turn_complete()],
            [msg_turn_complete()],
            [msg_empty_model_turn(), msg_turn_complete()],
        ]
        session = _ScriptedSession(batches)
        gemini = _make_gemini()
        gemini.session = session
        gemini.session_generation = 2  # simule la reconnexion S5 -> session g2

        turn_counter_snapshots: list[int] = []
        session_generation_snapshots: list[int] = []
        original_begin = gemini._begin_turn_if_needed

        def _spy_begin_turn():
            original_begin()
            turn_counter_snapshots.append(gemini.turn_counter)
            session_generation_snapshots.append(gemini.session_generation)

        gemini._begin_turn_if_needed = _spy_begin_turn

        await gemini._receive_one_turn_cycle()

        # _begin_turn_if_needed n'est appelé qu'UNE fois (sur la
        # transcription utilisateur de la tentative 1) : les relances
        # n'ont pas de transcription utilisateur propre, donc n'ouvrent
        # jamais un second tour -- le turn_counter ne doit PAS avancer
        # entre les 3 tentatives.
        self.assertEqual(turn_counter_snapshots, [1])
        self.assertEqual(session_generation_snapshots, [2])
        self.assertEqual(
            gemini.turn_id, "g2-t1",
            "les trois tentatives doivent partager la MÊME identité de tour",
        )


if __name__ == "__main__":
    unittest.main()
