"""Régression S6 (cause racine architecturale) — la relance
``EMPTY_GENERATION_RETRY`` ne doit JAMAIS rouvrir la porte micro de CETTE
session pendant qu'elle est en vol.

Contexte (voir CHANGELOG.md et docs/RAPPORT_RECONNEXION_PAR_TOUR_v1.7.4.md
§13.1) : la validation réelle montre, sur CHAQUE run qui échoue, la séquence
suivante pour le tour `g2-t8` (après une VRAIE reconnexion, question réelle
transcrite) :

    TTS_START
    LIVE_DIAGNOSTIC_FIELDS   generation_complete=True   (génération vide)
    EMPTY_GENERATION_RETRY   attempt=1/2
    [... AUDIO_SENT_TO_GEMINI speaking=False ...]        <-- PREUVE DU BUG
    MODEL_TURN_RECEIVED      (toujours vide)
    TTS_START
    LIVE_DIAGNOSTIC_FIELDS   generation_complete=True
    EMPTY_GENERATION_RETRY   attempt=2/2
    [... AUDIO_SENT_TO_GEMINI speaking=False ...]        <-- PREUVE DU BUG
    MODEL_TURN_RECEIVED      (toujours vide)
    TURN_COMPLETE            had_content=True has_model_content=False

Avant ce correctif, ``_receive_one_turn_cycle`` mettait ``self.speaking =
False`` juste avant de ré-envoyer le texte déjà transcrit via
``send_client_content`` (une relance texte, DANS LA MÊME session). Comme
``_can_send_now()``/``can_send()`` n'observent QUE ``self.speaking`` (et
``self._session_ready``/``tool_active``) pour décider si le pont micro peut
transmettre de l'audio temps réel, cela rouvrait exactement la porte que
``speaking=True`` tient fermée pendant une génération normale -- le pont
micro (qui tourne en continu, VAD serveur oblige, cf. §5 du rapport v1.7.4)
pouvait alors injecter de l'audio RÉEL dans la MÊME session PENDANT que le
serveur devait encore répondre à un ``clientContent`` texte de relance.
Mélanger une clôture de tour texte avec de l'audio temps réel qui arrive
pendant que le serveur doit encore y répondre est exactement le genre
d'ambiguïté de tour qui peut lui faire clore la génération suivante sans
contenu -- une relance qui, par construction, était censée CORRIGER une
génération vide pouvait ainsi elle-même entretenir le problème.

Correctif (``GeminiLive.__init__``/``_can_send_now``/``_receive_one_turn_cycle``,
``src/gemini_live.py``) : un drapeau dédié ``_regeneration_pending``, orthogonal
à ``self.speaking``, ferme ``can_send()`` pendant tout l'aller-retour d'une
relance -- jamais plus tôt qu'un tour normal ne le ferait, jamais moins
longtemps. Un barge-in RÉEL (``interrupt_active()``) continue de passer,
exactement comme pour ``speaking``.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

from tests.live_harness import (  # noqa: E402
    _ModelTurn,
    _Part,
    _ServerContent,
    _Message,
    msg_turn_complete,
    msg_user_transcript,
)

from src.conversation import ConversationContext  # noqa: E402
from src.gemini_live import EMPTY_GENERATION_MAX_RETRIES, GeminiLive  # noqa: E402


def _msg_text_only_model_turn() -> _Message:
    """``model_turn`` avec une part TEXTE (jamais lue par le code courant,
    cf. ``has_text_part``) mais SANS audio ni ``output_transcription`` --
    EXACTEMENT la forme observée sur CHAQUE tentative (pas seulement la
    dernière) dans la trace réelle g2-t8 (§13.1 du rapport)."""
    content = _ServerContent()
    content.model_turn = _ModelTurn([_Part(text="(generation vide)")])
    return _Message(content)


class _ScriptedSessionWithAudioProbe:
    """Comme ``_ScriptedSession`` (cf. ``tests/test_empty_generation_retry.py``),
    mais trace aussi les blocs audio réellement transmis au serveur -- c'est
    la seule façon de prouver, indépendamment de ``self.speaking``, qu'aucun
    octet n'a atteint le câble pendant une relance en vol.
    """

    def __init__(self, batches: list[list[object]]) -> None:
        self._batches = list(batches)
        self.sent: list[tuple[object, bool]] = []
        self.receive_calls = 0
        self.audio_sent: list[bytes] = []

    async def send_client_content(self, turns=None, turn_complete: bool = True) -> None:
        self.sent.append((turns, turn_complete))

    async def send_realtime_input(self, audio=None, **_kwargs) -> None:
        data = getattr(audio, "data", audio) or b""
        self.audio_sent.append(data)

    def receive(self):
        self.receive_calls += 1
        batch = self._batches.pop(0) if self._batches else []

        async def gen():
            for message in batch:
                yield message

        return gen()


def _make_gemini() -> GeminiLive:
    conversation = ConversationContext(max_turns=20, max_tokens=4096)
    return GeminiLive(
        key="test-key",
        model="gemini-2.5-flash-native-audio-preview-12-2025",
        user="Test",
        on_audio=lambda pcm: None,
        conversation=conversation,
    )


class MicGateStaysClosedDuringRetryTests(unittest.IsolatedAsyncioTestCase):
    """Reproduit EXACTEMENT la forme g2-t8 (model_turn texte-seul sur CHAQUE
    tentative, pas seulement la dernière) et prouve que ``can_send()``
    n'est JAMAIS vrai pendant les deux relances."""

    async def test_can_send_is_false_throughout_every_retry_round_trip(self) -> None:
        batches = [
            [msg_user_transcript("Quel est mon prénom ?"), _msg_text_only_model_turn(),
             msg_turn_complete()],
            [_msg_text_only_model_turn(), msg_turn_complete()],
            [_msg_text_only_model_turn(), msg_turn_complete()],
        ]
        session = _ScriptedSessionWithAudioProbe(batches)
        gemini = _make_gemini()
        gemini.session = session
        gemini._session_ready = True

        can_send_at_retry_time: list[bool] = []
        original_send_client_content = session.send_client_content

        async def _spy_send_client_content(turns=None, turn_complete=True):
            # Capturé au moment EXACT où la relance part sur le câble --
            # c'est précisément l'instant où la trace réelle montre
            # ``AUDIO_SENT_TO_GEMINI`` juste après.
            can_send_at_retry_time.append(gemini.can_send())
            await original_send_client_content(turns=turns, turn_complete=turn_complete)

        session.send_client_content = _spy_send_client_content

        received_any = await gemini._receive_one_turn_cycle()

        self.assertTrue(received_any)
        self.assertEqual(len(session.sent), EMPTY_GENERATION_MAX_RETRIES)
        self.assertEqual(
            can_send_at_retry_time, [False] * EMPTY_GENERATION_MAX_RETRIES,
            "can_send() doit être FAUX pendant l'envoi de CHAQUE relance -- "
            "sinon le pont micro peut transmettre de l'audio réel au serveur "
            "pendant qu'il doit encore répondre à cette relance (cause "
            "racine S6, cf. docstring du module)",
        )

        # Une fois le tour réellement clos (relances épuisées), la porte doit
        # se rouvrir normalement pour le tour suivant.
        self.assertTrue(gemini.can_send())
        self.assertFalse(gemini._regeneration_pending)

    async def test_stray_audio_during_retry_is_dropped_not_forwarded(self) -> None:
        """Simule le pont micro RÉEL : un bloc audio arrive pendant la
        fenêtre de relance (bruit ambiant, cf. §5 du rapport v1.7.4 -- le
        micro transmet en continu par conception). Avant ce correctif, ce
        bloc aurait été transmis au serveur EN PLUS de la relance texte ;
        avec le correctif, ``send_audio()`` le laisse tomber (même
        protection que ``can_send()==False`` pendant un tour normal)."""
        batches = [
            [msg_user_transcript("Quel est mon prénom ?"), _msg_text_only_model_turn(),
             msg_turn_complete()],
            [_msg_text_only_model_turn(), msg_turn_complete()],
        ]
        session = _ScriptedSessionWithAudioProbe(batches)
        gemini = _make_gemini()
        gemini.session = session
        gemini._session_ready = True

        original_send_client_content = session.send_client_content

        async def _mic_bridge_leak(turns=None, turn_complete=True):
            # Reproduit la concurrence réelle : le pont micro (thread audio)
            # planifie un envoi juste après que la relance a été écrite sur
            # le câble, pendant que le serveur n'a pas encore répondu.
            await original_send_client_content(turns=turns, turn_complete=turn_complete)
            await gemini.send_audio(b"\x00" * 10)

        session.send_client_content = _mic_bridge_leak

        await gemini._receive_one_turn_cycle()

        self.assertEqual(
            session.audio_sent, [],
            "aucun octet audio ne doit atteindre le serveur pendant qu'une "
            "relance de génération vide est en vol sur cette session",
        )
        kinds = [e["kind"] for e in gemini.trace_events()]
        self.assertIn(
            "AUDIO_SEND_DROPPED_STALE", kinds,
            "le bloc doit être explicitement tracé comme abandonné, pas "
            "silencieusement ignoré sans trace",
        )

    async def test_real_interruption_still_passes_during_regeneration_pending(self) -> None:
        """Le correctif ne doit PAS bloquer un VRAI barge-in pendant une
        relance : ``interrupt_active()`` garde la priorité, exactement comme
        pour ``speaking`` (cf. ``tests/test_turn_race.py``) -- sinon
        l'utilisateur ne pourrait plus jamais couper court à une relance qui
        s'éternise."""
        session = _ScriptedSessionWithAudioProbe([])
        gemini = _make_gemini()
        gemini.session = session
        gemini._session_ready = True

        # Simule l'état exact d'une relance en vol (sans passer par tout le
        # cycle de réception, pour isoler précisément ce comportement).
        gemini._regeneration_pending = True
        self.assertFalse(gemini.can_send(), "la porte doit être fermée en l'absence d'interruption")

        gemini.request_interrupt()
        self.assertTrue(
            gemini.can_send(),
            "un VRAI barge-in doit garder la priorité sur la fermeture de porte "
            "liée à une relance en vol, exactement comme pour `speaking`",
        )
        await gemini.send_audio(b"\x00" * 10)
        self.assertEqual(
            session.audio_sent, [b"\x00" * 10],
            "l'audio d'un barge-in réel doit être transmis même pendant "
            "`_regeneration_pending`",
        )


if __name__ == "__main__":
    unittest.main()
