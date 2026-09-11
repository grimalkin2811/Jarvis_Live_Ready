"""Tests de l'interruption de Jarvis (« stop » pendant une réponse).

Aucun matériel audio ni réseau n'est utilisé : les flux sounddevice sont
factices et Gemini est simulé. On vérifie :

* la détection vocale (barge-in) ne se déclenche que si Jarvis parle,
  après la période de grâce, sur plusieurs blocs consécutifs ;
* Jarvis ne s'interrompt pas lui-même : l'écho de ses enceintes est appris
  comme plancher de bruit ;
* la sortie audio est vidée immédiatement et le backend prévenu ;
* les blocs micro captés juste avant l'interruption sont réémis (le mot
  « stop » arrive entier à Gemini) ;
* côté Gemini : le micro repasse, l'audio du modèle est jeté, puis tout
  revient à la normale (confirmation serveur, fin de tour ou expiration).
"""

import asyncio
import os
import sys
import time
import types
import unittest

# Aucun téléchargement de modèles pendant les tests (hors ligne).
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np  # noqa: E402


def _install_fake_sounddevice() -> None:
    if "sounddevice" in sys.modules:
        return
    sd = types.ModuleType("sounddevice")

    class _FakeStream:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    sd.RawInputStream = _FakeStream
    sd.RawOutputStream = _FakeStream
    sys.modules["sounddevice"] = sd


_install_fake_sounddevice()

from src.audio import AudioIO  # noqa: E402
from src.gemini_live import GeminiLive  # noqa: E402


def _noise_block(amplitude: int) -> bytes:
    """Bloc micro de 80 ms au niveau RMS voulu (bruit déterministe)."""
    rng = np.random.default_rng(1234)
    samples = rng.normal(0.0, max(1.0, float(amplitude)), AudioIO.INPUT_BLOCKSIZE)
    return np.clip(samples, -32768, 32767).astype(np.int16).tobytes()


LOUD = _noise_block(4000)     # utilisateur qui parle près du micro
QUIET = _noise_block(80)      # silence relatif (casque)
ECHO = _noise_block(1500)     # voix de Jarvis renvoyée par les enceintes


class _Harness:
    """AudioIO instrumenté : on capture ce qui part vers Gemini."""

    def __init__(self, **kwargs):
        self.sent = []
        self.barge_in_calls = 0

        def _on_input(pcm):
            self.sent.append(pcm)

        def _on_barge_in():
            self.barge_in_calls += 1

        self.audio = AudioIO(_on_input, on_barge_in=_on_barge_in, **kwargs)
        self.audio.running = True
        self.audio.awake = True

    def start_speaking(self, grace_elapsed: bool = True) -> None:
        self.audio.begin_speaking()
        if grace_elapsed:
            # On antidate le début de la réponse pour sortir de la période
            # de grâce sans attendre réellement.
            self.audio._speaking_since -= AudioIO.BARGE_IN_GRACE_SECONDS + 0.1

    def feed(self, block: bytes, times: int = 1) -> None:
        for _ in range(times):
            self.audio._handle_awake_block(block)


