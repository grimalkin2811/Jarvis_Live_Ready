"""Harnais des scénarios réels E1-E10 (``scripts/validation/gemini_reel_dashboard.py``).

Non-régression PR #50 (validation Windows du 2026-10-16) : l'ancien harnais
envoyait chaque énoncé puis COUPAIT le flux audio, sans ``audio_stream_end``.
La VAD automatique de Gemini Live suppose un flux continu ; en production,
``AudioIO`` transmet le micro en continu tant que la conversation est ouverte.
L'ancien auto-test ne pouvait pas le voir : son faux serveur clôturait un
énoncé après un nombre fixe de blocs, sans VAD.

Le faux serveur de l'auto-test a désormais une VAD par énergie : ces tests
vérifient qu'il refuse de clore un tour quand le flux s'arrête après
l'énoncé (comme le vrai service) et qu'il le clôt quand le flux continue —
l'auto-test aurait donc révélé le défaut. Ils ne disent RIEN du service réel.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts", "validation"))

import gemini_reel_dashboard as harnais  # noqa: E402

BLOC = 1280 * 2  # 80 ms de micro, 16 kHz mono int16 (AudioIO.INPUT_BLOCKSIZE)


def _blocs(pcm: bytes) -> list[bytes]:
    return [pcm[i:i + BLOC] for i in range(0, len(pcm), BLOC)]


class SelftestServerVadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from tests.real_gemini_harness import RealAcousticPath
        from tests.voice_harness import VoiceHarness

        self.server, _client = harnais.installer_faux_serveur(RealAcousticPath())
        self.harness = VoiceHarness(self.server)
        await self.harness.start()
        await self.harness.wait_ready()

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    async def _wait_turns(self, count: int, timeout: float) -> bool:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if len(self.harness.turns) >= count:
                return True
            await asyncio.sleep(0.02)
        return len(self.harness.turns) >= count

    async def test_turn_closes_only_while_the_microphone_keeps_streaming(self):
        from tests.echo_loop_harness import _tone_pcm

        gemini = self.harness.gemini
        self.server.next_user_transcripts[:] = ["Quelle est la capitale de l'Espagne ? Réponds en un mot."]
        for bloc in _blocs(_tone_pcm(1.0, 2600.0, 16000)):  # l'énoncé
            await gemini.send_audio(bloc)
        # Ancien harnais : plus aucun audio après l'énoncé (ni audio_stream_end).
        self.assertFalse(await self._wait_turns(1, 1.5),
                         "flux coupé après l'énoncé : la VAD ne doit jamais clore le tour")
        self.assertEqual(self.server.turns, [], "aucun tour utilisateur commis côté serveur")
        # Production : le micro continue de transmettre (bruit de fond).
        bruit = np.random.default_rng(7).normal(0.0, 12.0, 1280).astype(np.int16).tobytes()
        for _ in range(10):
            await gemini.send_audio(bruit)
        self.assertTrue(await self._wait_turns(1, 3.0), "flux continu : la fin de parole est détectée")
        self.assertEqual(self.harness.turns[-1].assistant_text, "Madrid.")

    async def test_speech_during_a_streamed_answer_interrupts_it(self):
        from tests.echo_loop_harness import _tone_pcm

        gemini = self.harness.gemini
        bruit = np.random.default_rng(8).normal(0.0, 12.0, 1280).astype(np.int16).tobytes()
        self.server.next_user_transcripts[:] = ["Décris-moi en détail le cycle de l'eau."]
        for bloc in _blocs(_tone_pcm(1.0, 2600.0, 16000)) + [bruit] * 9:
            await gemini.send_audio(bloc)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 3.0
        while not gemini.speaking and loop.time() < deadline:
            await asyncio.sleep(0.02)
        self.assertTrue(gemini.speaking, "réponse longue diffusée dans le temps")
        gemini.request_interrupt()  # ce que fait le barge-in d'AudioIO (on_barge_in)
        self.server.next_user_transcripts[:] = ["Stop, arrête-toi s'il te plaît."]
        for bloc in _blocs(_tone_pcm(0.6, 2600.0, 16000)):
            await gemini.send_audio(bloc)
        deadline = loop.time() + 3.0
        while "INTERRUPTION" not in [e["kind"] for e in gemini.trace_events()] and loop.time() < deadline:
            await asyncio.sleep(0.02)
        self.assertIn("INTERRUPTION", [e["kind"] for e in gemini.trace_events()])


class DiagnosticTests(unittest.TestCase):
    def test_exception_description_keeps_api_details_and_masks_keys(self):
        class FakeAPIError(Exception):
            code = 1011
            status = "INTERNAL"

        cle = "AIza" + "SyD-0123456789abcdefghijkl"
        try:
            try:
                raise FakeAPIError(f"keepalive ping timeout (clé {cle})")
            except FakeAPIError as inner:
                raise RuntimeError("session perdue") from inner
        except RuntimeError as exc:
            texte = harnais.decrire_exception(exc)
        self.assertIn("RuntimeError: session perdue", texte)
        self.assertIn("FakeAPIError: keepalive ping timeout", texte)
        self.assertIn("code=1011", texte)
        self.assertIn("status=INTERNAL", texte)
        self.assertNotIn(cle, texte)
        self.assertIn("<clé masquée>", texte)

    def test_stop_utterance_level_is_raised_only_when_too_quiet(self):
        t = np.arange(16000) / 16000.0
        faible = (600 * np.sin(2 * np.pi * 220 * t)).astype(np.int16).tobytes()
        fort = (6000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16).tobytes()
        ajuste = np.frombuffer(harnais.ajuster_niveau(faible, harnais.NIVEAU_STOP), dtype=np.int16).astype(np.float64)
        # Mesure d'AudioIO pour le barge-in : RMS de blocs de 80 ms.
        blocs = np.sqrt(np.mean(ajuste[: ajuste.size // 1280 * 1280].reshape(-1, 1280) ** 2, axis=1))
        self.assertGreaterEqual(float(np.median(blocs)), harnais.NIVEAU_STOP * 0.99)
        self.assertGreater(float(blocs.min()), 900.0, "chaque bloc dépasse le seuil de barge-in")
        self.assertEqual(harnais.ajuster_niveau(fort, harnais.NIVEAU_STOP), fort)


if __name__ == "__main__":
    unittest.main()
