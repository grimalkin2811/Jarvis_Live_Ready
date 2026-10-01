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

import asyncio
import os
import sys
import time
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

from tests.echo_loop_harness import EchoLab  # noqa: E402

from src.conversation import ROLE_ASSISTANT, ROLE_USER  # noqa: E402
from src.gemini_live import GeminiLive  # noqa: E402

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


class TestRealReconnectionIsNotCausedByTurnComplete(_LifecycleTestCase):
    """Item D (mission v1.7.4-réel) : une VRAIE condition de reconnexion
    (coupure réseau — pas un ``turn_complete``) doit toujours déclencher une
    nouvelle session, et la raison journalisée ne doit JAMAIS être confondue
    avec une fin de tour normale.
    """

    def test_forced_disconnect_triggers_reconnect_not_attributed_to_turn_complete(self) -> None:
        lab = self.make_lab()
        gemini = lab.gemini
        lab.server.next_user_transcripts = ["quelle heure est-il", "et demain"]

        lab.user_speaks(1.5)
        self.settle_response(lab)
        generation_before = gemini.session_generation
        mark = time.monotonic()

        # Coupure réseau RÉELLE (pas une fin de tour) : équivalent d'une
        # perte Wi-Fi ou d'un redémarrage TCP côté serveur.
        asyncio.run_coroutine_threadsafe(
            lab._drop_current_session(), lab._loop
        ).result(timeout=5.0)

        lab.wait_until(
            lambda: gemini.session_generation > generation_before,
            10.0,
            "reconnexion après coupure réseau réelle",
        )

        connect_events = [
            e for e in gemini.trace_events()
            if e["kind"] in ("SESSION_CONNECT", "SESSION_RESUME") and e["ts"] > mark
        ]
        self.assertTrue(connect_events, "aucune reconnexion tracée après la coupure")
        reasons = {e.get("reason") for e in connect_events}
        # "unexpected_after_normal_turn" est le SENTINEL de régression : s'il
        # apparaît ici, la reconnexion aurait été confondue avec une fin de
        # tour normale (exactement le bug corrigé). "goaway"/"error" sont les
        # deux raisons légitimes qu'une coupure réseau peut produire selon le
        # point exact où le faux serveur l'interrompt.
        self.assertNotIn("unexpected_after_normal_turn", reasons)
        self.assertTrue(
            reasons <= {"error", "goaway"},
            f"raison de reconnexion inattendue : {reasons}",
        )

        # Le nouveau cycle de session fonctionne normalement.
        lab.user_speaks(1.5)
        self.settle_response(lab, min_turns=2)
        self.assertEqual(len(lab.user_turns()), 2)
        self.assertEqual(len(lab.ghost_turns()), 0)


class TestNoAudioBeforeSessionReady(_LifecycleTestCase):
    """Item E (mission v1.7.4-réel) : une session neuve ne doit recevoir
    AUCUN audio avant la fin de son initialisation/rejeu de contexte
    (``_session_ready``), même si le micro continue d'émettre en continu
    pendant la reconnexion (bruit de fond capté par ``AudioIO``).
    """

    def test_no_audio_reaches_new_session_before_it_is_ready(self) -> None:
        lab = self.make_lab()
        gemini = lab.gemini
        lab.server.next_user_transcripts = ["quelle heure est-il"]

        lab.user_speaks(1.5)
        self.settle_response(lab)
        generation_before = gemini.session_generation

        asyncio.run_coroutine_threadsafe(
            lab._drop_current_session(), lab._loop
        ).result(timeout=5.0)
        lab.wait_until(
            lambda: gemini.session_generation > generation_before,
            10.0,
            "reconnexion après coupure réseau réelle",
        )
        # Laisse le rejeu de contexte + quelques cycles micro se dérouler.
        lab.wait(1.0)
        generation_after = gemini.session_generation

        ready_events = [
            e for e in gemini.trace_events()
            if e["kind"] == "SESSION_READY" and e["session_generation"] == generation_after
        ]
        self.assertEqual(
            len(ready_events), 1,
            f"marqueur SESSION_READY absent/dupliqué pour gen={generation_after}",
        )
        ready_ts = ready_events[0]["ts"]

        early_audio = [
            e for e in gemini.trace_events()
            if e["kind"] == "AUDIO_SENT_TO_GEMINI"
            and e["session_generation"] == generation_after
            and e["ts"] < ready_ts
        ]
        self.assertEqual(
            early_audio, [],
            f"de l'audio a atteint la session neuve AVANT la fin de son "
            f"initialisation : {early_audio}",
        )


