"""Régression v1.7.4 : reconnexion-par-tour / « Je vous écoute » périodique.

Contexte (distinct du bug v1.7.2 couvert par ``test_echo_loop.py`` — porte
micro anti-écho — et du bug v1.7.3 couvert par ``test_turn_race.py`` — course
pont micro / double cycle) :

Symptôme rapporté (log Windows réel v1.7.3) : après une interaction NORMALE
(mot de réveil -> phrase -> réponse), pendant la fenêtre de suivi de 8
secondes et SANS aucune nouvelle parole de l'utilisateur, Jarvis dit à
nouveau « Je vous écoute. »/« Je suis prêt... » à intervalles réguliers, la
fenêtre de 8 s se réarme sans cesse, et le journal montre une tempête de
``SESSION CREATED``/reconnexions.

Cause racine démontrée (voir docs/RAPPORT_RECONNEXION_PAR_TOUR_v1.7.4.md) :
``session.receive()`` du SDK Gemini Live se termine NATURELLEMENT à la fin de
CHAQUE tour (confirmé par Google, issue googleapis/python-genai#1224,
résolue : « the receive() method throws you out of the loop if turn is
complete »). ``src/main.py``/``src/ui.py`` traitaient ce retour normal comme
la fin de la CONNEXION et rouvraient un WebSocket neuf après CHAQUE tour —
y compris pendant la fenêtre de conversation, sans aucune parole. Chaque
reconnexion à tort déclenchait ``_seed_context`` (rejeu de l'historique local
clos par un tour « user » synthétique), ce qui provoquait une brève réponse
du modèle, persistée comme message assistant orphelin, et réarmait la
fenêtre de 8 s via ``on_turn_complete`` — boucle auto-entretenue.

Correctif : ``GeminiLive._receive_loop()`` boucle maintenant en INTERNE sur
la MÊME session (``session.receive()`` rappelé pour chaque tour suivant) tant
qu'aucune vraie raison de reconnecter n'est apparue (GoAway, erreur, reset de
contexte, changement de voix). Les appelants (``main.py``/``ui.py``) sont
inchangés : leur reconnexion ne se déclenche plus qu'aux VRAIES fins de
session.

Ces tests utilisent le même harness temps réel que ``test_echo_loop.py``
(``EchoLab`` : AudioIO + GeminiLive + faux serveur Live avec VAD) — la
mission explicite étant de reproduire le scénario exact, pas seulement une
simulation isolée de ``receive_loop()``.
"""

from __future__ import annotations

import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

from tests.echo_loop_harness import EchoLab  # noqa: E402

from src.conversation import ROLE_ASSISTANT, ROLE_USER  # noqa: E402

#: Durée totale d'observation du silence (mission #8 : la fenêtre de 8 s
#: complète, avec marge). Réductible via JARVIS_ECHO_FAST=1 pour itérer.
FAST = os.environ.get("JARVIS_ECHO_FAST", "") not in ("", "0", "false", "non")
FOLLOW_UP_MARGIN_SECONDS = 4.0 if FAST else 12.0


class _LifecycleTestCase(unittest.TestCase):
    def make_lab(self, **kwargs) -> EchoLab:
        lab = EchoLab(**kwargs)
        self.addCleanup(lab.stop)
        lab.start()
        return lab

    def settle_response(self, lab: EchoLab, min_turns: int = 1,
                         timeout: float = 20.0) -> float:
        lab.wait_until(lambda: len(lab.server.turns) >= min_turns, timeout,
                        f"{min_turns} tour(s) committé(s)")
        quiet = 3.0

        def quiescent() -> bool:
            now = time.monotonic()
            recent_events = [e for e in lab.trace() if e["ts"] > now - quiet]
            recent_turns = [t for t in lab.server.turns if t["t"] > now - quiet]
            gate_open = lab.audio._mic_gate_state is True
            drained = lab.audio.output_pending_seconds() == 0.0
            return not recent_events and not recent_turns and gate_open and drained

        lab.wait_until(quiescent, timeout, "quiétude du pipeline")
        return time.monotonic()


