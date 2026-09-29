"""Tests de régression de la boucle d'écho « Je vous écoute ».

Contexte
--------
Symptôme : après une requête utilisateur, Jarvis répétait « Je vous écoute »
environ toutes les 2 secondes sans qu'aucune parole humaine ne soit dite, et
la fenêtre d'écoute de 8 secondes était réarmée en boucle.

Cause racine (démontrée par le harness ``tests/echo_loop_harness.py`` sur le
code non corrigé) : à ``turn_complete`` (fin de GÉNÉRATION côté serveur),
``GeminiLive.speaking`` repasse à False et le micro est immédiatement
transmis à Gemini — alors que la file de sortie contient encore ~1,2 s de
voix de Jarvis. Le serveur reçoit l'écho acoustique, sa VAD commit un tour
« utilisateur » fantôme, le modèle répond « Je vous écoute. », et chaque
``turn_complete`` réarme la fenêtre de 8 s : boucle stable d'environ 1,7 s.

Correctif : porte micro anti-écho dans ``AudioIO`` (``_mic_gate_open``) — le
micro n'est pas transmis tant que la voix de Jarvis sort des haut-parleurs
(file non vide + traîne acoustique) ; comptage corrigé de ``_queued_bytes`` ;
garde ``awake`` sur ``clear_output`` ; anti-rebond du réveil en mode écoute
continue.

Suites ci-dessous (mission §9 A-G, §10 stabilité, §12 performance, §5
comptage du timer) — les tests temps réel traversent le VRAI pipeline :
carte son factice threadée -> AudioIO -> pont micro -> GeminiLive -> faux
serveur Live avec VAD, avec un modèle acoustique à écho.
"""

from __future__ import annotations

import os
import sys
import time
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

import numpy as np  # noqa: E402

# La fausse carte son doit être installée avant src.audio (fait par le
# harness). On importe le harness d'abord, exprès.
from tests.echo_loop_harness import EchoLab  # noqa: E402

from src.audio import AudioIO  # noqa: E402


#: Durée du silence « preuve » (mission §3 : au moins 20 secondes).
SILENCE_SECONDS = float(os.environ.get("JARVIS_ECHO_SILENCE", "20"))
#: Mode rapide pour itérer localement : JARVIS_ECHO_FAST=1.
FAST = os.environ.get("JARVIS_ECHO_FAST", "") not in ("", "0", "false", "non")

if FAST:  # le test de stabilité reste long par nature ; on le raccourcit
    SILENCE_SECONDS = min(SILENCE_SECONDS, 6.0)


def _tone(seconds: float, rms: float, rate: int) -> bytes:
    n = int(rate * seconds)
    t = np.arange(n) / rate
    carrier = np.sin(2 * np.pi * 230.0 * t) * (0.8 + 0.2 * np.sin(2 * np.pi * 4.0 * t))
    return (np.clip(carrier * rms * np.sqrt(2.0), -32768, 32767)).astype(np.int16).tobytes()


# ---------------------------------------------------------------------------
# §5 + comptage : primitives audio (rapides, sans threads)
# ---------------------------------------------------------------------------


