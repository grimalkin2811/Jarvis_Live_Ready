"""Arrêt MANUEL de la réponse (bouton Stop du Dashboard, menu radial, touche S).

Correctif PR #50 (exigence « l'interruption arrête réellement la réponse »).
Un clic sur Stop est silencieux : le serveur n'entend rien et poursuit sa
génération. AVANT : passé la fenêtre d'interruption de 4 s (prévue pour les
fausses détections du barge-in vocal), la réponse stoppée REPRENAIT au milieu
d'une phrase et le micro se refermait (« jarvis parle »).

FAUX SERVEUR UNIQUEMENT (``tests/live_harness``) : logique du client, pas le
service réel.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import src.gemini_live as gemini_module  # noqa: E402
from tests.live_harness import (  # noqa: E402
    FakeLiveServer,
    _Message,
    _ServerContent,
    msg_interrupted,
    msg_turn_complete,
    msg_user_transcript,
)
from tests.voice_harness import VoiceHarness  # noqa: E402

WINDOW = 0.3  # fenêtre d'interruption accélérée (4 s en production)
AUDIO = b"\x01\x02" * 64


class _Base(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._patch = mock.patch.object(gemini_module, "INTERRUPT_WINDOW_SECONDS", WINDOW)
        self._patch.start()
        self.server = FakeLiveServer(semantics="2.x")
        self.harness = VoiceHarness(self.server)
        self.gemini = self.harness.gemini
        await self.harness.start()
        await self.harness.wait_ready()

    async def asyncTearDown(self) -> None:
        await self.harness.stop()
        self._patch.stop()

    async def _start_long_answer(self) -> None:
        session = self.server.current
        await session._emit(msg_user_transcript("Raconte une longue histoire"))
        await session._emit(_Message(_ServerContent(model_text="Il était une fois", audio=AUDIO)))
        for _ in range(100):
            if self.gemini.speaking:
                return
            await asyncio.sleep(0.005)
        self.fail("la réponse aurait dû commencer")

    async def _keep_streaming(self, seconds: float) -> list[tuple[bool, str]]:
        """Le serveur, qui n'a rien entendu, continue sa réponse."""
        session = self.server.current
        gates = []
        for index in range(int(seconds / 0.05)):
            await asyncio.sleep(0.05)
            await session._emit(_Message(_ServerContent(model_text=f" suite {index}", audio=AUDIO)))
            gates.append(self.gemini._can_send_now())
        await asyncio.sleep(0.02)
        return gates


class ManualStopTests(_Base):
    async def test_manual_stop_never_resumes_the_stopped_answer(self):
        await self._start_long_answer()
        played = len(self.harness.audio_chunks)
        self.gemini.request_interrupt(manual=True)
        gates = await self._keep_streaming(WINDOW * 3)  # bien au-delà de la fenêtre
        self.assertEqual(len(self.harness.audio_chunks), played,
                         "aucun morceau de la réponse stoppée ne doit être rejoué")
        self.assertTrue(all(gate[0] for gate in gates),
                        f"le micro reste ouvert pour que l'utilisateur puisse parler : {gates}")
        # Fin du tour stoppé côté serveur : retour au fonctionnement normal.
        await self.server.current._emit(msg_turn_complete())
        await asyncio.sleep(0.05)
        self.assertFalse(self.gemini.interrupt_active())
        self.assertEqual(await self.harness.speak("Quelle heure est-il ?"), "D'accord.")
        self.assertGreater(len(self.harness.audio_chunks), played, "la réponse suivante est bien jouée")

    async def test_server_interruption_ends_the_manual_stop(self):
        await self._start_long_answer()
        self.gemini.request_interrupt(manual=True)
        self.assertTrue(self.gemini.interrupt_active())
        await self.server.current._emit(msg_interrupted())
        await asyncio.sleep(0.05)
        self.assertFalse(self.gemini.interrupt_active())
        self.assertIsNone(self.gemini._manual_stop_epoch)

    async def test_manual_stop_after_the_server_finished_never_mutes_the_next_answer(self):
        await self.harness.speak("Bonjour")  # génération terminée, seule la lecture locale traînerait
        self.assertFalse(self.gemini.speaking)
        self.gemini.request_interrupt(manual=True)
        self.assertIsNone(self.gemini._manual_stop_epoch, "rien à jeter côté serveur")
        await asyncio.sleep(WINDOW + 0.05)
        before = len(self.harness.audio_chunks)
        self.assertEqual(await self.harness.speak("Quelle heure est-il ?"), "D'accord.")
        self.assertGreater(len(self.harness.audio_chunks), before)

    async def test_voice_barge_in_keeps_its_false_detection_safety_net(self):
        """Non-régression : un barge-in VOCAL non confirmé laisse reprendre la lecture."""
        await self._start_long_answer()
        played = len(self.harness.audio_chunks)
        self.gemini.request_interrupt()  # on_barge_in (détection vocale)
        await self._keep_streaming(WINDOW * 3)
        self.assertGreater(len(self.harness.audio_chunks), played,
                           "fausse détection : la lecture reprend à l'expiration de la fenêtre")


class UiWiringTests(unittest.TestCase):
    def test_stop_handler_empties_output_then_marks_a_manual_stop(self):
        from src.ui import _manual_stop

        calls: list[tuple] = []

        class Audio:
            def stop_speaking(self):
                calls.append(("audio.stop_speaking",))
                return True

        class Gemini:
            def request_interrupt(self, manual=False):
                calls.append(("gemini.request_interrupt", manual))

        _manual_stop(Audio(), Gemini())
        self.assertEqual(calls, [("audio.stop_speaking",), ("gemini.request_interrupt", True)])
        _manual_stop(None, None)  # backend vocal absent : aucun effet, aucune exception


if __name__ == "__main__":
    unittest.main()
