"""Tests sémantiques du contexte conversationnel sur le VRAI pipeline vocal.

Ces tests exécutent ``GeminiLive`` complet (boucle de reconnexion comprise,
cf. ``tests/voice_harness.py``) contre le faux serveur Live fidèle au
protocole (``tests/live_harness.py``). Le « modèle » est un oracle
déterministe : **il ne peut répondre correctement que si l'information a
réellement atteint la génération sous une forme exploitable** — via l'état
serveur (session continue ou reprise) ou via le rejeu committé.

Contrairement à un mock, l'échec d'un de ces tests signifie concrètement :
« l'utilisateur a dit X, puis a demandé X, et l'assistant n'a pas su ».

Scénarios couverts (mission §4 et §15) : A session unique, B multi-tour,
C anaphores, D contexte après outil, E interruption, F reconnexion,
G session resumption (valide et expirée), H changements de mode,
changements de voix, GoAway, reset, historique long, informations
similaires, payload du rejeu (ordre, rôles, unicité, précédence sur l'audio).
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import gemini_live as gl  # noqa: E402
from src.conversation import (  # noqa: E402
    PROVIDER_GEMINI,
    PROVIDER_OLLAMA,
    ConversationContext,
    to_gemini_contents,
    to_ollama_messages,
)
from tests.live_harness import FakeLiveServer  # noqa: E402
from tests.voice_harness import VoiceHarness  # noqa: E402

MODEL_25 = "gemini-2.5-flash-native-audio-preview-12-2025"
MODEL_3 = "gemini-3.1-flash-live-preview"


def _fake_music_search(**kwargs):
    return {
        "success": True,
        "results": [
            "Daft Punk — One More Time",
            "Daft Punk — Get Lucky",
            "Daft Punk — Harder Better Faster Stronger",
        ],
    }


class _HarnessCase(unittest.IsolatedAsyncioTestCase):
    """Socle : un harness vocal + un contexte dédié par test."""

    def make_server(self, semantics: str = "2.x") -> FakeLiveServer:
        server = FakeLiveServer(semantics)
        # Outils factices : le pipeline exécute les VRAIES fonctions de src.tools,
        # on substitue uniquement music_search pour la déterminisme.
        patcher = mock.patch.dict(gl.TOOL_FUNCTIONS, {"music_search": _fake_music_search})
        patcher.start()
        self.addCleanup(patcher.stop)
        return server

    async def make_harness(
        self, server: FakeLiveServer | None = None, **kwargs
    ) -> VoiceHarness:
        server = server if server is not None else self.make_server()
        harness = VoiceHarness(server, model=kwargs.pop("model", MODEL_25), **kwargs)
        await harness.start()
        self.addAsyncCleanup(harness.stop)
        return harness


# ---------------------------------------------------------------------------
# A. Conversation dans UNE session Live
# ---------------------------------------------------------------------------


class SessionUniqueTests(_HarnessCase):
    async def test_A_rappel_du_prenom_dans_une_session(self) -> None:
        harness = await self.make_harness()
        first = await harness.speak("Mon prénom est Simon.")
        self.assertTrue(first, "pas de réponse au premier tour")
        second = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", second)
        # Le contexte local contient bien les deux tours (métrique), mais la
        # preuve sémantique est la réponse ci-dessus.
        roles = [role for role, _ in harness.context_texts()]
        self.assertEqual(roles, ["user", "assistant", "user", "assistant"])

    async def test_transcriptions_fragmentees_fusionnees(self) -> None:
        harness = await self.make_harness()
        await harness.speak("Mon prénom est Simon.", chunks=5)
        user_texts = [t for r, t in harness.context_texts() if r == "user"]
        self.assertEqual(user_texts, ["Mon prénom est Simon."])


# ---------------------------------------------------------------------------
# B. Contexte multi-tour
# ---------------------------------------------------------------------------


class MultiTourTests(_HarnessCase):
    async def test_B_deux_compositeurs_puis_deux_questions(self) -> None:
        harness = await self.make_harness()
        await harness.speak("J'aime beaucoup Hans Zimmer.")
        await harness.speak("J'aime aussi Two Steps From Hell.")
        first = await harness.speak("Quel compositeur est-ce que je viens de citer ?")
        self.assertIn("Hans Zimmer", first)
        second = await harness.speak("Quel autre artiste est-ce que j'ai cité ?")
        self.assertIn("Two Steps From Hell", second)


# ---------------------------------------------------------------------------
# C / D. Outils, anaphores, état après outil
# ---------------------------------------------------------------------------


class ToolContextTests(_HarnessCase):
    async def test_C_le_deuxieme_puis_celui_d_avant(self) -> None:
        server = self.make_server()
        server.next_tool_script = (
            r"cherche.*daft punk",
            "music_search",
            {"query": "Daft Punk"},
        )
        harness = await self.make_harness(server)
        search = await harness.speak("Cherche les morceaux de Daft Punk.")
        self.assertIn("D'accord", search)  # réponse de continuation du tour outil
        self.assertEqual(harness.last_turn.tools, ["music_search"])
        second = await harness.speak("Lance le deuxième.")
        self.assertIn("Get Lucky", second)
        previous = await harness.speak("Reviens au précédent.")
        self.assertIn("One More Time", previous)

    async def test_D_contexte_apres_outil_survit_a_une_reconnexion(self) -> None:
        server = self.make_server()
        server.next_tool_script = (
            r"cherche.*daft punk",
            "music_search",
            {"query": "Daft Punk"},
        )
        harness = await self.make_harness(server)
        await harness.speak("Cherche les morceaux de Daft Punk.")
        # Coupure SANS handle : le rejeu local doit restituer le résultat
        # d'outil (numéroté, compacté) pour que « le deuxième » reste résoluble.
        server.suppress_resumption_updates = True
        server.drop_connection()
        await harness.wait_sessions(2)
        answer = await harness.speak("Lance le deuxième.")
        self.assertIn("Get Lucky", answer)


# ---------------------------------------------------------------------------
# E. Interruption
# ---------------------------------------------------------------------------


class InterruptionTests(_HarnessCase):
    async def test_E_interruption_puis_nouveau_tour_sans_doublon(self) -> None:
        server = self.make_server()
        harness = await self.make_harness(server)
        await harness.speak("Mon prénom est Simon.")
        server.interrupt_next_turn = True
        harness.gemini.request_interrupt()
        cut = await harness.speak("Fais le résumé de la Guerre du Golfe.")
        self.assertTrue(harness.last_turn.interrupted)
        # Le tour interrompu laisse un début de réponse référencable, PAS un
        # tour dupliqué : le contexte contient un seul message utilisateur
        # pour cette demande.
        then = await harness.speak("Fais plutôt la météo de Paris.")
        self.assertTrue(then)
        recall = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", recall)
        user_turns = [t for r, t in harness.context_texts() if r == "user"]
        self.assertEqual(user_turns.count("Fais le résumé de la Guerre du Golfe."), 1)

    async def test_interruption_conserve_le_debut_de_reponse(self) -> None:
        server = self.make_server()
        harness = await self.make_harness(server)
        server.interrupt_next_turn = True
        await harness.speak("Raconte-moi l'histoire de Rome.")
        partial = [
            text
            for role, text in harness.context_texts()
            if role == "assistant"
        ]
        self.assertTrue(partial, "le début de réponse interrompue doit rester référencable")


# ---------------------------------------------------------------------------
# F. Reconnexion sans handle (rejeu local obligatoire)
# ---------------------------------------------------------------------------


class ReconnexionTests(_HarnessCase):
    async def test_F_le_contexte_survit_a_une_reconnexion_sans_handle(self) -> None:
        server = self.make_server()
        server.suppress_resumption_updates = True
        harness = await self.make_harness(server)
        await harness.speak("Mon prénom est Simon.")
        server.drop_connection()
        await harness.wait_sessions(2)
        answer = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", answer)

    async def test_F_multi_tour_apres_reconnexion(self) -> None:
        server = self.make_server()
        server.suppress_resumption_updates = True
        harness = await self.make_harness(server)
        await harness.speak("J'aime beaucoup Hans Zimmer.")
        await harness.speak("J'aime aussi Two Steps From Hell.")
        server.drop_connection()
        await harness.wait_sessions(2)
        answer = await harness.speak("Quel compositeur est-ce que je viens de citer ?")
        self.assertIn("Hans Zimmer", answer)

    async def test_F_le_seed_precede_le_premier_audio(self) -> None:
        # Ordre du câble : dans la session reseedée, le clientContent arrive
        # AVANT tout realtime_input (sinon l'audio tombe dans le pattern
        # « clientContent en attente + realtimeInput », indéfini côté serveur).
        server = self.make_server()
        server.suppress_resumption_updates = True
        harness = await self.make_harness(server)
        await harness.speak("Mon prénom est Simon.")
        server.drop_connection()
        await harness.wait_sessions(2)
        await harness.speak("Quel est mon prénom ?")
        gen2 = server.sessions[1].generation
        kinds = [e.kind for e in server.wire if e.generation == gen2]
        self.assertIn("client_content", kinds)
        self.assertLess(
            kinds.index("client_content"),
            len(kinds) - len([k for k in kinds if k == "client_content"]),
            "le seed doit précéder le premier realtime_input",
        )
        self.assertEqual(kinds[0], "client_content")

    async def test_F_aucune_duplication_dans_le_seed(self) -> None:
        server = self.make_server()
        server.suppress_resumption_updates = True
        harness = await self.make_harness(server)
        await harness.speak("Mon prénom est Simon.")
        await harness.speak("Quel est mon prénom ?")
        server.drop_connection()
        await harness.wait_sessions(2)
        await harness.speak("Il fait beau.")
        seed = server.client_content_events(generation=server.sessions[1].generation)
        self.assertEqual(len(seed), 1)
        all_text = seed[0].all_text()
        # « Simon » apparait légitimement dans 2 tours (déclaration user puis
        # réponse assistant) ; la duplication à détecter serait un TOUR rejoué
        # deux fois : on compte donc chaque phrase exactement une fois.
        self.assertEqual(all_text.count("Mon prénom est Simon."), 1, "tour user dupliqué dans le seed")
        self.assertEqual(all_text.count("Ton prénom est Simon."), 1, "tour assistant dupliqué dans le seed")

    async def test_F_seed_cloture_et_termine_par_un_tour_user(self) -> None:
        server = self.make_server()
        server.suppress_resumption_updates = True
        harness = await self.make_harness(server)
        await harness.speak("Mon prénom est Simon.")
        server.drop_connection()
        await harness.wait_sessions(2)
        seed = server.client_content_events(generation=server.sessions[1].generation)
        self.assertEqual(len(seed), 1)
        self.assertTrue(seed[0].turn_complete, "le seed doit être clôturé (turn_complete=True)")
        self.assertEqual(seed[0].contents[-1].get("role"), "user")
        # Aucun écart de protocole 2.x signalé par le serveur factice.
        self.assertNotIn(
            "seed_termine_par_tour_model", server.sessions[1].protocol_warnings
        )


# ---------------------------------------------------------------------------
# G. Session resumption (handle valide / expiré)
# ---------------------------------------------------------------------------


class ResumptionTests(_HarnessCase):
    async def test_G_handle_valide_le_serveur_restaure_sans_rejeu(self) -> None:
        server = self.make_server()
        harness = await self.make_harness(server)
        await harness.speak("Mon prénom est Simon.")
        self.assertIsNotNone(harness.gemini.resumption_handle)
        server.drop_connection()
        await harness.wait_sessions(2)
        # Le serveur a repris la session : AUCUN clientContent ne doit être
        # envoyé (sinon doublon d'historique).
        self.assertEqual(server.resumed_count, 1)
        self.assertEqual(
            server.client_content_events(generation=server.sessions[1].generation),
            [],
        )
        answer = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", answer)

    async def test_G_handle_expire_repli_sur_session_neuve_et_rejeu(self) -> None:
        server = self.make_server()
        harness = await self.make_harness(server)
        await harness.speak("Mon prénom est Simon.")
        dead_handle = harness.gemini.resumption_handle
        server.invalidate_all_handles()
        server.drop_connection()
        await harness.wait_sessions(2)
        # Le repli a abandonné le handle mort : celui stocké désormais est
        # soit None, soit le nouveau handle de la session de repli (délivré
        # une fois le seed complété) — jamais le handle invalidé.
        stored = harness.gemini.resumption_handle
        self.assertNotEqual(stored, dead_handle, "le handle mort doit être abandonné")
        self.assertTrue(
            stored is None or (stored in server.handles and stored not in server.invalidated),
            "le handle stocké doit être le handle valide de la session de repli",
        )
        answer = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", answer)
        # Pas de boucle de reconnexion : exactement 3 tentatives
        # (1 initiale + 1 refusée + 1 session neuve), pas des dizaines.
        self.assertLessEqual(server.connect_count, 3)

    async def test_G_le_handle_est_renouvelle_apres_chaque_tour(self) -> None:
        server = self.make_server()
        harness = await self.make_harness(server)
        first_handle = harness.gemini.resumption_handle
        await harness.speak("Il fait beau.")
        self.assertIsNotNone(harness.gemini.resumption_handle)
        self.assertNotEqual(first_handle, harness.gemini.resumption_handle)


# ---------------------------------------------------------------------------
# H. Modes, voix, GoAway, reset
# ---------------------------------------------------------------------------


class ModesAndVoicesTests(_HarnessCase):
    async def test_H_le_changement_de_mode_ne_vide_pas_le_contexte(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        from src import modes

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        previous_manager = modes._DEFAULT_MANAGER
        manager = modes.JarvisModeManager(Path(tmp.name) / "mode.json")
        modes.set_default_mode_manager(manager)
        self.addCleanup(modes.set_default_mode_manager, previous_manager)
        priority = patch.object(
            modes.JarvisModeManager, "_set_jarvis_low_priority", return_value={"success": True}
        )
        restore = patch.object(
            modes.JarvisModeManager, "_restore_jarvis_priority", return_value={"success": True}
        )
        priority.start()
        restore.start()
        self.addCleanup(priority.stop)
        self.addCleanup(restore.stop)

        harness = await self.make_harness()
        await harness.speak("Mon prénom est Simon.")
        before = harness.context_texts()
        self.assertTrue(manager.activate_focus_mode(close_distractions=False)["success"])
        self.assertEqual(harness.context_texts(), before)
        self.assertTrue(manager.activate_game_mode(close_background=False)["success"])
        self.assertEqual(harness.context_texts(), before)
        self.assertTrue(manager.disable_mode()["success"])
        # Après un aller-retour de modes, le rappel fonctionne toujours.
        answer = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", answer)

    async def test_H_changement_de_voix_conserve_le_contexte(self) -> None:
        server = self.make_server()
        version = {"v": 0}
        voices = {"v": 0, "name": "Kore"}
        harness = await self.make_harness(
            server,
            voice_provider=lambda: voices["name"],
            voice_version_provider=lambda: version["v"],
        )
        await harness.speak("Mon prénom est Simon.")
        old_handle = harness.gemini.resumption_handle
        voices["name"] = "Puck"
        version["v"] = 1
        # Le watcher détecte le changement (poll 0,5 s) et reconnecte.
        await harness.wait_sessions(2, timeout=5)
        # Session NEUVE — prouvé par le câble : AUCUNE reprise de session
        # (la reprise conserverait l'ANCIENNE voix, cf. doc Live API) —
        # et le handle de l'ancienne session est bien abandonné.
        self.assertEqual(server.resumed_count, 0, "la session doit être neuve, pas reprise")
        stored = harness.gemini.resumption_handle
        self.assertNotEqual(stored, old_handle, "le handle de l'ancienne voix doit être abandonné")
        # La nouvelle voix figure dans la config de la nouvelle session.
        config = server.last_config
        speech = getattr(config, "speech_config", None)
        voice_cfg = getattr(speech, "voice_config", None)
        prebuilt = getattr(voice_cfg, "prebuilt_voice_config", None)
        self.assertEqual(getattr(prebuilt, "voice_name", None), "Puck")
        # ... et le contexte local a été rejoué : le rappel fonctionne.
        answer = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", answer)

    async def test_goaway_reconnexion_propre_avec_handle(self) -> None:
        server = self.make_server()
        harness = await self.make_harness(server)
        await harness.speak("Mon prénom est Simon.")
        await server.send_goaway()
        await asyncio.sleep(0)
        self.assertTrue(harness.gemini.reconnect_requested)
        server.drop_connection()
        await harness.wait_sessions(2)
        self.assertEqual(server.resumed_count, 1)
        answer = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", answer)

    async def test_reset_vide_le_contexte_et_la_session(self) -> None:
        server = self.make_server()
        harness = await self.make_harness(server)
        await harness.speak("Mon prénom est Simon.")
        harness.conversation.start_new_conversation(reason="test reset")
        await harness.wait_sessions(2)
        answer = await harness.speak("Quel est mon prénom ?")
        self.assertNotIn("Simon", answer)
        self.assertTrue(harness.conversation.is_empty() or harness.context_texts() == [
            ("user", "Quel est mon prénom ?"),
            ("assistant", answer),
        ])


# ---------------------------------------------------------------------------
# §15 — Questions pièges sémantiques
# ---------------------------------------------------------------------------


class SemanticEdgeTests(_HarnessCase):
    async def test_plusieurs_informations_dans_un_tour(self) -> None:
        harness = await self.make_harness()
        await harness.speak(
            "Mon prénom est Simon et mon compositeur préféré est Hans Zimmer."
        )
        name = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", name)
        composer = await harness.speak("Quel est mon compositeur préféré ?")
        self.assertIn("Hans Zimmer", composer)

    async def test_negation_et_correction(self) -> None:
        server = self.make_server()
        server.oracle.register(
            r"quelle était ma (?:première|tout première) demande",
            lambda visible: (
                f"Ta première demande était : {visible[0][1]}."
                if visible
                else "Je ne sais pas."
            ),
        )
        harness = await self.make_harness(server)
        await harness.speak("Mets la chanson A.")
        await harness.speak("Non, finalement mets la chanson B.")
        asked = await harness.speak("Qu'est-ce que je t'avais demandé juste avant ?")
        self.assertIn("chanson B", asked)
        first = await harness.speak("Quelle était ma première demande ?")
        self.assertIn("chanson A", first)

    async def test_informations_similaires_distinguees(self) -> None:
        server = self.make_server()
        server.oracle.register(
            r"qu'est-ce que je n'aime pas",
            lambda visible: (
                "Tu n'aimes pas Daft Punk."
                if any("n'aime pas Daft Punk" in text for _role, text in visible)
                else "Je ne sais pas."
            ),
        )
        server.oracle.register(
            r"que préfères-je",
            lambda visible: (
                "Tu préfères Two Steps From Hell à Ludovico Einaudi."
                if any("préfère Two Steps From Hell" in text for _role, text in visible)
                else "Je ne sais pas."
            ),
        )
        harness = await self.make_harness(server)
        await harness.speak("J'aime Hans Zimmer.")
        await harness.speak("Je n'aime pas Daft Punk.")
        await harness.speak("Je préfère Two Steps From Hell à Ludovico Einaudi.")
        liked = await harness.speak("Quel compositeur est-ce que je viens de citer ?")
        self.assertIn("Hans Zimmer", liked)
        disliked = await harness.speak("Qu'est-ce que je n'aime pas ?")
        self.assertIn("Daft Punk", disliked)
        preferred = await harness.speak("Que préfères-je, et à quoi ?")
        self.assertIn("Two Steps From Hell", preferred)

    async def test_historique_long_rappel_du_debut(self) -> None:
        server = self.make_server()
        server.suppress_resumption_updates = True
        harness = await self.make_harness(server)
        await harness.speak("Mon prénom est Simon.")
        for index in range(16):
            await harness.speak(f"Rappelé numéro {index}, merci.")
        # En session continue, le serveur a tout l'historique.
        in_session = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", in_session)
        # Après coupure + rejeu, le DÉBUT de la conversation doit survivre au
        # trimming (20 tours par défaut) et rester exploitable par le modèle.
        server.drop_connection()
        await harness.wait_sessions(2)
        after = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", after)
        seed = server.client_content_events(generation=server.sessions[1].generation)[0]
        self.assertIn("Simon", seed.all_text())


# ---------------------------------------------------------------------------
# Provider switch (contexte détenu par Jarvis, pas par le fournisseur)
# ---------------------------------------------------------------------------


class ProviderSwitchTests(_HarnessCase):
    async def test_le_changement_de_provider_conserve_le_contexte(self) -> None:
        harness = await self.make_harness()
        await harness.speak("Mon prénom est Simon.")
        previous = harness.conversation.set_provider(PROVIDER_OLLAMA)
        self.assertEqual(previous, PROVIDER_GEMINI)
        self.assertIn(("user", "Mon prénom est Simon."), harness.context_texts())
        # Les deux adaptateurs restent exploitables avec le même historique.
        gemini_payload = harness.conversation.messages_for_provider(PROVIDER_GEMINI)
        ollama_payload = harness.conversation.messages_for_provider(PROVIDER_OLLAMA)
        self.assertTrue(any("Simon" in str(m) for m in gemini_payload))
        self.assertTrue(any("Simon" in str(m) for m in ollama_payload))
        # Retour à Gemini : la conversation continue sans perte.
        harness.conversation.set_provider(PROVIDER_GEMINI)
        answer = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", answer)

    def test_adaptateurs_unitaires_roles_et_outils(self) -> None:
        context = ConversationContext()
        context.add_user_message("Cherche Daft Punk.")
        context.add_tool_interaction(
            "music_search", {"query": "Daft Punk"}, {"success": True, "results": ["a", "b"]}
        )
        context.add_assistant_message("Voilà ce que j'ai trouvé.")
        messages = context.get_messages()
        gemini = to_gemini_contents(messages)
        self.assertEqual([c["role"] for c in gemini], ["user", "model", "user", "model"])
        ollama = to_ollama_messages(messages, system_prompt="Tu es Jarvis.")
        self.assertEqual(ollama[0]["role"], "system")
        self.assertEqual(
            [m["role"] for m in ollama[1:]], ["user", "assistant", "tool", "assistant"]
        )


# ---------------------------------------------------------------------------
# Modèles 3.x : historyConfig + commit silencieux
# ---------------------------------------------------------------------------


class ModernModelTests(_HarnessCase):
    async def test_3x_le_seed_est_committe_sans_recap_et_rappelle(self) -> None:
        server = self.make_server(semantics="3.x")
        server.suppress_resumption_updates = True
        harness = await self.make_harness(server, model=MODEL_3)
        await harness.speak("Mon prénom est Simon.")
        turns_before = len(harness.turns)
        server.drop_connection()
        await harness.wait_sessions(2)
        # Le seed est committé SANS appel modèle (pas de tour de récapitulatif).
        seed = server.client_content_events(generation=server.sessions[1].generation)
        self.assertEqual(len(seed), 1)
        self.assertTrue(seed[0].turn_complete)
        answer = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", answer)

    async def test_la_configuration_contient_history_config_et_compression(self) -> None:
        from google.genai import types

        server = self.make_server()
        harness = await self.make_harness(server)
        config = server.last_config
        history = getattr(config, "history_config", None)
        self.assertTrue(
            history and getattr(history, "initial_history_in_client_content", False),
            "historyConfig.initialHistoryInClientContent doit être activé",
        )
        compression = getattr(config, "context_window_compression", None)
        self.assertIsNotNone(
            compression, "la compression de fenêtre de contexte doit être activée"
        )
        self.assertIsNotNone(getattr(compression, "sliding_window", None))
        self.assertIsNotNone(getattr(config, "input_audio_transcription", None))
        self.assertIsNotNone(getattr(config, "output_audio_transcription", None))


# ---------------------------------------------------------------------------
# Instrumentation (mission §5)
# ---------------------------------------------------------------------------


class InstrumentationTests(_HarnessCase):
    async def test_generations_de_session_distinctes(self) -> None:
        server = self.make_server()
        server.suppress_resumption_updates = True
        harness = await self.make_harness(server)
        self.assertEqual(harness.gemini.session_generation, 1)
        self.assertFalse(harness.gemini.context_seeded)  # contexte vide au départ
        self.assertIsNotNone(harness.gemini.session_id)
        await harness.speak("Mon prénom est Simon.")
        server.drop_connection()
        await harness.wait_sessions(2)
        self.assertEqual(harness.gemini.session_generation, 2)
        self.assertTrue(harness.gemini.context_seeded)  # rejeu effectif

    async def test_traces_structurees_presentes(self) -> None:
        server = self.make_server()
        server.suppress_resumption_updates = True
        harness = await self.make_harness(server)
        with self.assertLogs("jarvis.gemini", level="DEBUG") as captured:
            await harness.speak("Mon prénom est Simon.")
            server.drop_connection()
            await harness.wait_sessions(2)
            await harness.speak("Quel est mon prénom ?")
        text = "\n".join(captured.output)
        self.assertIn("SESSION CREATED", text)
        self.assertIn("CONTEXT REPLAY START", text)
        self.assertIn("CONTEXT REPLAY END", text)
        self.assertIn("TURN COMPLETE", text)
        self.assertIn("gen=2", text)
        with self.assertLogs("jarvis.conversation", level="DEBUG") as ctx_logs:
            await harness.speak("Encore un mot.")
        ctx_text = "\n".join(ctx_logs.output)
        self.assertIn("user", ctx_text)
        self.assertIn("assistant", ctx_text)


if __name__ == "__main__":
    unittest.main()