class QueuedBytesAccountingTests(unittest.TestCase):
    """``_queued_bytes`` doit compter la file TOTALE (q + buffer), sans
    double décompte : c'est lui qui borne l'avance audio (MAX_QUEUED_SECONDS)
    et alimente la porte anti-écho."""

    def setUp(self) -> None:
        self.audio = AudioIO(lambda pcm: None)
        self.audio.running = True

    def _dac_tick(self, frames: int = 1024) -> None:
        outdata = bytearray(frames * AudioIO.SAMPLE_WIDTH)
        self.audio._out(outdata, frames, None, None)

    def test_counter_tracks_real_pending_and_never_goes_negative(self) -> None:
        one_second = _tone(1.0, 4000, AudioIO.OUTPUT_RATE)
        for _ in range(3):
            self.audio.play(one_second)
        self.assertEqual(self.audio._queued_bytes, 3 * len(one_second))
        # La carte son consomme par ticks de 1024 trames (~42,7 ms).
        ticks = 0
        while self.audio.output_pending_bytes() > 0:
            before = self.audio._queued_bytes
            self._dac_tick()
            ticks += 1
            # Chaque tick décompte exactement ce qu'il joue, jamais plus.
            self.assertGreaterEqual(self.audio._queued_bytes, 0)
            self.assertEqual(
                self.audio._queued_bytes,
                before - min(before, 1024 * AudioIO.SAMPLE_WIDTH),
            )
        self.assertGreater(ticks, 40)
        self.assertEqual(self.audio._queued_bytes, 0)
        self.assertEqual(self.audio.output_pending_seconds(), 0.0)

    def test_cap_still_bounds_the_queue(self) -> None:
        one_second = _tone(1.0, 4000, AudioIO.OUTPUT_RATE)
        for _ in range(int(AudioIO.MAX_QUEUED_SECONDS) * 3 + 2):
            self.audio.play(one_second)
        self.assertLessEqual(self.audio._queued_bytes, self.audio._max_queued_bytes)
        self.assertLessEqual(
            self.audio.output_pending_seconds(), AudioIO.MAX_QUEUED_SECONDS + 1.0
        )


class MicGateUnitTests(unittest.TestCase):
    """Table de vérité de la porte micro anti-écho."""

    def setUp(self) -> None:
        self.audio = AudioIO(lambda pcm: None)
        self.audio.running = True

    def test_open_when_nothing_ever_played(self) -> None:
        self.assertTrue(self.audio._mic_gate_open())

    def test_closed_while_output_pending(self) -> None:
        self.audio.play(_tone(0.3, 4000, AudioIO.OUTPUT_RATE))
        self.assertFalse(self.audio._mic_gate_open())

    def test_guard_survives_a_short_moment_after_drain(self) -> None:
        self.audio.play(_tone(0.3, 4000, AudioIO.OUTPUT_RATE))
        self.audio._mic_gate_open()  # occupe : horodate le pending
        # La carte son vide tout.
        while self.audio.output_pending_bytes() > 0:
            self.audio._out(bytearray(1024 * AudioIO.SAMPLE_WIDTH), 1024, None, None)
        self.assertEqual(self.audio.output_pending_bytes(), 0)
        # La traîne acoustique garde la porte fermée un court instant.
        self.assertFalse(self.audio._mic_gate_open())

    def test_guard_opens_after_the_acoustic_tail(self) -> None:
        self.audio.play(_tone(0.3, 4000, AudioIO.OUTPUT_RATE))
        self.audio._mic_gate_open()
        while self.audio.output_pending_bytes() > 0:
            self.audio._out(bytearray(1024 * AudioIO.SAMPLE_WIDTH), 1024, None, None)
        self.audio._output_last_pending_ts = (
            time.monotonic() - AudioIO.MIC_ECHO_GUARD_SECONDS - 0.05
        )
        self.assertTrue(self.audio._mic_gate_open())

    def test_clear_output_opens_the_gate_immediately(self) -> None:
        self.audio.play(_tone(0.3, 4000, AudioIO.OUTPUT_RATE))
        self.audio.awake = True
        self.audio.clear_output()
        self.assertTrue(self.audio._mic_gate_open())

    def test_clear_output_while_asleep_is_ignored(self) -> None:
        """Un callback résiduel ne doit jamais rouvrir l'écoute d'un Jarvis
        endormi (mission §6 : hidden != listening)."""
        presence: list[str] = []
        audio = AudioIO(lambda pcm: None, presence_hook=presence.append)
        audio.running = True
        audio.awake = False
        before = audio.follow_up_until
        audio.clear_output()
        self.assertEqual(audio.follow_up_until, before)
        self.assertNotIn("listening", presence)
        kinds = [e["kind"] for e in audio.trace_events()]
        self.assertIn("clear_output ignore", kinds)

    def test_extend_listening_while_asleep_is_ignored(self) -> None:
        audio = AudioIO(lambda pcm: None)
        audio.running = True
        audio.awake = False
        before = audio.follow_up_until
        audio.extend_listening()
        self.assertEqual(audio.follow_up_until, before)

    def test_listen_mode_rewake_respects_cooldown(self) -> None:
        """Mode écoute continue : un Jarvis rendu endormi (perte de connexion)
        ne doit pas être réveilli à chaque bloc de 80 ms — un seul réveil par
        seconde de cooldown, comme le wake word."""
        wakes: list[str] = []
        audio = AudioIO(
            lambda pcm: None,
            listen_mode_provider=lambda: True,
        )
        audio.running = True
        audio.awake = False
        # 5 blocs consécutifs (400 ms) : un seul réveil.
        block = _tone(0.08, 10, AudioIO.INPUT_RATE)
        for _ in range(5):
            audio._handle_idle_block(block)
        wake_events = [
            e for e in audio.trace_events() if e["kind"] == "wake"
        ]
        self.assertEqual(len(wake_events), 1)
        self.assertEqual(wake_events[0]["reason"], "listen_mode")
        self.assertTrue(audio.awake)
        _ = wakes


