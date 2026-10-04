"""Reproduction/validation de bout en bout du scénario S6 (mission Task 7) :

« après une VRAIE reconnexion, avec la compression de contexte activée par
DÉFAUT (``JARVIS_LIVE_COMPRESSION`` n'est jamais désactivée ici), la première
génération déclenchée par une vraie question utilisateur revient vide à
plusieurs reprises -- Jarvis doit relancer et finir par répondre
correctement, SANS perdre le contexte rejoué, SANS consommer `session_generation`
de façon incohérente, et SANS que la relance elle-même ne rouvre la porte
micro (cause racine documentée dans
``tests/test_empty_generation_retry_mic_gate.py``/CHANGELOG.md). »

Ce module utilise le harness partagé et déjà éprouvé
(``tests/voice_harness.py`` + ``tests/live_harness.py``) plutôt qu'un mock
isolé : le chemin complet ``connect -> seed -> SESSION_READY -> audio ->
transcription -> génération -> EMPTY_GENERATION_RETRY -> réponse`` est
exercé avec le VRAI ``GeminiLive``, exactement comme dans les autres tests de
la mission (scénarios A à H, cf. ``tests/test_live_context_harness.py``).

``FakeLiveServer.empty_generations_remaining`` (ajouté pour cette
investigation) fait revenir vide les N prochaines générations DÉCLENCHÉES PAR
UN TOUR UTILISATEUR réel, avec la forme EXACTE observée en validation réelle
(``model_turn`` texte-seul, ``has_inline_audio=False``, aucun
``output_transcription``, rien committé côté serveur) -- cf. docstring de
``FakeLiveServer`` dans ``tests/live_harness.py`` et
``docs/RAPPORT_RECONNEXION_PAR_TOUR_v1.7.4.md`` §13.1.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

from src import gemini_live as gl  # noqa: E402
from tests.live_harness import FakeLiveServer  # noqa: E402
from tests.voice_harness import VoiceHarness  # noqa: E402


class S6ReconnectionWithDefaultCompressionTests(unittest.IsolatedAsyncioTestCase):
    """Le scénario S6 exact : reconnexion, contexte rejoué, PUIS génération(s)
    vide(s) consécutive(s) sur la vraie question -- avec la compression
    activée par défaut (jamais désactivée dans ce module, conformément à la
    contrainte explicite de la mission)."""

    async def asyncSetUp(self) -> None:
        # Garde-fou explicite de la mission : ce test ne doit JAMAIS désactiver
        # la compression par défaut pour "faire passer" le scénario -- on
        # vérifie au contraire qu'elle reste active tout du long.
        self.assertTrue(
            gl.CONTEXT_COMPRESSION_ENABLED,
            "JARVIS_LIVE_COMPRESSION doit rester activée par défaut",
        )

    async def test_empty_generation_retry_recovers_after_reconnection_with_compression_on(
        self,
    ) -> None:
        server = FakeLiveServer("2.x")
        server.suppress_resumption_updates = True  # force le rejeu local (pas de reprise serveur)
        harness = VoiceHarness(server)
        await harness.start()
        try:
            # 1) Avant toute reconnexion : contexte établi normalement.
            first = await harness.speak("Mon prénom est Simon.")
            self.assertTrue(first)

            # 2) VRAIE reconnexion (coupure réseau), comme dans S6/S5.
            server.drop_connection()
            await harness.wait_sessions(2)
            await harness.wait_ready()

            self.assertTrue(
                harness.gemini.context_seeded,
                "le contexte local doit être rejoué (pas de handle de reprise)",
            )
            self.assertTrue(
                harness.gemini.context_seed_confirmed,
                "le rejeu doit être confirmé par le serveur avant l'ouverture du micro",
            )
            # La configuration de la session reseedée a bien la compression
            # (jamais désactivée) -- invariant v1.7.x à préserver.
            config = server.configs[harness.gemini.session_generation]
            compression = getattr(config, "context_window_compression", None)
            self.assertIsNotNone(
                compression,
                "la compression de fenêtre de contexte doit rester active "
                "après reconnexion (jamais désactivée par ce correctif)",
            )

            # 3) La VRAIE question post-reconnexion revient vide 2 fois de
            # suite (EMPTY_GENERATION_MAX_RETRIES), EXACTEMENT la signature
            # g2-t8 de la validation réelle -- puis réussit normalement.
            self.assertEqual(gl.EMPTY_GENERATION_MAX_RETRIES, 2)
            server.empty_generations_remaining = 2
            answer = await harness.speak("Quel est mon prénom ?")

            # NB : le faux serveur partagé (``FakeLiveSession.send_client_content``)
            # traite TOUTE clôture de tour par ``clientContent`` -- qu'il
            # s'agisse du rejeu de contexte après reconnexion OU d'une
            # relance ``EMPTY_GENERATION_RETRY`` -- comme un « récapitulatif »
            # générique (comportement 2.x déjà modélisé AVANT cette mission,
            # cf. ``ReconnexionTests`` dans ``test_live_context_harness.py``) :
            # la réponse finale de la relance n'est donc pas censée citer
            # « Simon » ici. La preuve de non-régression du contexte se fait
            # directement sur ``context_texts()`` plus bas ; ce qui compte
            # ICI est que la relance délivre bien une réponse RÉELLE,
            # non dégradée (pas de tour fantôme « (sans transcription) »).
            self.assertTrue(answer, "la relance doit finir par délivrer une réponse réelle")
            self.assertNotEqual(answer, "(sans transcription)")
            self.assertEqual(
                server.empty_generations_remaining, 0,
                "les deux générations vides simulées ont bien été consommées",
            )

            # 4) Preuves directes sur la trace GeminiLive : exactement 2
            # relances, un seul TURN_COMPLETE final porteur de la vraie
            # réponse, et la porte micro n'a jamais été ouverte par erreur
            # pendant les relances (cf. EMPTY_GENERATION_RETRY_MIC_HELD).
            kinds = [e["kind"] for e in harness.gemini.trace_events()]
            self.assertEqual(kinds.count("EMPTY_GENERATION_RETRY"), 2)
            self.assertEqual(
                kinds.count("EMPTY_GENERATION_RETRY_MIC_HELD"), 2,
                "chaque relance doit explicitement fermer la porte micro",
            )

            # 5) session_generation n'a progressé que pour la VRAIE
            # reconnexion (2) -- les relances internes au tour ne créent
            # JAMAIS de session supplémentaire (pas de régression v1.7.4).
            self.assertEqual(harness.gemini.session_generation, 2)
            self.assertEqual(len(server.sessions), 2)

            # 6) Contexte local final : la déclaration d'avant reconnexion
            # ET l'échange post-reconnexion sont tous deux présents, dans
            # l'ordre, sans doublon ni message fantôme inséré par les
            # tentatives avortées.
            roles_and_texts = harness.context_texts()
            user_texts = [t for r, t in roles_and_texts if r == "user"]
            assistant_texts = [t for r, t in roles_and_texts if r == "assistant"]
            self.assertIn("Mon prénom est Simon.", user_texts)
            self.assertIn("Quel est mon prénom ?", user_texts)
            self.assertEqual(
                user_texts.count("Quel est mon prénom ?"), 1,
                "les tentatives avortées ne doivent jamais dupliquer la "
                "question dans le contexte local",
            )
            # Le contexte local contient les réponses assistant d'avant
            # reconnexion, du récapitulatif de rejeu (``_await_seed_commit``,
            # comportement 2.x déjà existant, inchangé) et de la relance
            # post-reconnexion -- aucune n'est vide/dégradée, ce qui prouve
            # que ni la reconnexion ni les tentatives avortées n'ont laissé
            # de message fantôme ou tronqué dans le contexte local.
            self.assertEqual(len(assistant_texts), 3)
            self.assertTrue(all(assistant_texts), "aucune réponse assistant vide")
            self.assertNotIn("(sans transcription)", assistant_texts)
        finally:
            await harness.stop()

    async def test_regeneration_pending_is_cleared_and_gate_reopens_for_next_turn(
        self,
    ) -> None:
        """Anti-régression : une fois la relance résolue, le tour SUIVANT
        (sur la MÊME session, sans reconnexion) doit fonctionner normalement
        -- la porte fermée pendant la relance ne doit jamais rester bloquée."""
        server = FakeLiveServer("2.x")
        server.suppress_resumption_updates = True
        harness = VoiceHarness(server)
        await harness.start()
        try:
            await harness.speak("Mon prénom est Simon.")
            server.drop_connection()
            await harness.wait_sessions(2)
            await harness.wait_ready()

            server.empty_generations_remaining = 1
            first_answer = await harness.speak("Quel est mon prénom ?")
            self.assertTrue(first_answer)
            self.assertNotEqual(first_answer, "(sans transcription)")
            self.assertFalse(
                harness.gemini._regeneration_pending,
                "la relance résolue doit rouvrir la porte pour le tour suivant",
            )
            self.assertTrue(harness.gemini.can_send())

            # Tour normal suivant, SANS reconnexion ni relance : doit
            # fonctionner exactement comme avant ce correctif.
            second_answer = await harness.speak("Il fait beau.")
            self.assertTrue(second_answer)
            self.assertEqual(
                len(server.sessions), 2,
                "aucune reconnexion supplémentaire ne doit avoir eu lieu",
            )
        finally:
            await harness.stop()


if __name__ == "__main__":
    unittest.main()
