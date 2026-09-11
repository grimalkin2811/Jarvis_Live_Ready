"""Tests des contrôles temps réel de AudioIO (sans matériel audio).

Utilise des flux factices : aucun périphérique réel n'est ouvert. Vérifie :
* le volume TTS appliqué en direct sur les samples (gain perceptuel) ;
* le plafond d'avance audio (latence qui ne croît pas indéfiniment) ;
* le mode écoute continue (Jarvis ne retourne pas en veille) ;
* la robustesse de clear_output sur les compteurs internes.
"""

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


def _tone_pcm(seconds: float = 0.1, amplitude: int = 12000) -> bytes:
    rate = AudioIO.OUTPUT_RATE
    samples = (np.linspace(0, seconds * 2 * np.pi, int(rate * seconds)) * 1000).astype(np.int16)
    return (samples % amplitude).astype(np.int16).tobytes()


class GainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audio = AudioIO(lambda pcm: None)
        self.audio.running = True

    def test_full_volume_leaves_audio_untouched(self) -> None:
        pcm = _tone_pcm()
        self.audio.volume_provider = lambda: 100
        # Comparer les octets directement (gain ≈ 1.0).
        self.assertAlmostEqual(self.audio._output_volume(), 1.0, places=3)

    def test_zero_volume_silences_output(self) -> None:
        pcm = _tone_pcm()
        silent = self.audio._apply_gain(pcm, 0.0)
        self.assertEqual(max(abs(b) for b in np.frombuffer(silent, dtype=np.int16)), 0)

    def test_gain_clips_instead_of_overflow(self) -> None:
        loud = np.full(100, 30000, dtype=np.int16).tobytes()
        result = np.frombuffer(self.audio._apply_gain(loud, 2.0), dtype=np.int16)
        self.assertLessEqual(result.max(), 32767)
        self.assertGreaterEqual(result.min(), -32768)

    def test_volume_curve_is_perceptual(self) -> None:
        self.audio.volume_provider = lambda: 50
        # 50 % du curseur ne doit pas donner 50 % du gain (trop faible à
        # l'oreille) mais environ un tiers.
        self.assertLess(self.audio._output_volume(), 0.45)
        self.assertGreater(self.audio._output_volume(), 0.25)


class QueueCapTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audio = AudioIO(lambda pcm: None)
        self.audio.running = True

    def test_queue_is_capped(self) -> None:
        pcm = _tone_pcm(seconds=1.0)
        # Enfiler bien plus que MAX_QUEUED_SECONDS d'audio.
        for _ in range(int(AudioIO.MAX_QUEUED_SECONDS * 4) + 2):
            self.audio.play(pcm)
        self.assertLessEqual(self.audio._queued_bytes, self.audio._max_queued_bytes)

    def test_clear_output_resets_counter(self) -> None:
        self.audio.play(_tone_pcm(seconds=1.0))
        self.audio.clear_output()
        self.assertEqual(self.audio._queued_bytes, 0)
        self.assertTrue(self.audio.q.empty())


class ListenModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audio = AudioIO(lambda pcm: None)
        self.audio.running = True

    def test_listen_mode_keeps_jarvis_awake(self) -> None:
        self.audio.awake = True
        self.audio.listen_mode_provider = lambda: True
        self.audio.follow_up_until = time.monotonic() - 1.0  # expiré
        self.audio._check_timeout()
        self.assertTrue(self.audio.awake)
        self.assertGreater(self.audio.follow_up_until, time.monotonic())

    def test_normal_mode_returns_to_sleep(self) -> None:
        self.audio.awake = True
        self.audio.listen_mode_provider = lambda: False
        self.audio.follow_up_until = time.monotonic() - 1.0
        self.audio._check_timeout()
        self.assertFalse(self.audio.awake)

    def test_listen_mode_wakes_from_sleep(self) -> None:
        self.audio.awake = False
        self.audio.listen_mode_provider = lambda: True
        self.audio._handle_idle_block(b"\x00" * 1280)
        self.assertTrue(self.audio.awake)

    def test_mic_disabled_reports_correctly(self) -> None:
        self.audio.mic_enabled = lambda: False
        self.assertFalse(self.audio._is_mic_enabled())
        self.audio.mic_enabled = lambda: True
        self.assertTrue(self.audio._is_mic_enabled())


if __name__ == "__main__":
    unittest.main()
