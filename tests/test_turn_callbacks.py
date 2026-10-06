"""Régression v1.7.5 ter — bugs A et B révélés par une validation réelle
(Windows, vraie clé Gemini) après le correctif S6 (v1.7.5 bis) :

* **Bug A** (``src/audio.py``) : ``AudioIO`` pouvait rendormir Jarvis (ou
  laisser expirer la fenêtre de conversation) alors que Gemini était encore
  en train de générer sa réponse, ce qui faisait perdre silencieusement
  toute question de relance posée sans mot-clé de réveil juste après. La
  correction ajoute une paire de callbacks ``on_turn_open``/
  ``on_turn_resolved`` sur ``GeminiLive`` qui annoncent, indépendamment du
  contenu produit, qu'un VRAI tour est ouvert puis résolu — ``AudioIO`` s'en
  sert pour suspendre son minuteur pendant que Gemini réfléchit encore
  (cf. ``tests/test_audio_turn_pending.py`` pour le volet ``AudioIO``).

* **Bug B** (``src/gemini_live.py``) : la traîne tardive d'un tour de rejeu
  de contexte abandonné (après un ``SEED_COMMIT_TIMEOUT``) pouvait arriver
  bien après l'ouverture dégradée de la porte micro et se faire passer,
  via ``on_interrupted``/``on_turn_complete``, pour la fin du VRAI tour en
  cours — un consommateur qui attend « le prochain tour terminé » recevait
  ce tour fantôme à la place de la vraie réponse. Ce module vérifie que
  ``on_turn_open`` ne se déclenche jamais pour ce tour fantôme (il n'a pas
  de transcription utilisateur) et que ``on_interrupted``/``on_turn_complete``
  restent silencieux pour lui, tout en continuant à fonctionner normalement
  pour le VRAI tour suivant.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

from tests.live_harness import (  # noqa: E402
    msg_interrupted,
    msg_model_transcript,
    msg_turn_complete,
    msg_user_transcript,
)

from src.conversation import ConversationContext  # noqa: E402
from src.gemini_live import GeminiLive  # noqa: E402


class _ScriptedSession:
    """Un ``receive()`` = un appel = UN batch scripté (fidèle au vrai SDK,
    cf. ``tests/test_empty_generation_retry.py``)."""

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


class TurnOpenResolvedHooksFireForRealTurnsTests(unittest.IsolatedAsyncioTestCase):
    """Un VRAI tour (texte utilisateur transcrit) doit toujours déclencher
    exactement une paire ``on_turn_open``/``on_turn_resolved``, et dans cet
    ordre — c'est le signal dont ``AudioIO`` a besoin pour ne jamais
    rendormir Jarvis pendant que la réponse arrive encore (bug A)."""

    async def test_open_then_resolved_for_a_real_answered_turn(self) -> None:
        events: list[str] = []
        session = _ScriptedSession([
            [
                msg_user_transcript("Quelle heure est-il ?"),
                msg_model_transcript("Il est midi.", audio=b"\x01\x02"),
                msg_turn_complete(),
            ],
        ])
        gemini = _make_gemini(
            on_turn_open=lambda: events.append("open"),
            on_turn_resolved=lambda: events.append("resolved"),
            on_turn_complete=lambda: events.append("complete"),
        )
        gemini.session = session

        received_any = await gemini._receive_one_turn_cycle()

        self.assertTrue(received_any)
        self.assertEqual(events, ["open", "resolved", "complete"])


class TurnOpenNeverFiresForContentlessSeedReplayTests(unittest.IsolatedAsyncioTestCase):
    """Le tour de rejeu de contexte n'a pas de transcription utilisateur :
    ``on_turn_open`` ne se déclenche donc jamais pour lui (seule une
    transcription ``input_transcription`` ouvre un tour, cf.
    ``_begin_turn_if_needed``). Par construction, ``_finish_turn`` ne
    déclenche alors pas non plus ``on_turn_resolved`` pour ce non-tour
    (déduplication ``already_closed`` : il n'y avait rien à résoudre côté
    ``AudioIO``, qui n'a jamais été suspendu faute de ``on_turn_open``) --
    et ``on_turn_complete`` doit rester silencieux dans tous les cas."""

    async def test_no_open_no_resolved_for_a_bare_turn_complete(self) -> None:
        events: list[str] = []
        session = _ScriptedSession([[msg_turn_complete()]])
        gemini = _make_gemini(
            on_turn_open=lambda: events.append("open"),
            on_turn_resolved=lambda: events.append("resolved"),
            on_turn_complete=lambda: events.append("complete"),
        )
        gemini.session = session

        received_any = await gemini._receive_one_turn_cycle()

        self.assertTrue(received_any)
        self.assertEqual(
            events, [],
            "pas de transcription -> pas de on_turn_open ; sans ouverture "
            "préalable il n'y a rien à résoudre pour AudioIO (jamais suspendu), "
            "et on_turn_complete ne doit pas se faire passer pour un vrai échange",
        )


class StaleTailDoesNotMasqueradeAsRealTurnCompletionTests(unittest.IsolatedAsyncioTestCase):
    """Reproduction directe du bug B : la traîne tardive d'un tour abandonné
    arrive comme ``interrupted`` suivi d'un ``turn_complete``, tous deux sans
    la moindre transcription ni contenu. Elle ne doit JAMAIS déclencher
    ``on_interrupted``/``on_turn_complete`` — mais le VRAI tour suivant,
    dans un nouveau cycle, doit continuer à fonctionner normalement."""

    async def test_empty_interrupted_then_turn_complete_is_silent(self) -> None:
        events: list[str] = []
        session = _ScriptedSession([
            # Traîne tardive, totalement vide : ni texte utilisateur, ni le
            # moindre octet audio/transcription assistant.
            [msg_interrupted(), msg_turn_complete()],
        ])
        gemini = _make_gemini(
            on_turn_open=lambda: events.append("open"),
            on_turn_resolved=lambda: events.append("resolved"),
            on_turn_complete=lambda: events.append("complete"),
            on_interrupted=lambda: events.append("interrupted"),
        )
        gemini.session = session

        received_any = await gemini._receive_one_turn_cycle()

        self.assertTrue(received_any)
        self.assertNotIn("complete", events)
        self.assertNotIn("interrupted", events)
        kinds = [e["kind"] for e in gemini.trace_events()]
        self.assertIn("INTERRUPTION_CALLBACK_SUPPRESSED_EMPTY", kinds)
        self.assertIn("TURN_COMPLETE_CALLBACK_SUPPRESSED_EMPTY", kinds)

    async def test_real_turn_after_a_stale_empty_tail_still_completes_normally(self) -> None:
        events: list[str] = []
        gemini = _make_gemini(
            on_turn_open=lambda: events.append("open"),
            on_turn_resolved=lambda: events.append("resolved"),
            on_turn_complete=lambda: events.append("complete"),
            on_interrupted=lambda: events.append("interrupted"),
        )

        # 1er cycle : la traîne fantôme, totalement silencieuse (aucun tour
        # n'a jamais été ouvert pour elle, donc rien à résoudre non plus).
        gemini.session = _ScriptedSession([[msg_interrupted(), msg_turn_complete()]])
        await gemini._receive_one_turn_cycle()
        self.assertEqual(events, [])

        # 2e cycle : le VRAI tour suivant doit être signalé normalement —
        # c'est précisément le cas que le bug B faisait manquer (un
        # consommateur qui attendait « le prochain tour terminé » recevait
        # le tour fantôme ci-dessus à la place).
        events.clear()
        gemini.session = _ScriptedSession([
            [
                msg_user_transcript("Et demain ?"),
                msg_model_transcript("Il fera beau.", audio=b"\x03"),
                msg_turn_complete(),
            ],
        ])
        await gemini._receive_one_turn_cycle()
        self.assertEqual(events, ["open", "resolved", "complete"])


if __name__ == "__main__":
    unittest.main()