class TestNoSpuriousReconnectDuringActiveWindow(_LifecycleTestCase):
    """Item #8 : réveil -> phrase -> réponse -> silence -> fenêtre de 8 s
    active -> aucune nouvelle parole.

    Doit démontrer : exactement UNE interaction utilisateur, AUCUNE création
    de session répétée pendant la fenêtre active, AUCUN nouveau tour Gemini
    généré par le silence, AUCUNE synthèse « Je vous écoute », expiration
    normale après 8 s.
    """

    def test_silence_after_reply_triggers_no_reconnect_and_no_extra_turn(self) -> None:
        lab = self.make_lab()
        gemini = lab.gemini
        lab.server.next_user_transcripts = ["quelle heure est-il"]

        # 1. Mot de réveil (fait par ``EchoLab.start()``) -> phrase.
        lab.user_speaks(1.5)
        silence_start = self.settle_response(lab)

        # État juste après la réponse, AVANT le silence de preuve : une seule
        # session a été créée pour toute l'interaction.
        self.assertEqual(
            lab.server.connect_count, 1,
            "une session a été recréée pendant/juste après la première "
            "réponse, alors qu'aucune reconnexion n'était nécessaire",
        )
        session_events = [
            e for e in gemini.trace_events()
            if e["kind"] in ("SESSION_CONNECT", "SESSION_RESUME")
        ]
        self.assertEqual(
            len(session_events), 1,
            f"plus d'une connexion de session tracée : {session_events}",
        )

        # 2. Silence total pendant (au moins) toute la fenêtre de 8 s.
        lab.wait(FOLLOW_UP_MARGIN_SECONDS)

        # --- Aucune reconnexion supplémentaire n'a eu lieu pendant le silence.
        self.assertEqual(
            lab.server.connect_count, 1,
            "une reconnexion a eu lieu PENDANT le silence — c'est exactement "
            "le bug « Je vous écoute » périodique (v1.7.3 et antérieures)",
        )
        session_events_after = [
            e for e in gemini.trace_events()
            if e["kind"] in ("SESSION_CONNECT", "SESSION_RESUME")
        ]
        self.assertEqual(
            len(session_events_after), 1,
            f"une session a été (re)créée pendant le silence : {session_events_after}",
        )

        # --- Aucun rejeu de contexte supplémentaire (donc aucun tour
        # synthétique ayant pu provoquer une réponse-filler du modèle).
        replay_events = [
            e for e in gemini.trace_events() if e["kind"] == "CONTEXT REPLAY START"
        ]
        self.assertLessEqual(
            len(replay_events), 1,
            f"le contexte a été rejoué plus d'une fois : {replay_events}",
        )

        # --- Exactement UNE interaction utilisateur, aucun tour fantôme.
        self.assertEqual(len(lab.user_turns()), 1)
        self.assertEqual(len(lab.ghost_turns()), 0)

        # --- Aucun réarmement du minuteur de silence pendant le silence
        # observé (aucun « turn_complete » supplémentaire == aucune synthèse
        # « Je vous écoute »/« Je suis prêt... » générée par le silence).
        self.assertEqual(
            len(lab.resets(since=silence_start)), 0,
            f"la fenêtre de 8 s a été réarmée sans nouvelle parole : "
            f"{lab.resets(since=silence_start)}",
        )

        # --- La conversation persistée ne contient que le seul échange réel
        # (aucun message assistant orphelin ajouté par un rejeu de contexte).
        messages = gemini.conversation.get_messages()
        self.assertEqual(len(messages), 2, f"messages inattendus : {messages}")
        self.assertEqual([m.role for m in messages], [ROLE_USER, ROLE_ASSISTANT])

        # --- Expiration normale de la fenêtre : retour en veille.
        lab.wait_until(lambda: not lab.audio.awake, 20.0, "retour en veille")
        self.assertFalse(lab.audio.awake)


class TestRealFollowUpStillHandledOnSameSession(_LifecycleTestCase):
    """Item #9 : un VRAI enchaînement (nouvelle parole réelle avant
    expiration des 8 s) doit continuer à fonctionner — le correctif ne doit
    PAS bloquer les nouvelles sessions/tours légitimes.
    """

    def test_second_real_phrase_before_expiry_uses_same_session(self) -> None:
        lab = self.make_lab()
        gemini = lab.gemini
        lab.server.next_user_transcripts = ["mets un minuteur", "annule le minuteur"]

        # 1. Réveil -> première phrase -> réponse.
        lab.user_speaks(1.5)
        self.settle_response(lab)
        self.assertEqual(len(lab.user_turns()), 1)
        self.assertEqual(lab.server.connect_count, 1)
        generation_after_first_turn = gemini.session_generation

        # 2. VRAIE deuxième phrase, prononcée AVANT l'expiration de la
        # fenêtre de 8 s (le test tourne bien plus vite que 8 s réelles).
        lab.user_speaks(1.5)
        lab.wait_until(lambda: len(lab.user_turns()) >= 2, 8.0,
                       "deuxième tour utilisateur")
        self.settle_response(lab, min_turns=2)

        # --- Le suivi a été traité SANS la moindre reconnexion : même
        # session, même génération, un simple tour supplémentaire reçu sur
        # le WebSocket déjà ouvert (preuve du correctif : le suivi réel n'a
        # besoin d'aucune reconnexion ni d'aucun rejeu de contexte).
        self.assertEqual(
            lab.server.connect_count, 1,
            "le deuxième énoncé réel a déclenché une reconnexion — "
            "comportement des enchaînements légitimes cassé par le correctif",
        )
        self.assertEqual(
            gemini.session_generation, generation_after_first_turn,
            "la génération de session a changé entre les deux tours : "
            "une reconnexion a eu lieu alors qu'aucune n'était nécessaire",
        )
        reused_events = [
            e for e in gemini.trace_events() if e["kind"] == "SESSION_REUSED_NEXT_TURN"
        ]
        self.assertGreaterEqual(
            len(reused_events), 1,
            "aucune trace de continuation sur la même session : le suivi "
            "n'a probablement pas été traité par le mécanisme corrigé",
        )

        # --- Les deux échanges réels sont bien persistés, dans l'ordre,
        # sans message orphelin intercalé.
        self.assertEqual(len(lab.user_turns()), 2)
        self.assertEqual(lab.user_turns()[0]["text"], "mets un minuteur")
        self.assertEqual(lab.user_turns()[1]["text"], "annule le minuteur")
        self.assertEqual(len(lab.ghost_turns()), 0)

        messages = gemini.conversation.get_messages()
        self.assertEqual(len(messages), 4, f"messages inattendus : {messages}")
        self.assertEqual(
            [m.role for m in messages],
            [ROLE_USER, ROLE_ASSISTANT, ROLE_USER, ROLE_ASSISTANT],
        )


if __name__ == "__main__":
    unittest.main()
