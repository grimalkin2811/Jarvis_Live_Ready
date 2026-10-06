"""Tests de régression du DOUBLE CYCLE (v1.7.3) — « Dis-moi tout » superposé.

Symptôme rapporté après v1.7.2
-------------------------------
Une seule phrase utilisateur produit DEUX cycles logiques : la réponse
correcte ET une seconde réponse générique (« Dis-moi tout ») qui se
superpose à la première. Contrairement au bug v1.7.2 (boucle stable
d'échos toutes les ~2 s), il n'y a ici qu'UN seul cycle fantôme, déclenché
au moment précis où Jarvis commence à répondre.

Cause racine (démontrée ci-dessous, et par `docs/RAPPORT_DOUBLE_CYCLE_v1.7.3.md`)
----------------------------------------------------------------------------
Le pont micro (`mic()` dans `src/main.py` / `src/ui.py`) tourne sur le
THREAD AUDIO temps réel :

    if gemini.can_send():                                  # (1) vérifié ICI
        asyncio.run_coroutine_threadsafe(gemini.send_audio(pcm), loop)  # (2) exécuté PLUS TARD

Entre (1) et (2), le thread audio ne réévalue JAMAIS `can_send()`. Si la
boucle asyncio met du temps à traiter la coroutine planifiée — parce
qu'elle est occupée à exécuter un callback synchrone (`AudioIO.play()` :
gain, verrou, carte son) ou à recevoir le début de la réponse du serveur —
alors `gemini.speaking` peut devenir `True` ENTRE (1) et (2). Le bloc,
capturé quand tout allait bien, est quand même envoyé au serveur qui est
déjà en train de générer une réponse au tour 1 : il l'interprète comme le
DÉBUT D'UN SECOND TOUR. Ce second tour, ne contenant qu'un souffle ou un
reste de parole sans contexte, produit une réponse générique du type
« Dis-moi tout », dont l'audio est mis en file JUSTE APRÈS celui du tour 1
— d'où la superposition perçue par l'utilisateur.

Ce mécanisme exact avait déjà été repéré sur le runner Windows (commit
« test(echo): TestC tolère le traînard d'ordonnancement… ») MAIS la
réponse apportée à l'époque avait été d'AFFAIBLIR l'assertion du test
(tolérer 1 bloc traînard) plutôt que de fermer la course. C'est
exactement la course que ce module ferme et prouve fermée.

Le correctif (`GeminiLive.send_audio`) ré-évalue `can_send()` À NEUF sur la
boucle asyncio, juste avant l'écriture réseau — le seul endroit où l'état
ne peut pas changer sous nos pieds (asyncio est mono-thread). Un second
garde-fou (`capture_generation`) rejette aussi un bloc dont la session a
été remplacée (reconnexion) entre la capture et l'exécution.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

import numpy as np  # noqa: E402

# La fausse carte son doit être installée avant src.audio (fait par le
# harness écho). Import volontairement en premier.
from tests.echo_loop_harness import EchoLab, VadLiveServer, AcousticPath  # noqa: E402
from tests.live_harness import FakeClient, FakeLiveServer  # noqa: E402

from src.audio import AudioIO  # noqa: E402
from src.conversation import ConversationContext  # noqa: E402
from src.gemini_live import GeminiLive  # noqa: E402


def _tone(seconds: float, rms: float, rate: int) -> bytes:
    n = int(rate * seconds)
    t = np.arange(n) / rate
    carrier = np.sin(2 * np.pi * 210.0 * t)
    return (np.clip(carrier * rms * np.sqrt(2.0), -32768, 32767)).astype(np.int16).tobytes()


def _make_gemini(server) -> GeminiLive:
    return GeminiLive(
        key="test-key",
        model="gemini-2.5-flash-native-audio-preview-12-2025",
        user="Test",
        on_audio=lambda pcm: None,
        conversation=ConversationContext(max_turns=20, max_tokens=4096),
    )


# ---------------------------------------------------------------------------
# §8 mission : unit tests rapides et déterministes de la ré-validation
# ---------------------------------------------------------------------------


class SendAudioFreshnessTests(unittest.IsolatedAsyncioTestCase):
    """``send_audio`` doit revalider l'état à l'EXÉCUTION, pas faire confiance
    à un ``can_send()`` évalué plus tôt sur un autre thread (Test D/E §10)."""

    async def asyncSetUp(self) -> None:
        self.server = FakeLiveServer(semantics="2.x")
        self.gemini = _make_gemini(self.server)
        self.gemini.client = FakeClient(self.server)
        await self.gemini.connect()
        self.pcm = b"\x01\x02" * 640

    async def test_stale_speaking_flag_is_dropped_at_execution_time(self) -> None:
        """Test D (§10) : capturé quand can_send()==True, exécuté après que
        Jarvis a commencé à parler -> le bloc est ignoré, pas envoyé."""
        self.assertTrue(self.gemini.can_send())  # (1) vrai au moment de la capture

        # Le serveur a commencé à répondre ENTRE la capture et l'exécution
        # de send_audio (c'est exactement ce qui se passe quand la boucle
        # asyncio traite le début du tour AVANT la coroutine déjà planifiée
        # par le thread micro).
        self.gemini.speaking = True

        await self.gemini.send_audio(self.pcm, capture_generation=self.gemini.session_generation)

        audio_events = self.server.events("realtime_audio")
        self.assertEqual(audio_events, [], "le bloc périmé n'aurait jamais dû partir")
        self.assertEqual(self.gemini.stale_audio_dropped, 1)
        kinds = [e["kind"] for e in self.gemini.trace_events()]
        self.assertIn("AUDIO_SEND_DROPPED_STALE", kinds)

    async def test_stale_generation_after_reconnect_is_dropped(self) -> None:
        """Test E (§10), variante reconnexion : le bloc appartient à une
        session qui n'existe plus (la conversation a été rejouée ailleurs)."""
        captured_generation = self.gemini.session_generation
        # Reconnexion (nouvelle session) pendant que le bloc était « en
        # transit » sur la boucle asyncio.
        await self.gemini.close()
        await self.gemini.connect()
        self.assertNotEqual(self.gemini.session_generation, captured_generation)

        await self.gemini.send_audio(self.pcm, capture_generation=captured_generation)

        audio_events = self.server.events("realtime_audio")
        self.assertEqual(audio_events, [])
        self.assertEqual(self.gemini.stale_audio_dropped, 1)

    async def test_fresh_audio_is_still_sent_normally(self) -> None:
        """Non-régression : un bloc capturé ET exécuté dans un état valide
        part bien vers Gemini (le correctif ne doit pas rendre Jarvis sourd)."""
        await self.gemini.send_audio(self.pcm, capture_generation=self.gemini.session_generation)
        audio_events = self.server.events("realtime_audio")
        self.assertEqual(len(audio_events), 1)
        self.assertEqual(self.gemini.stale_audio_dropped, 0)

    async def test_interrupt_window_still_allows_send_while_speaking(self) -> None:
        """Le barge-in doit continuer à fonctionner : pendant la fenêtre
        d'interruption locale, l'audio DOIT partir même si speaking=True —
        c'est ainsi que le serveur entend « stop »."""
        self.gemini.speaking = True
        self.gemini.request_interrupt()

        await self.gemini.send_audio(self.pcm, capture_generation=self.gemini.session_generation)

        audio_events = self.server.events("realtime_audio")
        self.assertEqual(len(audio_events), 1, "le barge-in ne doit pas être bloqué par le correctif")
        self.assertEqual(self.gemini.stale_audio_dropped, 0)

    async def test_tool_active_is_also_revalidated(self) -> None:
        self.gemini.tool_active = True
        await self.gemini.send_audio(self.pcm, capture_generation=self.gemini.session_generation)
        self.assertEqual(self.server.events("realtime_audio"), [])
        self.assertEqual(self.gemini.stale_audio_dropped, 1)

    async def test_stale_turn_epoch_is_dropped_even_when_idle_again(self) -> None:
        """Test E (§10), cas précis que ``speaking`` seul ne peut PAS
        détecter : le bloc est capturé pendant le tour N, exécuté APRÈS que
        le tour N a été clos ET que Jarvis est redevenu totalement idle
        (``speaking`` == False) — un état indiscernable d'un vrai suivi si
        on ne regarde que ``speaking``. ``turn_epoch`` lève l'ambiguïté."""
        capture_epoch = self.gemini.turn_epoch  # tour N encore ouvert/à venir
        self.gemini._begin_turn_if_needed()      # le tour N s'ouvre...
        self.gemini.speaking = True               # ...Jarvis répond...
        self.gemini._finish_turn()               # ...puis le tour N se clôt...
        self.gemini.speaking = False              # ...et Jarvis redevient idle.

        # Sans capture_turn_epoch, ce bloc serait accepté (speaking==False,
        # can_send()==True) alors qu'il appartenait à un tour déjà terminé.
        await self.gemini.send_audio(
            self.pcm,
            capture_generation=self.gemini.session_generation,
            capture_turn_epoch=capture_epoch,
        )

        self.assertEqual(self.server.events("realtime_audio"), [])
        self.assertEqual(self.gemini.stale_audio_dropped, 1)

    async def test_fresh_turn_epoch_after_close_is_a_legitimate_follow_up(self) -> None:
        """Non-régression : un VRAI suivi, capturé APRÈS la fermeture du
        tour précédent, ne doit jamais être confondu avec un bloc périmé."""
        self.gemini._begin_turn_if_needed()
        self.gemini._finish_turn()
        capture_epoch = self.gemini.turn_epoch  # capturé APRÈS la fermeture

        await self.gemini.send_audio(
            self.pcm,
            capture_generation=self.gemini.session_generation,
            capture_turn_epoch=capture_epoch,
        )

        self.assertEqual(len(self.server.events("realtime_audio")), 1)
        self.assertEqual(self.gemini.stale_audio_dropped, 0)