# ---------------------------------------------------------------------------
# Tests temps réel : le vrai pipeline avec écho acoustique
# ---------------------------------------------------------------------------


class _EchoTestCase(unittest.TestCase):
    """Socle : un lab par test, avec arrêt garanti."""

    def make_lab(self, **kwargs) -> EchoLab:
        lab = EchoLab(**kwargs)
        self.addCleanup(lab.stop)
        lab.start()
        return lab

    def settle_response(self, lab: EchoLab, min_turns: int = 1,
                        timeout: float = 20.0) -> float:
        """Attend le commit des ``min_turns`` tours puis la QUIÉTUDE du
        pipeline : plus AUCUN événement (tour serveur, réarmement, porte
        micro, parole) pendant 3 s, porte micro ouverte et file de sortie
        vidée. Retourne l'instant où le silence effectif commence.

        La quiétude — plutôt qu'un événement précis — élimine toute course
        d'ordonnancement entre le commit d'un tour et ses événements dérivés
        (réarmement, interruption, réouverture) : on attend que TOUT soit
        terminé, y compris la traîne de lecture.
        """
        lab.wait_until(lambda: len(lab.server.turns) >= min_turns, timeout,
                       f"{min_turns} tour(s) committé(s)")
        quiet = 3.0
        deadline = time.monotonic() + timeout

        def quiescent() -> bool:
            now = time.monotonic()
            recent_events = [e for e in lab.trace() if e["ts"] > now - quiet]
            recent_turns = [t for t in lab.server.turns if t["t"] > now - quiet]
            gate_open = lab.audio._mic_gate_state is True
            drained = lab.audio.output_pending_seconds() == 0.0
            return not recent_events and not recent_turns and gate_open and drained

        lab.wait_until(quiescent, timeout, "quiétude du pipeline")
        return time.monotonic()


class TestA_Silence(_EchoTestCase):
    """A : parole -> silence 20 s -> 0 redémarrage d'écoute."""

    def test_silence_produces_zero_listening_restarts(self) -> None:
        lab = self.make_lab()
        lab.server.next_user_transcripts = ["quelle heure est-il"]
        lab.user_speaks(1.5)
        lab.wait(1.0)
        # Le tour utilisateur + sa réponse se terminent (porte rouverte).
        silence_start = self.settle_response(lab)

        # SILLENCE TOTAL : ni parole, ni bruit supérieur au seuil VAD.
        lab.wait(SILENCE_SECONDS)

        metrics = lab.metrics()
        ghosts = lab.ghost_turns(since=silence_start)
        resets = lab.resets(since=silence_start)
        listening = [
            s for s in lab.presence_states(since=silence_start) if s == "listening"
        ]

        # --- Assertions fondamentales (mission §3) ---
        self.assertEqual(len(ghosts), 0, f"tours fantômes: {ghosts}")
        self.assertEqual(
            len(resets), 0,
            f"réarmements du timer pendant le silence: "
            f"{[(r['reason'], r['since_last_reset_s']) for r in resets]}"
        )
        self.assertEqual(len(listening), 0, "redémarrages d'écoute")
        self.assertEqual(metrics["wake_events"], 1)  # le réveil de test seul

        # La fenêtre de 8 s a expiré normalement : retour en veille.
        self.assertLessEqual(metrics["sleep_events"], 1)
        self.assertIn("hidden", lab.presence_states(since=silence_start))
        self.assertFalse(lab.audio.awake)

        # Le tour unique est bien celui de l'utilisateur.
        self.assertEqual(len(lab.user_turns()), 1)
        self.assertEqual(lab.user_turns()[0]["text"], "quelle heure est-il")