class BargeInDetectionTests(unittest.TestCase):
    def test_no_interruption_when_jarvis_is_silent(self) -> None:
        h = _Harness()
        h.feed(LOUD, times=10)
        self.assertEqual(h.barge_in_calls, 0)
        # Le micro continue d'alimenter Gemini normalement.
        self.assertEqual(len(h.sent), 10)

    def test_loud_speech_interrupts_jarvis(self) -> None:
        h = _Harness()
        h.start_speaking()
        h.feed(QUIET, times=3)  # apprentissage du plancher
        h.feed(LOUD, times=AudioIO.BARGE_IN_BLOCKS)
        self.assertEqual(h.barge_in_calls, 1)
        # Jarvis ne parle plus et la file de lecture est vide.
        self.assertFalse(h.audio.speaking)
        self.assertTrue(h.audio.q.empty())
        self.assertEqual(h.audio._queued_bytes, 0)

    def test_short_noise_does_not_interrupt(self) -> None:
        h = _Harness()
        h.start_speaking()
        for _ in range(4):
            # Un seul bloc fort isolé (clic de souris, claquement) suivi de
            # silence : jamais BARGE_IN_BLOCKS d'affilée.
            h.feed(LOUD)
            h.feed(QUIET, times=2)
        self.assertEqual(h.barge_in_calls, 0)

    def test_grace_period_protects_start_of_answer(self) -> None:
        h = _Harness()
        h.start_speaking(grace_elapsed=False)
        h.feed(LOUD, times=AudioIO.BARGE_IN_BLOCKS + 2)
        self.assertEqual(h.barge_in_calls, 0)

    def test_jarvis_does_not_interrupt_himself(self) -> None:
        """L'écho des enceintes est appris : il ne coupe pas la réponse."""
        h = _Harness()
        h.start_speaking(grace_elapsed=False)
        # Pendant la grâce, Jarvis parle fort dans les enceintes.
        h.feed(ECHO, times=6)
        h.audio._speaking_since -= AudioIO.BARGE_IN_GRACE_SECONDS + 0.1
        h.feed(ECHO, times=30)
        self.assertEqual(h.barge_in_calls, 0)

    def test_user_still_wins_over_loud_echo(self) -> None:
        """Même avec des enceintes fortes, la voix proche interrompt."""
        h = _Harness()
        h.start_speaking(grace_elapsed=False)
        h.feed(ECHO, times=6)
        h.audio._speaking_since -= AudioIO.BARGE_IN_GRACE_SECONDS + 0.1
        h.feed(ECHO, times=5)
        h.feed(_noise_block(12000), times=AudioIO.BARGE_IN_BLOCKS)
        self.assertEqual(h.barge_in_calls, 1)

    def test_feature_can_be_disabled(self) -> None:
        h = _Harness(barge_in_provider=lambda: False)
        h.start_speaking()
        h.feed(LOUD, times=AudioIO.BARGE_IN_BLOCKS + 5)
        self.assertEqual(h.barge_in_calls, 0)
        self.assertTrue(h.audio.speaking)

    def test_broken_provider_keeps_feature_on(self) -> None:
        def _boom():
            raise RuntimeError("réglages indisponibles")

        h = _Harness(barge_in_provider=_boom)
        h.start_speaking()
        h.feed(QUIET, times=2)
        h.feed(LOUD, times=AudioIO.BARGE_IN_BLOCKS)
        self.assertEqual(h.barge_in_calls, 1)

    def test_cooldown_prevents_double_interruption(self) -> None:
        h = _Harness()
        h.start_speaking()
        h.feed(QUIET, times=2)
        h.feed(LOUD, times=AudioIO.BARGE_IN_BLOCKS)
        self.assertEqual(h.barge_in_calls, 1)
        # Jarvis repart (nouvelle réponse) tout de suite après.
        h.start_speaking()
        h.feed(LOUD, times=AudioIO.BARGE_IN_BLOCKS + 3)
        self.assertEqual(h.barge_in_calls, 1)


class BargeInStreamTests(unittest.TestCase):
    def test_prebuffer_is_replayed_without_duplicates(self) -> None:
        h = _Harness()
        h.start_speaking()
        marks = [_noise_block(60 + i) for i in range(3)]
        for block in marks:
            h.feed(block)
        before = len(h.sent)
        h.feed(LOUD, times=AudioIO.BARGE_IN_BLOCKS)
        self.assertEqual(h.barge_in_calls, 1)

        # Les blocs mémorisés pendant la réponse sont rejoués une seule fois,
        # dans l'ordre, juste avant le bloc courant.
        replayed = h.sent[before:]
        self.assertEqual(replayed.count(marks[0]), 1)
        self.assertEqual(
            [b for b in replayed if b in marks],
            marks,
        )
        # Pré-tampon vidé : pas de rejeu en boucle.
        self.assertEqual(len(h.audio._barge_prebuffer), 0)

    def test_prebuffer_is_bounded(self) -> None:
        h = _Harness()
        h.start_speaking()
        h.feed(QUIET, times=AudioIO.BARGE_IN_PREBUFFER_BLOCKS * 4)
        self.assertLessEqual(
            len(h.audio._barge_prebuffer), AudioIO.BARGE_IN_PREBUFFER_BLOCKS
        )

    def test_prebuffer_cleared_when_jarvis_stops_talking(self) -> None:
        h = _Harness()
        h.start_speaking()
        h.feed(QUIET, times=3)
        self.assertTrue(len(h.audio._barge_prebuffer) > 0)
        h.audio.extend_listening()  # fin de tour Gemini
        self.assertEqual(len(h.audio._barge_prebuffer), 0)
        h.feed(QUIET, times=2)
        self.assertEqual(len(h.audio._barge_prebuffer), 0)

    def test_backend_hook_failure_is_not_fatal(self) -> None:
        h = _Harness()

        def _boom():
            raise RuntimeError("backend absent")

        h.audio.on_barge_in = _boom
        h.start_speaking()
        h.feed(QUIET, times=2)
        h.feed(LOUD, times=AudioIO.BARGE_IN_BLOCKS)
        # Malgré l'échec du hook, la lecture est bien coupée.
        self.assertFalse(h.audio.speaking)
        self.assertTrue(h.audio.q.empty())


