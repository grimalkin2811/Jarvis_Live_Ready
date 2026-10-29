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


    async def test_two_sentences_with_a_pause_give_one_answered_turn(self):
        """Motif réel E01/E07/E09/E10 (validation du 2026-10-16) : la VAD clôt la
        parole à la pause, la phrase suivante annule la génération encore muette
        (``interrupted`` puis ``turn_complete``). Le tour reste ouvert et reçoit LA
        réponse ; aucune relance (sur 28234b0 : relance, micro retenu, délai)."""
        gemini = self.harness.gemini
        prompt = harnais.PROMPTS_VOIX["VA"]
        self.server.next_user_transcripts[:] = harnais.phrases(prompt)
        bruit = np.random.default_rng(9).normal(0.0, 12.0, 1280).astype(np.int16).tobytes()
        for bloc in _blocs(harnais.enonce_synthetique(prompt)) + [bruit] * 10:
            await gemini.send_audio(bloc)
            await asyncio.sleep(0.01)
        self.assertTrue(await self._wait_turns(1, 5.0), "aucune fin de tour")
        kinds = [e["kind"] for e in gemini.trace_events()]
        self.assertIn("TURN_KEPT_OPEN", kinds, "le motif réel (génération annulée muette) n'a pas été exercé")
        self.assertNotIn("EMPTY_GENERATION_RETRY", kinds)
        self.assertEqual(len(self.harness.turns), 1)
        self.assertEqual(self.harness.turns[-1].assistant_text, "Madrid.")
        users = [m.text for m in self.harness.conversation.get_messages() if m.role == "user"]
        self.assertEqual(users, [prompt], "une seule demande, les deux phrases réunies")

    async def test_client_content_during_speech_waits_for_the_end_of_the_activity(self):
        """Règle reproduite : un ``clientContent`` pendant une parole attend la fin de
        celle-ci ; sans ``audio_stream_end`` et sans audio, elle ne vient jamais."""
        from tests.echo_loop_harness import _tone_pcm

        gemini = self.harness.gemini
        session = gemini.session
        self.server.next_user_transcripts[:] = ["Bonjour"]
        for bloc in _blocs(_tone_pcm(0.5, 2600.0, 16000)):
            await gemini.send_audio(bloc)
        await session.send_client_content(turns=[{"role": "user", "parts": [{"text": "Un texte"}]}],
                                          turn_complete=True)
        self.assertEqual(self.server.contenus_differes, 1)
        await asyncio.sleep(0.3)
        self.assertEqual(self.harness.turns, [], "rien ne doit répondre tant que la parole n'est pas close")
        await session.send_realtime_input(audio_stream_end=True)
        self.assertTrue(await self._wait_turns(1, 5.0), "audio_stream_end clôt la parole et libère le texte")


class AttributionTests(unittest.TestCase):
    def test_a_turn_without_user_request_is_never_the_answer_to_an_utterance(self):
        """E08 sur le PC : la réponse du modèle au rejeu de contexte (tour sans
        demande) était prise pour la réponse au message suivant."""

        class Labo:
            turns = [{"user": "", "assistant": "Je suis là, je t'écoute."},
                     {"user": "Quel est mon prénom ?", "assistant": "Tu t'appelles Ada."}]

        scenarios = harnais.Scenarios.__new__(harnais.Scenarios)
        scenarios.labo = Labo()
        self.assertEqual(scenarios.reponse_vocale(0)["assistant"], "Tu t'appelles Ada.")
        self.assertIsNone(scenarios.reponse_vocale(2))

    def test_multi_sentence_prompts_are_split_like_the_real_pauses(self):
        self.assertEqual(harnais.phrases(harnais.PROMPTS_VOIX["VA"]),
                         ["Quelle est la capitale de l'Espagne ?", "Réponds en un mot."])
        self.assertEqual(len(harnais.phrases(harnais.PROMPTS_VOIX["V7"])), 3)
        self.assertEqual(harnais.phrases(harnais.PROMPTS_VOIX["STOP"]), [harnais.PROMPTS_VOIX["STOP"]])


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