class TestB_TwoPhrases(_EchoTestCase):
    """B : parole 1 -> silence -> parole 2 : le 2e énoncé est détecté."""

    def test_second_phrase_is_detected_after_first_response(self) -> None:
        lab = self.make_lab()
        lab.server.next_user_transcripts = ["mets un minuteur", "annule le minuteur"]

        # Première phrase et sa réponse.
        lab.user_speaks(1.5)
        self.settle_response(lab)
        self.assertEqual(len(lab.user_turns()), 1)

        # Deuxième phrase (après la fin de lecture de la réponse).
        lab.user_speaks(1.5)
        lab.wait_until(lambda: len(lab.user_turns()) >= 2, 8.0,
                       "deuxième tour utilisateur")
        self.settle_response(lab)

        self.assertEqual(len(lab.user_turns()), 2)
        self.assertEqual(lab.user_turns()[1]["text"], "annule le minuteur")
        self.assertEqual(len(lab.ghost_turns()), 0)


class TestC_TTSOutputIsNotUserInput(_EchoTestCase):
    """C : la sortie TTS ne doit jamais devenir une requête utilisateur.

    Concrètement : pendant la lecture de la réponse (et jusqu'à la traîne
    acoustique), le serveur ne reçoit AUCUN bloc micro — pas seulement des
    blocs silencieux : la porte coupe le flux.
    """

    def test_no_mic_audio_reaches_server_while_speaking(self) -> None:
        lab = self.make_lab()
        lab.server.next_user_transcripts = ["raconte-moi une blague"]
        lab.server.next_user_answers = ["Pourquoi les poissons détestent l'ordinateur ? À cause de la souris."]
        lab.user_speaks(1.5)

        # Attendre le début de la réponse (porte fermée).
        speaking_start = time.monotonic()
        lab.wait_until(
            lambda: any(
                e["kind"] == "mic_gate" and not e.get("state")
                for e in lab.trace()
            ),
            6.0, "fermeture de la porte micro",
        )
        gate_closed_ts = next(
            e["ts"] for e in lab.trace()
            if e["kind"] == "mic_gate" and not e.get("state")
        )
        lab.wait_until(lambda: lab.gate_opened_after(gate_closed_ts), 8.0,
                       "réouverture de la porte")

        # Aucun audio micro n'est arrivé pendant la lecture + traîne.
        during_playback = [
            (t, rms) for t, rms in lab.audio_in(since=gate_closed_ts - 0.01)
            if t <= gate_closed_ts + AudioIO.MIC_ECHO_GUARD_SECONDS
            or any(
                e["kind"] == "mic_gate" and e.get("state") and e["ts"] >= t
                for e in []
            )
        ]
        # Plus simple et plus fort : entre la fermeture et la réouverture,
        # le serveur ne reçoit rien du tout.
        reopen_ts = next(
            e["ts"] for e in lab.trace()
            if e["kind"] == "mic_gate" and e.get("state") and e["ts"] > gate_closed_ts
        )
        forwarded = [
            (t, rms) for t, rms in lab.audio_in()
            if gate_closed_ts <= t <= reopen_ts
        ]
        self.assertEqual(
            forwarded, [],
            f"blocs micro transmis pendant la lecture de la réponse: {forwarded}"
        )
        _ = during_playback, speaking_start

        # Après la réouverture : seul le bruit de fond (sous le seuil VAD).
        after = lab.audio_in(since=reopen_ts)
        self.assertTrue(after, "le micro doit reprendre après la réponse")
        self.assertLess(max(rms for _, rms in after), lab.server.threshold_rms)

        # Et donc aucun tour fantôme.
        self.settle_response(lab)
        self.assertEqual(len(lab.ghost_turns()), 0)