class ManualStopTests(unittest.TestCase):
    def test_stop_speaking_cuts_playback(self) -> None:
        h = _Harness()
        h.start_speaking()
        h.audio.play(np.zeros(2400, dtype=np.int16).tobytes())
        self.assertTrue(h.audio.stop_speaking())
        self.assertEqual(h.barge_in_calls, 1)
        self.assertFalse(h.audio.speaking)
        self.assertTrue(h.audio.q.empty())

    def test_stop_speaking_when_idle_is_harmless(self) -> None:
        h = _Harness()
        self.assertFalse(h.audio.stop_speaking())
        self.assertEqual(h.barge_in_calls, 1)
        self.assertFalse(h.audio.speaking)


class _Msg:
    def __init__(self, server_content=None, tool_call=None):
        self.server_content = server_content
        self.tool_call = tool_call
        self.session_resumption_update = None


class _Content:
    def __init__(self, model_turn=None, interrupted=False, turn_complete=False):
        self.model_turn = model_turn
        self.interrupted = interrupted
        self.turn_complete = turn_complete
        self.input_transcription = None


class _Part:
    def __init__(self, data):
        self.inline_data = types.SimpleNamespace(data=data)


class _Turn:
    def __init__(self, *chunks):
        self.parts = [_Part(c) for c in chunks]


class _Session:
    def __init__(self, messages):
        self._messages = messages

    async def receive(self):
        for msg in self._messages:
            yield msg


def _run_loop(gemini, messages):
    async def _go():
        gemini.session = _Session(messages)
        await gemini.receive_loop()

    asyncio.run(_go())


class GeminiInterruptTests(unittest.TestCase):
    def _make(self):
        self.audio_chunks = []
        self.events = []
        gemini = GeminiLive(
            key="test-key",
            model="test-model",
            user="Test",
            on_audio=self.audio_chunks.append,
            on_turn_complete=lambda: self.events.append("turn_complete"),
            on_interrupted=lambda: self.events.append("interrupted"),
            on_speaking=lambda: self.events.append("speaking"),
        )
        gemini.session = object()
        return gemini

    def test_mic_is_blocked_while_speaking_but_opens_on_interrupt(self) -> None:
        gemini = self._make()
        self.assertTrue(gemini.can_send())
        gemini.speaking = True
        self.assertFalse(gemini.can_send())
        gemini.request_interrupt()
        self.assertTrue(gemini.can_send())

    def test_interrupt_window_expires(self) -> None:
        gemini = self._make()
        gemini.speaking = True
        gemini.request_interrupt()
        gemini._interrupt_until = time.monotonic() - 0.01
        self.assertFalse(gemini.interrupt_active())
        self.assertFalse(gemini.can_send())
        self.assertFalse(gemini.interrupt_requested)

    def test_tools_still_block_the_mic(self) -> None:
        gemini = self._make()
        gemini.tool_active = True
        gemini.request_interrupt()
        self.assertFalse(gemini.can_send())

    def test_model_audio_is_dropped_during_interruption(self) -> None:
        gemini = self._make()
        gemini.request_interrupt()
        _run_loop(gemini, [_Msg(_Content(model_turn=_Turn(b"\x01\x02")))])
        self.assertEqual(self.audio_chunks, [])
        self.assertNotIn("speaking", self.events)

    def test_server_confirmation_clears_the_interruption(self) -> None:
        gemini = self._make()
        gemini.request_interrupt()
        _run_loop(
            gemini,
            [
                _Msg(_Content(model_turn=_Turn(b"\x01"))),
                _Msg(_Content(interrupted=True)),
                _Msg(_Content(model_turn=_Turn(b"\x02"))),
            ],
        )
        # Le premier bloc est jeté, la réponse suivante est bien jouée.
        self.assertEqual(self.audio_chunks, [b"\x02"])
        self.assertIn("interrupted", self.events)
        self.assertFalse(gemini.interrupt_requested)

    def test_turn_complete_clears_the_interruption(self) -> None:
        gemini = self._make()
        gemini.request_interrupt()
        _run_loop(gemini, [_Msg(_Content(turn_complete=True))])
        self.assertFalse(gemini.interrupt_requested)
        self.assertIn("turn_complete", self.events)

    def test_playback_resumes_if_gemini_never_confirms(self) -> None:
        """Fausse détection : on ne laisse jamais Jarvis muet."""
        gemini = self._make()
        gemini.request_interrupt()
        gemini._interrupt_until = time.monotonic() + 10.0

        expired = {"done": False}
        original = gemini.interrupt_active

        def _interrupt_active():
            if not expired["done"]:
                expired["done"] = True
                return original()
            gemini._interrupt_until = time.monotonic() - 0.01
            return original()

        gemini.interrupt_active = _interrupt_active
        _run_loop(
            gemini,
            [
                _Msg(_Content(model_turn=_Turn(b"\x01"))),
                _Msg(_Content(model_turn=_Turn(b"\x02"))),
            ],
        )
        self.assertEqual(self.audio_chunks, [b"\x02"])
        # L'UI est réinformée que Jarvis parle de nouveau.
        self.assertEqual(self.events.count("speaking"), 1)

    def test_normal_turn_is_unaffected(self) -> None:
        gemini = self._make()
        _run_loop(
            gemini,
            [
                _Msg(_Content(model_turn=_Turn(b"\x01", b"\x02"))),
                _Msg(_Content(turn_complete=True)),
            ],
        )
        self.assertEqual(self.audio_chunks, [b"\x01", b"\x02"])
        self.assertEqual(self.events.count("speaking"), 1)
        self.assertIn("turn_complete", self.events)
        self.assertFalse(gemini.speaking)


