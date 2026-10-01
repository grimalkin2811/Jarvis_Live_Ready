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

---

RÉGRESSION v1.7.5 (suite) : contexte après reconnexion contre l'API RÉELLE
---------------------------------------------------------------------------

Validation réelle (Windows, vraie clé Gemini, vrai micro, vrai TTS) du
correctif v1.7.4 : S1+S4/S2/S5 PASS (la reconnexion-par-tour reste corrigée,
NE PAS RÉGRESSER), mais **S6 (mémoire contextuelle après une VRAIE
reconnexion) a échoué** : réponse vide à « Quel est mon prénom ? » alors que
la trace montrait ``context_seeded=True`` et « aucun audio avant
SESSION_READY ».

Cause racine démontrée (voir addendum v1.7.5 du rapport) : ``context_seeded``
ne prouve que l'ENVOI du rejeu (``send_client_content``), pas que le serveur
l'ait réellement TRAITÉ avant l'arrivée du tour suivant. Sur un WebSocket
réel, l'envoi revient dès l'écriture réseau ; la réponse du modèle (reprise
2.x) arrive de façon RÉELLEMENT asynchrone, parfois après que le tour suivant
a déjà commencé. Le faux serveur partagé (``tests/live_harness.py``) ne
pouvait PAS reproduire cette course : il génère sa réponse de façon
SYNCHRONE à l'intérieur même de l'appel ``send_client_content`` (awaited
jusqu'au bout avant de rendre la main), ce qu'un vrai WebSocket ne fait
jamais — c'est pour cela qu'aucun test existant (y compris
``TestContextPreservedAcrossRealReconnection`` ci-dessus) n'avait détecté le
problème.

Correctif (``GeminiLive._seed_context`` / nouveau ``_await_seed_commit``,
``src/gemini_live.py``) : si le rejeu a été clôturé (``turn_complete=True``),
Jarvis consomme maintenant EXPLICITEMENT le tour de réponse du serveur à ce
rejeu (via ``_receive_one_turn_cycle``, le même mécanisme qu'un tour normal)
AVANT d'ouvrir la porte audio — borné par ``SEED_COMMIT_TIMEOUT_SECONDS``
pour ne jamais bloquer indéfiniment sur un modèle à commit silencieux
(historyConfig/3.x). Un nouveau drapeau ``context_seed_confirmed`` distingue
honnêtement « rejeu envoyé » de « rejeu confirmé par le serveur ».

Les tests ci-dessous utilisent une session factice MINIMALE et dédiée
(``_SlowAckSession``), volontairement DIFFÉRENTE du faux serveur partagé :
son ``send_client_content`` revient immédiatement (fidèle au WebSocket
réel), et sa réponse n'est délivrée que lorsque le test la libère
explicitement — ce qui permet de observer/prouver la course de façon
déterministe, sans dépendre d'un vrai délai réseau.
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
from tests.live_harness import msg_model_transcript, msg_turn_complete  # noqa: E402

from src.conversation import ROLE_ASSISTANT, ROLE_USER, ConversationContext  # noqa: E402
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


# ---------------------------------------------------------------------------
# v1.7.5 — S6 : le rejeu de contexte doit être CONFIRMÉ, pas seulement ENVOYÉ,
# avant l'ouverture de la porte audio.
# ---------------------------------------------------------------------------


class _SlowAckSession:
    """Double minimal et volontairement RÉALISTE d'une session WebSocket.

    Contrairement au faux serveur partagé (``tests/live_harness.py``), dont
    la réponse au rejeu est générée de façon SYNCHRONE à l'intérieur même de
    l'appel ``send_client_content`` (awaited jusqu'au bout avant de rendre la
    main — ce qu'un vrai WebSocket ne fait JAMAIS), cette classe modélise
    fidèlement la vraie asynchronie réseau : ``send_client_content`` revient
    IMMÉDIATEMENT après l'écriture, et la réponse du serveur (reprise +
    ``turn_complete``) n'est livrée sur ``receive()`` que lorsque le test
    appelle explicitement ``release_ack()``. C'est cette fidélité qui permet
    de prouver, de façon déterministe, la course identifiée par l'audit
    v1.7.5 (cause racine de l'échec du scénario S6 contre l'API réelle).
    """

    def __init__(self) -> None:
        self.sent: list[tuple[object, bool]] = []
        self._queue: asyncio.Queue = asyncio.Queue()
        self._closed = False
        self.session_id = "slow-ack-session"
        self.setup_complete = None

    async def send_client_content(self, turns=None, turn_complete: bool = True) -> None:
        self.sent.append((turns, turn_complete))
        # AUCUNE attente ici : fidèle au vrai WebSocket, un envoi n'attend
        # jamais la réponse du modèle avant de rendre la main.

    def receive(self):
        async def gen():
            while True:
                message = await self._queue.get()
                if message is None:
                    return
                yield message
                server_content = getattr(message, "server_content", None)
                if server_content is not None and getattr(server_content, "turn_complete", False):
                    return

        return gen()

    def release_ack(self, text: str = "Parfait, j'ai bien noté.") -> None:
        """Le serveur « répond » enfin au rejeu — appelé explicitement par le test."""
        self._queue.put_nowait(msg_model_transcript(text))
        self._queue.put_nowait(msg_turn_complete())

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(None)


class _SlowAckCtx:
    def __init__(self, session: _SlowAckSession) -> None:
        self.session = session

    async def __aenter__(self) -> _SlowAckSession:
        return self.session

    async def __aexit__(self, *args) -> bool:
        self.session.close()
        return False


class _MiniFakeLive:
    def __init__(self, ctx: _SlowAckCtx) -> None:
        self._ctx = ctx

    def connect(self, model=None, config=None) -> _SlowAckCtx:
        return self._ctx


class _MiniFakeAio:
    def __init__(self, live: _MiniFakeLive) -> None:
        self.live = live


class _MiniFakeClient:
    def __init__(self, ctx: _SlowAckCtx) -> None:
        self.aio = _MiniFakeAio(_MiniFakeLive(ctx))


def _make_seeded_gemini(session: _SlowAckSession) -> GeminiLive:
    """``GeminiLive`` connecté à ``session``, contexte local pré-rempli,
    prêt à exécuter ``_connect_once()``/``_seed_context()`` en isolation
    (sans réseau, sans faux serveur partagé)."""
    conversation = ConversationContext(max_turns=20, max_tokens=4096)
    conversation.add_user_message("Mon prénom est Simon.")
    conversation.add_assistant_message("Enchanté Simon.")
    gemini = GeminiLive(
        key="test-key",
        model="gemini-2.5-flash-native-audio-preview-12-2025",
        user="Test",
        on_audio=lambda pcm: None,
        conversation=conversation,
    )
    gemini.client = _MiniFakeClient(_SlowAckCtx(session))
    gemini.resumption_handle = None
    return gemini


class TestSeedCommitAwaitsRealServerAck(unittest.IsolatedAsyncioTestCase):
    """Item E/S6 (mission v1.7.5) : la porte audio ne doit PAS s'ouvrir tant
    que le serveur n'a pas réellement répondu au rejeu de contexte — un envoi
    réussi (``send_client_content`` revenu) ne suffit pas.
    """

    async def test_connect_once_blocks_until_server_acks_the_seed(self) -> None:
        session = _SlowAckSession()
        gemini = _make_seeded_gemini(session)

        task = asyncio.ensure_future(gemini._connect_once())
        # Laisse la coroutine envoyer le rejeu et commencer à attendre la
        # confirmation — mais le serveur ne répond PAS encore.
        for _ in range(10):
            await asyncio.sleep(0)
        self.assertEqual(len(session.sent), 1, "le rejeu n'a pas été envoyé")
        self.assertTrue(
            session.sent[0][1], "le rejeu doit être clôturé (turn_complete=True)"
        )
        self.assertFalse(
            task.done(),
            "_connect_once() a terminé AVANT que le serveur confirme le rejeu : "
            "la porte audio s'ouvrirait alors que le serveur traite encore le "
            "tour de rejeu — exactement la course qui a fait échouer le "
            "scénario S6 contre l'API réelle (réponse vide à la question "
            "suivante).",
        )
        self.assertFalse(gemini._session_ready)
        self.assertFalse(gemini.can_send())

        # Le serveur répond enfin : la porte doit s'ouvrir, et seulement
        # maintenant.
        session.release_ack()
        await asyncio.wait_for(task, timeout=2.0)
        self.assertTrue(gemini._session_ready)
        self.assertTrue(gemini.can_send())
        self.assertTrue(gemini.context_seeded)
        self.assertTrue(
            gemini.context_seed_confirmed,
            "la confirmation serveur a été observée : elle doit être tracée comme telle",
        )

        ready_events = [e for e in gemini.trace_events() if e["kind"] == "SESSION_READY"]
        self.assertEqual(len(ready_events), 1)
        self.assertTrue(ready_events[0]["context_seed_confirmed"])
        confirm_events = [
            e for e in gemini.trace_events() if e["kind"] == "SEED_COMMIT_CONFIRMED"
        ]
        self.assertEqual(len(confirm_events), 1)

        # La brève reprise du modèle a bien été committée dans le contexte
        # local (comportement 2.x documenté), sans perdre les 2 messages
        # réels précédents.
        messages = gemini.conversation.get_messages()
        self.assertEqual(len(messages), 3)
        self.assertEqual(messages[2].role, ROLE_ASSISTANT)
        self.assertEqual(messages[2].text, "Parfait, j'ai bien noté.")


class TestSeedCommitTimesOutWithoutBlockingForever(unittest.IsolatedAsyncioTestCase):
    """Item C/sécurité (mission v1.7.5) : un modèle qui commit le rejeu SANS
    jamais répondre (historyConfig/3.x — comportement documenté, pas une
    panne) ne doit JAMAIS bloquer le micro indéfiniment. La porte s'ouvre en
    dégradé après ``SEED_COMMIT_TIMEOUT_SECONDS``, et le diagnostic dit
    honnêtement qu'aucune confirmation n'a été observée.
    """

    async def test_gate_opens_in_degraded_mode_after_timeout(self) -> None:
        session = _SlowAckSession()  # jamais libérée : aucune réponse ne viendra
        gemini = _make_seeded_gemini(session)

        start = time.monotonic()
        await asyncio.wait_for(gemini._connect_once(), timeout=10.0)
        elapsed = time.monotonic() - start

        self.assertTrue(gemini._session_ready, "la porte doit finir par s'ouvrir")
        self.assertTrue(gemini.can_send())
        self.assertTrue(gemini.context_seeded, "le rejeu a bien été envoyé")
        self.assertFalse(
            gemini.context_seed_confirmed,
            "aucune réponse n'a été observée : la confirmation ne doit jamais "
            "être affirmée par optimisme",
        )
        # Le délai consommé est borné (pas un blocage permanent du micro).
        from src.gemini_live import SEED_COMMIT_TIMEOUT_SECONDS

        self.assertLessEqual(elapsed, SEED_COMMIT_TIMEOUT_SECONDS + 2.0)
        timeout_events = [
            e for e in gemini.trace_events() if e["kind"] == "SEED_COMMIT_TIMEOUT"
        ]
        self.assertEqual(len(timeout_events), 1)


class TestSeedCommitSkippedWithoutASeed(unittest.IsolatedAsyncioTestCase):
    """Item C (mission v1.7.5) : reconnexion SANS historique local (démarrage
    à froid) ou AVEC un handle de reprise — aucun rejeu n'est envoyé, donc
    aucune attente de confirmation ne doit avoir lieu (la porte s'ouvre
    immédiatement, comme avant ce correctif).
    """

    async def test_cold_start_opens_immediately_without_waiting(self) -> None:
        session = _SlowAckSession()
        conversation = ConversationContext(max_turns=20, max_tokens=4096)
        gemini = GeminiLive(
            key="test-key",
            model="gemini-2.5-flash-native-audio-preview-12-2025",
            user="Test",
            on_audio=lambda pcm: None,
            conversation=conversation,
        )
        gemini.client = _MiniFakeClient(_SlowAckCtx(session))
        gemini.resumption_handle = None

        start = time.monotonic()
        await asyncio.wait_for(gemini._connect_once(), timeout=2.0)
        elapsed = time.monotonic() - start

        self.assertFalse(gemini.context_seeded, "rien à rejouer : historique vide")
        self.assertFalse(gemini.context_seed_confirmed)
        self.assertTrue(gemini._session_ready)
        self.assertEqual(session.sent, [], "aucun clientContent ne devait être envoyé")
        self.assertLess(elapsed, 1.0, "aucune attente ne devait avoir lieu sans rejeu")

    async def test_resumed_session_opens_immediately_without_reseed(self) -> None:
        session = _SlowAckSession()
        gemini = _make_seeded_gemini(session)
        gemini.resumption_handle = "handle-valide"

        start = time.monotonic()
        await asyncio.wait_for(gemini._connect_once(), timeout=2.0)
        elapsed = time.monotonic() - start

        self.assertFalse(
            gemini.context_seeded,
            "le serveur reprend la session lui-même : aucun rejeu local ne doit partir",
        )
        self.assertEqual(session.sent, [])
        self.assertTrue(gemini._session_ready)
        self.assertLess(elapsed, 1.0)


class TestBugReproducesWithoutThePatchS6(unittest.IsolatedAsyncioTestCase):
    """Contrôle négatif (mission v1.7.5) : sans le correctif (c'est-à-dire si
    ``_seed_context`` n'attendait pas la confirmation serveur, comportement
    EXACT d'avant ce correctif), la porte audio s'ouvre bien AVANT que le
    serveur ait traité le rejeu — la course existe réellement, elle n'est pas
    une invention du nouveau test.
    """

    async def test_old_behavior_opens_the_gate_before_the_ack(self) -> None:
        session = _SlowAckSession()
        gemini = _make_seeded_gemini(session)

        async def _pre_v175_seed_context(self: GeminiLive) -> bool:
            """Rejoue EXACTEMENT le ``_seed_context`` d'avant v1.7.5 : envoie
            le rejeu puis rend la main sans jamais attendre de réponse."""
            if self.resumption_handle:
                return False
            messages = self.conversation.get_messages()
            if not messages:
                return False
            from src.conversation import to_gemini_contents

            turns = to_gemini_contents(messages)
            if turns and turns[-1].get("role") != "user":
                turns = [*turns, {"role": "user", "parts": [{"text": " "}]}]
            await self.session.send_client_content(turns=turns, turn_complete=True)
            return True

        with mock.patch.object(GeminiLive, "_seed_context", _pre_v175_seed_context):
            await asyncio.wait_for(gemini._connect_once(), timeout=2.0)

        self.assertTrue(gemini.context_seeded)
        self.assertTrue(
            gemini._session_ready,
            "le comportement PRÉ-v1.7.5 reproduit bien le bug : la porte "
            "s'ouvre immédiatement après l'ENVOI, sans qu'aucune réponse du "
            "serveur n'ait été observée — c'est exactement la course qui "
            "casse le scénario S6 contre l'API réelle",
        )
        self.assertTrue(gemini.can_send())
        # Le serveur n'a RIEN répondu : la preuve que la porte s'est ouverte
        # sur la seule foi de l'envoi, pas d'une confirmation réelle.
        self.assertEqual(len(session.sent), 1)


class TestMultipleReconnectionsDoNotDuplicateContext(_LifecycleTestCase):
    """Item G (mission v1.7.5) : au moins deux reconnexions réelles
    successives ne doivent ni dupliquer ni perdre le contexte local, et la
    SECONDE reconnexion doit être une REPRISE (handle obtenu après le rejeu
    de la première) plutôt qu'un second rejeu complet — sinon le contexte
    rejoué grossirait sans borne à chaque reconnexion.
    """

    def test_second_reconnection_resumes_instead_of_reseeding(self) -> None:
        lab = self.make_lab()
        gemini = lab.gemini
        lab.server.next_user_transcripts = ["mon prénom est simon"]

        lab.user_speaks(1.5)
        self.settle_response(lab)
        messages_before = len(gemini.conversation.get_messages())
        self.assertEqual(messages_before, 2)

        # Première coupure réseau réelle : aucun handle disponible -> VRAI
        # rejeu local (chemin modifié par v1.7.4/v1.7.5).
        gen_before_first = gemini.session_generation
        gemini.resumption_handle = None
        asyncio.run_coroutine_threadsafe(
            lab._drop_current_session(), lab._loop
        ).result(timeout=5.0)
        lab.wait_until(
            lambda: gemini.session_generation > gen_before_first,
            10.0, "première reconnexion",
        )
        lab.wait_until(lambda: gemini.can_send(), 10.0, "session prête (1re reco)")
        gen_after_first = gemini.session_generation
        self.assertTrue(gemini.context_seeded, "premier rejeu attendu (aucun handle)")
        handle_after_first = gemini.resumption_handle
        self.assertIsNotNone(
            handle_after_first,
            "un nouveau handle doit avoir été délivré après le rejeu (sinon "
            "toute reconnexion suivante rejouerait l'historique en entier à "
            "chaque fois, sans jamais converger)",
        )
        messages_after_first = len(gemini.conversation.get_messages())
        self.assertIn(
            messages_after_first - messages_before, (0, 1),
            "au plus UN message orphelin (la reprise 2.x) après le 1er rejeu",
        )

        # Deuxième coupure réseau réelle : CETTE FOIS un handle est présent
        # -> la session doit être REPRISE, pas re-rejouée.
        asyncio.run_coroutine_threadsafe(
            lab._drop_current_session(), lab._loop
        ).result(timeout=5.0)
        lab.wait_until(
            lambda: gemini.session_generation > gen_after_first,
            10.0, "deuxième reconnexion",
        )
        lab.wait_until(lambda: gemini.can_send(), 10.0, "session prête (2e reco)")

        self.assertFalse(
            gemini.context_seeded,
            "la 2e reconnexion devait REPRENDRE la session (handle présent), "
            "pas rejouer l'historique une seconde fois",
        )
        resume_events = [
            e for e in gemini.trace_events() if e["kind"] == "SESSION_RESUME"
        ]
        self.assertTrue(resume_events, "aucune reprise de session tracée")

        # Le contexte local n'a PAS grossi : aucune duplication, rien perdu.
        messages_after_second = len(gemini.conversation.get_messages())
        self.assertEqual(
            messages_after_second, messages_after_first,
            "le contexte local a changé alors qu'aucun rejeu n'a eu lieu à "
            "la 2e reconnexion : signe d'une duplication ou d'une perte",
        )

        # Un vrai suivi après ces deux reconnexions reste fonctionnel.
        lab.server.next_user_transcripts = ["quel est mon prénom"]
        lab.user_speaks(1.5)
        self.settle_response(lab, min_turns=2)
        self.assertEqual(len(lab.user_turns()), 2)
        self.assertEqual(len(lab.ghost_turns()), 0)


if __name__ == "__main__":
    unittest.main()