class TestD_Interrupt(_EchoTestCase):
    """D : l'utilisateur coupe la réponse — aucun ancien timer ne survit."""

    def test_barge_in_cuts_response_and_no_stale_timer_survives(self) -> None:
        lab = self.make_lab(echo_gain=0.15, response_seconds=2.0)
        lab.server.next_user_transcripts = ["stop"]
        lab.user_speaks(1.2)
        # Attendre le début de la réponse, puis parler par-dessus (après la
        # période de grâce de 0,6 s).
        lab.wait_until(
            lambda: any(e["kind"] == "mic_gate" and not e.get("state")
                        for e in lab.trace()),
            6.0, "début de réponse",
        )
        gate_closed_ts = next(
            e["ts"] for e in lab.trace()
            if e["kind"] == "mic_gate" and not e.get("state")
        )
        lab.wait(0.7)  # période de grâce écoulée, réponse toujours en lecture
        lab.user_speaks(1.6, rms=4500)  # l'utilisateur parle FORT par-dessus

        # Le barge-in se déclenche, la sortie est coupée, le micro passe.
        lab.wait_until(
            lambda: any(e["kind"] == "barge_in" for e in lab.trace()),
            4.0, "barge-in",
        )
        lab.wait_until(lambda: len(lab.user_turns()) >= 2, 8.0,
                       "le « stop » atteint le serveur")

        # Après l'interruption : silence total, aucun timer résiduel.
        silence_start = self.settle_response(lab, min_turns=2)
        lab.wait(min(SILENCE_SECONDS, 12.0))

        self.assertEqual(len(lab.ghost_turns(since=silence_start)), 0)
        self.assertEqual(len(lab.resets(since=silence_start)), 0)
        # Les réarmements sont tous expliqués et postérieurs au réveil.
        reasons = {e["reason"] for e in lab.resets()}
        self.assertIn("interrupt", reasons)      # le barge-in a réarmé
        self.assertIn("turn_complete", reasons)  # le tour « stop » a réarmé
        # Jarvis est retourné en veille : la fenêtre a expiré.
        self.assertFalse(lab.audio.awake)


class TestE_FollowUpListening(_EchoTestCase):
    """E : fenêtre d'écoute après réponse -> silence -> timeout -> hidden,
    et hidden reste hidden (aucun nouveau listening)."""

    def test_window_expires_to_hidden_and_stays_hidden(self) -> None:
        lab = self.make_lab()
        lab.server.next_user_transcripts = ["merci"]
        lab.user_speaks(1.2)
        self.settle_response(lab)

        # La fenêtre de suivi est ouverte : Jarvis est à l'écoute...
        self.assertTrue(lab.audio.awake)
        self.assertIn("listening", lab.presence_states())
        # ...puis expire (8 s) : retour en veille, sans restart.
        lab.wait(AudioIO.FOLLOW_UP_SECONDS + 2.5)
        self.assertFalse(lab.audio.awake)
        self.assertEqual(lab.presence_states()[-1], "hidden")

        # hidden != nouveau listening : 5 s de plus, rien ne bouge.
        mark = time.monotonic()
        lab.wait(5.0)
        self.assertEqual(len(lab.resets(since=mark)), 0)
        self.assertEqual(len(lab.ghost_turns(since=mark)), 0)
        self.assertEqual(
            [s for s in lab.presence_states(since=mark) if s == "listening"],
            [],
        )
        self.assertFalse(lab.audio.awake)