# ---------------------------------------------------------------------------
# §4/§5/§10 : reproduction bout en bout (vrai pipeline, vrais threads)
# ---------------------------------------------------------------------------


class TurnRaceIntegrationTests(unittest.TestCase):
    """Reproduction §10 Test D/E sur le VRAI pipeline (vraie ``AudioIO``,
    vrai pont micro ``mic() -> run_coroutine_threadsafe -> send_audio()``,
    vraie ``GeminiLive``) : un bloc micro réel, capturé pendant que
    ``can_send()`` valait vrai, dont l'EXÉCUTION est retardée (comme le
    ferait une boucle asyncio occupée sur un PC réel chargé) jusqu'à APRÈS
    que Jarvis ait commencé à répondre, ne doit jamais atteindre le
    serveur : il appartient à un tour déjà clos.
    """

    def make_lab(self, **kwargs) -> EchoLab:
        lab = EchoLab(**kwargs)
        self.addCleanup(lab.stop)
        lab.start()
        return lab

    def _delay_next_send(self, lab: EchoLab, *, hold_seconds: float = 0.0,
                          until_speaking: bool = False, max_wait: float = 6.0) -> dict:
        """Retarde l'EXÉCUTION du prochain ``send_audio`` planifié — pas sa
        capture ni sa vérification côté thread micro — exactement le
        scénario « capturé à T0, exécuté à T0+500ms » du §10 (Test D/E) :
        le pont micro a déjà décidé d'envoyer (``can_send()`` était vrai),
        la coroutine est simplement en retard sur la boucle asyncio (comme
        le serait une boucle occupée par un callback de lecture audio ou
        par le traitement d'un message réseau).

        Si ``until_speaking`` est vrai, le retard dure jusqu'à ce que
        ``gemini.speaking`` devienne vrai (borné par ``max_wait``) au lieu
        d'une durée fixe — ceci rend le test robuste au chronométrage réel
        (peu importe exactement quand la réponse démarre, on relâche le
        bloc PILE au bon moment pour observer la course)."""
        state: dict = {"armed": True, "held_pcm": None, "released": False}
        original_send_audio = lab.gemini.send_audio

        async def delayed_send_audio(pcm, **kwargs):
            if state["armed"]:
                state["armed"] = False
                state["held_pcm"] = pcm
                if until_speaking:
                    deadline = time.monotonic() + max_wait
                    while not lab.gemini.speaking and time.monotonic() < deadline:
                        await asyncio.sleep(0.01)
                if hold_seconds:
                    await asyncio.sleep(hold_seconds)
                state["released"] = True
            return await original_send_audio(pcm, **kwargs)

        lab.gemini.send_audio = delayed_send_audio
        return state

    def test_delayed_capture_after_turn_closes_is_ignored(self) -> None:
        """Test D (§10) : un bloc capturé pendant la phrase, dont l'exécution
        est retardée jusqu'à ce que Jarvis ait commencé à répondre, ne doit
        produire NI second tour NI second envoi réseau."""
        lab = self.make_lab(echo_gain=0.0, response_seconds=1.5)
        lab.server.next_user_transcripts = ["quelle heure est-il"]

        # Retarde le PROCHAIN bloc micro planifié : il ne sera relâché que
        # lorsque Jarvis aura commencé à répondre (``gemini.speaking`` ==
        # True). Ce bloc a été capturé alors que can_send() valait vrai
        # (Jarvis n'avait pas encore répondu) — exactement comme le ferait
        # le thread audio réel sur une boucle asyncio momentanément
        # occupée par un callback de lecture ou de réseau.
        state = self._delay_next_send(lab, until_speaking=True)

        lab.user_speaks(1.5, rms=2600.0)

        # Attend que le bloc retardé ait effectivement été capturé...
        lab.wait_until(lambda: state["held_pcm"] is not None, 4.0,
                       "capture du bloc à retarder")
        speaking_started_at = time.monotonic()

        # ... puis qu'il ait été relâché (relâché seulement une fois
        # gemini.speaking devenu vrai — c'est la fenêtre de course).
        lab.wait_until(lambda: state["released"], 8.0, "exécution tardive du bloc")
        self.assertTrue(lab.gemini.speaking, "le test est invalide : Jarvis n'a jamais répondu")
        lab.wait(2.5)

        # --- Propriété de sûreté : au plus 1 tour utilisateur actif ---
        self.assertEqual(
            len(lab.server.turns), 1,
            f"le bloc périmé a créé un second tour : {lab.server.turns}",
        )
        self.assertEqual(lab.server.turns[0]["text"], "quelle heure est-il")
        self.assertEqual(len(lab.ghost_turns()), 0)

        # --- Preuve directe que c'est bien le garde-fou qui a agi ---
        self.assertGreaterEqual(
            lab.gemini.stale_audio_dropped, 1,
            "le bloc retardé aurait dû être détecté comme périmé et abandonné",
        )
        stale_events = [
            e for e in lab.gemini.trace_events()
            if e["kind"] == "AUDIO_SEND_DROPPED_STALE"
        ]
        self.assertTrue(stale_events, "aucun événement AUDIO_SEND_DROPPED_STALE tracé")
        # L'abandon a bien eu lieu APRÈS le début de la réponse (c'est la
        # course elle-même : capturé avant, exécuté après).
        self.assertGreaterEqual(stale_events[-1]["ts"], speaking_started_at)

    def test_delayed_capture_after_response_also_ignored(self) -> None:
        """Test E (§10) : même scénario, mais le retard s'étend jusqu'à
        APRÈS la fin complète de la réponse — le bloc doit rester ignoré."""
        lab = self.make_lab(echo_gain=0.0, response_seconds=0.5)
        lab.server.next_user_transcripts = ["annule le minuteur"]

        # Relâché seulement après le début de la réponse, PUIS on attend
        # encore 1 s (la réponse fait 0.5 s) pour couvrir aussi le
        # turn_complete — le bloc reste périmé même après la fin complète
        # du tour, pas seulement pendant son tout début.
        state = self._delay_next_send(lab, until_speaking=True, hold_seconds=1.0)

        lab.user_speaks(1.2, rms=2600.0)
        lab.wait_until(lambda: state["held_pcm"] is not None, 4.0, "capture du bloc à retarder")

        lab.wait_until(lambda: state["released"], 8.0, "exécution tardive du bloc")
        lab.wait_until(lambda: len(lab.server.turns) >= 1, 6.0, "réponse complète")
        lab.wait(2.0)

        self.assertEqual(len(lab.server.turns), 1, f"tours: {lab.server.turns}")
        self.assertEqual(len(lab.ghost_turns()), 0)
        self.assertGreaterEqual(lab.gemini.stale_audio_dropped, 1)

    def test_three_utterances_under_race_pressure_yield_exactly_three_turns(self) -> None:
        """Test H (§10), avec la pression de course appliquée à CHAQUE tour :
        3 phrases -> exactement 3 tours utilisateur, jamais plus, malgré un
        bloc périmé injecté à chaque cycle."""
        lab = self.make_lab(echo_gain=0.0, response_seconds=0.6)
        lab.server.next_user_transcripts = ["un", "deux", "trois"]

        for i in range(3):
            state = self._delay_next_send(lab, until_speaking=True)
            lab.user_speaks(1.0, rms=2600.0)
            lab.wait_until(lambda: state["held_pcm"] is not None, 4.0,
                           f"capture (tour {i + 1})")
            lab.wait_until(lambda: state["released"], 8.0, f"exécution tardive (tour {i + 1})")
            lab.wait_until(lambda i=i: len(lab.server.turns) >= i + 1, 8.0,
                           f"tour {i + 1} committé")
            lab.wait(1.5)

        self.assertEqual(len(lab.server.turns), 3, f"tours: {lab.server.turns}")
        self.assertEqual([t["text"] for t in lab.server.turns], ["un", "deux", "trois"])
        self.assertEqual(len(lab.ghost_turns()), 0)
        self.assertGreaterEqual(lab.gemini.stale_audio_dropped, 3)


if __name__ == "__main__":
    unittest.main()