class EndToEndInterruptionTests(unittest.TestCase):
    """Scénario complet, câblé comme dans src/main.py."""

    def test_user_says_stop_during_a_monologue(self) -> None:
        sent = []
        played = []
        holder = {}

        def mic(pcm):
            # Exactement la passerelle micro -> Gemini de src/main.py.
            if holder["gemini"].can_send():
                sent.append(pcm)

        def on_barge_in():
            holder["gemini"].request_interrupt()

        audio = AudioIO(mic, on_barge_in=on_barge_in)
        audio.running = True
        audio.awake = True

        def on_audio(pcm):
            played.append(pcm)
            audio.play(pcm)

        gemini = GeminiLive(
            key="test-key",
            model="test-model",
            user="Test",
            on_audio=on_audio,
            on_turn_complete=audio.extend_listening,
            on_interrupted=audio.clear_output,
            on_speaking=audio.begin_speaking,
        )
        holder["gemini"] = gemini
        gemini.session = object()

        checkpoints = {}

        class _ScriptedSession:
            async def receive(self):
                # 1. Jarvis se lance dans un long monologue.
                yield _Msg(_Content(model_turn=_Turn(b"\x10" * 4800)))
                checkpoints["speaking"] = audio.speaking

                # 2. L'utilisateur dit « stop » par-dessus la réponse.
                audio._speaking_since -= AudioIO.BARGE_IN_GRACE_SECONDS + 0.1
                for _ in range(3):
                    audio._handle_awake_block(QUIET)
                checkpoints["sent_before_stop"] = len(sent)
                for _ in range(AudioIO.BARGE_IN_BLOCKS):
                    audio._handle_awake_block(LOUD)
                checkpoints["sent_after_stop"] = len(sent)
                checkpoints["queue_after_stop"] = audio.q.qsize()

                # 3. Gemini a encore de l'audio en vol : il doit être jeté.
                yield _Msg(_Content(model_turn=_Turn(b"\x20" * 4800)))
                checkpoints["queue_during_interrupt"] = audio.q.qsize()

                # 4. Le serveur confirme l'interruption, puis clôt le tour.
                yield _Msg(_Content(interrupted=True))
                yield _Msg(_Content(turn_complete=True))

        async def _go():
            gemini.session = _ScriptedSession()
            await gemini.receive_loop()

        asyncio.run(_go())

        # Jarvis parlait bien avant l'interruption.
        self.assertTrue(checkpoints["speaking"])
        # Tant qu'il parlait, le micro n'allait pas vers Gemini.
        self.assertEqual(checkpoints["sent_before_stop"], 0)
        # Après le « stop », la phrase de l'utilisateur (pré-tampon inclus)
        # part bien vers Gemini.
        self.assertGreaterEqual(
            checkpoints["sent_after_stop"], AudioIO.BARGE_IN_BLOCKS + 3
        )
        # La sortie audio est coupée immédiatement, puis reste muette.
        self.assertEqual(checkpoints["queue_after_stop"], 0)
        self.assertEqual(checkpoints["queue_during_interrupt"], 0)
        # Seul le premier bloc a été joué : celui d'après a été jeté.
        self.assertEqual(len(played), 1)
        # État final propre : prêt pour la suite de la conversation.
        self.assertFalse(gemini.interrupt_requested)
        self.assertFalse(gemini.speaking)
        self.assertFalse(audio.speaking)
        self.assertTrue(audio.awake)
        self.assertTrue(audio.q.empty())
        self.assertGreater(audio.follow_up_until, time.monotonic())
        # Le micro repasse normalement une fois le tour terminé.
        self.assertTrue(gemini.can_send())


if __name__ == "__main__":
    unittest.main()