class TestF_NoFollowUp(_EchoTestCase):
    """F : sans écoute post-réponse, la réponse ramène directement en veille,
    et aucun callback audio résiduel ne peut rouvrir l'écoute."""

    def test_response_goes_straight_to_hidden_and_stays_there(self) -> None:
        lab = self.make_lab(post_response=False)
        lab.server.next_user_transcripts = ["au revoir"]
        lab.user_speaks(1.2)
        lab.wait_until(lambda: len(lab.user_turns()) >= 1, 6.0, "tour utilisateur")
        lab.wait_until(
            lambda: any(e["kind"] == "sleep" for e in lab.trace()), 6.0,
            "retour en veille immédiat",
        )
        self.assertFalse(lab.audio.awake)

        # Callbacks résiduels simulés (ancienne session, vieux timer) :
        mark = time.monotonic()
        lab.audio.clear_output()        # vieux on_interrupted
        lab.audio.extend_listening()    # vieux turn_complete
        lab.wait(3.0)

        self.assertEqual(len(lab.resets(since=mark)), 0)
        self.assertEqual(lab.presence_states(since=mark), [])
        self.assertFalse(lab.audio.awake)


class TestG_SessionChurn(_EchoTestCase):
    """G : déconnexion/reconnexion pendant la lecture d'une réponse (chemin
    « changement de voix ») — l'ancienne session ne fuit pas de micro, la
    nouvelle n'hérite d'aucun état audio fantôme."""

    def test_reconnect_during_playback_does_not_create_ghosts(self) -> None:
        lab = self.make_lab(response_seconds=1.6)
        lab.server.next_user_transcripts = ["bonjour"]
        lab.user_speaks(1.2)
        lab.wait_until(
            lambda: any(e["kind"] == "mic_gate" and not e.get("state")
                        for e in lab.trace()),
            6.0, "début de réponse",
        )
        # Coupure réseau brutale pendant la lecture de la réponse.
        lab.server.drop_connection()
        lab.wait_until(lambda: lab.server.connect_count >= 2, 8.0,
                       "reconnexion")
        # La reprise a rétabli la session (handle serveur).
        lab.wait(2.0)

        silence_start = time.monotonic()
        lab.wait(min(SILENCE_SECONDS, 12.0))

        self.assertEqual(len(lab.ghost_turns(since=silence_start)), 0)
        self.assertEqual(len(lab.resets(since=silence_start)), 0)
        # La reconnexion a rétabli la continuité PAR L'UN DES DEUX CHEMINS
        # légitimes : reprise serveur (handle) OU session neuve + rejeu du
        # contexte local. La coupure a lieu avant la fin du tour : le handle
        # n'a pas pu être livré, le rejeu est alors le chemin attendu.
        self.assertGreaterEqual(lab.server.connect_count, 2)
        self.assertTrue(
            lab.server.resumed_count >= 1 or lab.gemini.context_seeded,
            "ni reprise de session ni rejeu de contexte après reconnexion",
        )
        # Le rejeu (recap 2.x) rejoue de l'AUDIO : la porte anti-écho l'a
        # couvert lui aussi, sinon des fantômes auraient été committés.
        self.assertFalse(lab.audio.awake)


class TestListenModeRenewal(_EchoTestCase):
    """Mode écoute continue : le renouvellement de la fenêtre est SILENCIEUX
    (aucun réveil, aucun « Je vous écoute », aucun tour fantôme) — à
    distinguer du bug : ici aucun son n'est émis."""

    def test_renewal_is_silent_and_creates_no_turns(self) -> None:
        lab = self.make_lab(listen_mode=True)
        lab.server.next_user_transcripts = ["bonjour"]
        lab.user_speaks(1.2)
        self.settle_response(lab)

        mark = time.monotonic()
        lab.wait(min(SILENCE_SECONDS, 12.0))  # dépasse la fenêtre de 8 s

        renewals = [
            e for e in lab.resets(since=mark) if e["reason"] == "listen_mode_renew"
        ]
        # Le renouvellement a bien eu lieu (mode écoute continue)...
        self.assertGreaterEqual(len(renewals), 1)
        # ...mais SANS réveil, SANS tour, SANS « Je vous écoute ».
        self.assertEqual(len(lab.ghost_turns(since=mark)), 0)
        self.assertEqual(
            [e for e in lab.trace() if e["kind"] == "wake" and e["ts"] >= mark],
            [],
        )
        self.assertTrue(lab.audio.awake)  # le mode garde Jarvis éveillé
        self.assertNotIn("hidden", lab.presence_states(since=mark))