class TestContextPreservedAcrossRealReconnection(_LifecycleTestCase):
    """Item F (mission v1.7.4-réel) : le contexte conversationnel local doit
    survivre intact à une VRAIE reconnexion (coupure réseau), et doit être
    RÉELLEMENT renvoyé au serveur lors du rejeu (pas seulement conservé en
    mémoire locale sans être exploitable).

    Note méthodologique : ``VadLiveServer`` (utilisé par ``EchoLab`` pour
    reproduire fidèlement la boucle temps réel micro/haut-parleurs) répond
    toujours par une réplique fixe — il ne simule PAS la compréhension
    sémantique d'un vrai modèle (contrairement à ``FakeLiveServer`` +
    ``SemanticOracle`` utilisé par ``tests/test_live_context_harness.py``,
    qui couvre déjà abondamment le rappel de contexte au niveau protocole).
    Ce test vérifie donc la partie qui relève de CE harness : le contexte
    local est intact après reconnexion ET il est effectivement REENVOYÉ sur
    le câble lors du rejeu — l'exploitation sémantique réelle par le modèle
    est validée séparément contre la vraie API (scénario 6 de la mission).
    """

    def test_context_survives_forced_reconnection_and_is_replayed_on_wire(self) -> None:
        lab = self.make_lab()
        gemini = lab.gemini
        lab.server.next_user_transcripts = ["mon prénom est simon"]

        lab.user_speaks(1.5)
        self.settle_response(lab)
        messages_before = gemini.conversation.get_messages()
        self.assertEqual(len(messages_before), 2)
        self.assertEqual([m.role for m in messages_before], [ROLE_USER, ROLE_ASSISTANT])

        # Handle de reprise volontairement abandonné : force le VRAI chemin
        # de rejeu local (``_seed_context``), celui qui a été modifié par le
        # correctif v1.7.4 — sinon le serveur factice reprendrait la session
        # lui-même (``SESSION_RESUME``) et aucun rejeu ne serait observable
        # sur le câble depuis ce harness.
        gemini.resumption_handle = None
        asyncio.run_coroutine_threadsafe(
            lab._drop_current_session(), lab._loop
        ).result(timeout=5.0)
        generation_before = gemini.session_generation
        lab.wait_until(
            lambda: gemini.session_generation > generation_before,
            10.0,
            "reconnexion après coupure réseau réelle",
        )
        lab.wait_until(lambda: gemini.can_send(), 10.0, "session prête après reconnexion")
        generation_after = gemini.session_generation
        self.assertTrue(
            gemini.context_seeded,
            "le contexte n'a pas été rejoué localement alors qu'aucun handle "
            "de reprise n'était disponible",
        )

        # Le contexte local n'a pas été altéré par la reconnexion : les 2
        # messages réels précédents sont intacts, dans l'ordre. Sur les
        # modèles « 2.x » (dont ce faux serveur reproduit la sémantique),
        # le protocole documenté (``_seed_context``, pipecat) veut que la
        # clôture du rejeu déclenche une BRÈVE reprise du modèle — un 3e
        # message assistant peut donc apparaître ici légitimement (ce n'est
        # PAS le bug v1.7.4 : celui-ci ne se reproduit QUE si ce cycle se
        # répète en boucle pendant la fenêtre active, cf.
        # ``TestBugReproducesWithoutThePatch``). Si le VRAI modèle Gemini se
        # comporte différemment (silencieux, comme documenté pour les
        # modèles 3.x/historyConfig), seule la validation API réelle
        # (scénario 6 de la mission) peut le confirmer — ce test ne porte
        # que sur la fidélité du client, pas sur ce choix de protocole.
        messages_after_reconnect = gemini.conversation.get_messages()
        self.assertIn(len(messages_after_reconnect), (2, 3))
        self.assertEqual(
            messages_after_reconnect[0].role, ROLE_USER,
        )
        self.assertEqual(
            messages_after_reconnect[1].role, ROLE_ASSISTANT,
        )
        if len(messages_after_reconnect) == 3:
            self.assertEqual(messages_after_reconnect[2].role, ROLE_ASSISTANT)

        # Le contexte a bien été RENVOYÉ au serveur lors du rejeu de LA
        # NOUVELLE session (pas seulement conservé côté client sans jamais
        # être exploitable) : preuve au niveau du câble.
        replay_events = [
            e for e in lab.server.client_content_events(generation=generation_after)
        ]
        self.assertTrue(
            replay_events, "aucun rejeu de contexte envoyé à la session neuve"
        )
        replayed_text = " ".join(e.all_text() for e in replay_events).lower()
        self.assertIn(
            "simon", replayed_text,
            f"le prénom donné avant la reconnexion n'a pas été rejoué : "
            f"{replayed_text!r}",
        )

        # Un VRAI suivi après reconnexion reste traité normalement (aucun
        # tour fantôme, session inchangée) — l'exploitation sémantique par
        # le modèle réel est validée séparément (API réelle, scénario 6).
        lab.server.next_user_transcripts = ["quel est mon prénom"]
        lab.user_speaks(1.5)
        self.settle_response(lab, min_turns=2)
        self.assertEqual(gemini.session_generation, generation_after)
        self.assertEqual(len(lab.user_turns()), 2)
        self.assertEqual(len(lab.ghost_turns()), 0)