# ---------------------------------------------------------------------------
# §10 : test de stabilité prolongé
# ---------------------------------------------------------------------------


class TestStability(_EchoTestCase):
    """interaction -> silence 30 s -> interaction -> silence 30 s ->
    interaction -> silence 60 s : compteurs exacts, aucune croissance."""

    def test_long_run_counters_are_exact(self) -> None:
        if FAST:
            self.skipTest("mode rapide (JARVIS_ECHO_FAST=1)")
        lab = self.make_lab()
        gaps = (30.0, 30.0, 60.0)

        for index, gap in enumerate(gaps):
            lab.server.next_user_transcripts = [f"phrase {index + 1}"]
            if index > 0:
                # Réveil explicite (le wake word réel n'existe pas en test).
                lab.audio._wake(reason="test_wake", src="test")
            lab.user_speaks(1.2)
            self.settle_response(lab, min_turns=index + 1)
            lab.wait(gap)

        metrics = lab.metrics()
        # --- Compteurs EXACTS (définition même de « pas de croissance
        # inexpliquée » : tout est prédit par le scénario) ---
        self.assertEqual(metrics["ghost_turns"], 0)
        self.assertEqual(metrics["user_turns"], 3)
        self.assertEqual(metrics["wake_events"], 3)        # 1 initial + 2
        self.assertEqual(metrics["sleep_events"], 3)       # 1 par timeout
        self.assertEqual(metrics["timer_resets"], 6)       # 3 wake + 3 tours
        self.assertEqual(
            metrics["resets_by_reason"],
            {"test_wake": 3, "turn_complete": 3},
        )
        self.assertEqual(metrics["sessions"], 1)           # aucune fuite
        self.assertEqual(metrics["listening_transitions"], 6)
        self.assertEqual(len(metrics["timer_epochs"]), metrics["timer_resets"])
        # Les époques sont strictement croissantes : un seul timer logique.
        self.assertEqual(
            metrics["timer_epochs"], list(range(1, metrics["timer_resets"] + 1))
        )
        self.assertFalse(lab.audio.awake)


# ---------------------------------------------------------------------------
# §12 : performance de la porte
# ---------------------------------------------------------------------------


class TestGatePerformance(unittest.TestCase):
    """La porte anti-écho ne doit pas coûter cher : un verrou + une somme
    sur une file courte, appelé à chaque bloc de 80 ms."""

    def test_gate_cost_per_block_is_negligible(self) -> None:
        audio = AudioIO(lambda pcm: None)
        audio.running = True
        # File chargée (10 chunks d'une seconde) : le pire cas réaliste.
        for _ in range(10):
            audio.play(_tone(1.0, 4000, AudioIO.OUTPUT_RATE))
        # Échauffement.
        for _ in range(100):
            audio._mic_gate_open()
        start = time.perf_counter()
        iterations = 20_000
        for _ in range(iterations):
            audio._mic_gate_open()
        elapsed = time.perf_counter() - start
        per_call_us = elapsed / iterations * 1e6
        # Budget d'un bloc de 80 ms = 80 000 µs ; on exige < 1 % de ce
        # budget, avec une marge x10 sur la mesure observée (~2-5 µs).
        self.assertLess(
            per_call_us, 800.0,
            f"porte micro trop coûteuse : {per_call_us:.1f} µs/appel",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