# ---------------------------------------------------------------------------
# Preuve que le bug est RÉEL (pas un artefact du mock)
# ---------------------------------------------------------------------------


async def _pre_v174_receive_loop(self) -> None:
    """Reproduction FIDÈLE du comportement ANTÉRIEUR à v1.7.4.

    Avant le correctif, ``_receive_loop`` consommait un seul appel à
    ``session.receive()`` (donc un seul tour) puis rendait la main à
    l'appelant (``main.py``/``ui.py``/``EchoLab._voice_main``), qui
    interprétait ce retour normal comme une session terminée et rouvrait un
    WebSocket neuf. Cette fonction est injectée à la place de
    ``GeminiLive._receive_loop`` UNIQUEMENT pour prouver que les tests
    ci-dessus détectent un vrai régression et ne passent pas par hasard.
    """
    await self._receive_one_turn_cycle()


class TestBugReproducesWithoutThePatch(_LifecycleTestCase):
    """Contrôle négatif : sans le correctif v1.7.4, le scénario « silence
    après réponse » DOIT reproduire le bug (reconnexion et/ou tour fantôme
    pendant la fenêtre active). Si ce test ne détectait rien, cela voudrait
    dire que ``TestNoSpuriousReconnectDuringActiveWindow`` passe pour de
    mauvaises raisons (mock aligné sur l'implémentation plutôt que sur le
    protocole réel du SDK).
    """

    def test_old_receive_loop_reproduces_the_ghost_turn_storm(self) -> None:
        with mock.patch.object(GeminiLive, "_receive_loop", _pre_v174_receive_loop):
            lab = self.make_lab()
            lab.server.next_user_transcripts = ["quelle heure est-il"]

            lab.user_speaks(1.5)
            # NE PAS utiliser ``settle_response`` ici : sa notion de
            # « quiétude » (aucun évènement/tour depuis 3 s) ne peut
            # structurellement pas être atteinte sous le bug reproduit — la
            # boucle de reconnexion tourne en continu. On attend seulement
            # le premier tour réel, puis on observe une fenêtre fixe.
            lab.wait_until(
                lambda: len(lab.user_turns()) >= 1, 20.0, "premier tour utilisateur"
            )
            silence_start = time.monotonic()
            connect_before = lab.server.connect_count

            lab.wait(FOLLOW_UP_MARGIN_SECONDS)

            reconnected_during_silence = lab.server.connect_count > connect_before
            ghosted = len(lab.ghost_turns(since=silence_start)) > 0
            self.assertTrue(
                reconnected_during_silence or ghosted,
                "le comportement PRÉ-correctif n'a pas reproduit le bug "
                "« reconnexion après chaque tour » sur ce harness — le "
                "harness ne serait alors pas capable de le détecter, ce qui "
                "invaliderait TestNoSpuriousReconnectDuringActiveWindow",
            )


if __name__ == "__main__":
    unittest.main()
