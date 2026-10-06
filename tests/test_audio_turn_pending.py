"""Régression v1.7.5 ter — bug A (validation réelle) : ``AudioIO`` ne doit
jamais rendormir Jarvis (ou laisser expirer la fenêtre de conversation)
pendant que Gemini est encore en train de générer une réponse, même quand
cette génération prend plusieurs secondes et qu'aucun octet audio n'a
encore été produit (``_voice_audible()`` reste alors faux).

``note_turn_open``/``note_turn_resolved`` (câblés depuis
``GeminiLive.on_turn_open``/``on_turn_resolved``, cf. src/gemini_live.py)
comblent cet angle mort : voir aussi ``tests/test_turn_callbacks.py`` pour
le volet ``GeminiLive`` du correctif.
"""

from __future__ import annotations

import os
import sys
import time
import types
import unittest

os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


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


class TurnPendingSuspendsTheTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audio = AudioIO(lambda pcm: None)
        self.audio.running = True
        self.audio.awake = True

    def test_check_timeout_does_not_sleep_while_a_turn_is_pending(self) -> None:
        # La fenêtre de conversation est déjà expirée (comme si les 8 s
        # s'étaient écoulées) MAIS un tour vient de s'ouvrir côté Gemini et
        # n'a encore produit ni audio ni texte : _voice_audible() est donc
        # faux, et c'est exactement le trou que note_turn_open comble.
        self.audio.follow_up_until = time.monotonic() - 1.0
        self.audio.note_turn_open()

        self.audio._check_timeout()

        self.assertTrue(
            self.audio.awake,
            "un tour Gemini en attente de réponse ne doit jamais se faire "
            "endormir par le minuteur de conversation (bug A, validation réelle)",
        )

    def test_check_timeout_resumes_normally_once_the_turn_resolves(self) -> None:
        self.audio.follow_up_until = time.monotonic() - 1.0
        self.audio.note_turn_open()
        self.audio._check_timeout()
        self.assertTrue(self.audio.awake)

        # La réponse arrive enfin : GeminiLive notifie la résolution.
        self.audio.note_turn_resolved()
        # La fenêtre de suivi reste celle d'avant (expirée) : sans nouvel
        # ``extend_listening()``, le minuteur normal doit reprendre la main.
        self.audio._check_timeout()

        self.assertFalse(
            self.audio.awake,
            "une fois le tour résolu, le minuteur de conversation normal "
            "doit reprendre ses droits (pas de suspension permanente)",
        )

    def test_grace_period_caps_the_suspension_if_never_resolved(self) -> None:
        # Filet de sécurité : si on_turn_resolved n'arrive jamais (connexion
        # perdue sans notification, plantage...), la suspension ne doit pas
        # durer indéfiniment.
        self.audio.follow_up_until = time.monotonic() - 1.0
        self.audio.note_turn_open()
        with self.audio._turn_pending_lock:
            self.audio._turn_pending_since = (
                time.monotonic() - AudioIO.TURN_PENDING_MAX_GRACE_SECONDS - 1.0
            )

        self.audio._check_timeout()

        self.assertFalse(
            self.audio.awake,
            "le filet de sécurité doit laisser le minuteur reprendre la "
            "main au-delà de TURN_PENDING_MAX_GRACE_SECONDS",
        )

    def test_turn_pending_does_not_mask_a_listen_mode_setting(self) -> None:
        # L'écoute continue doit continuer à se renouveler normalement même
        # sans tour en attente (non-régression trivialement vérifiée ici).
        self.audio.listen_mode_provider = lambda: True
        self.audio.follow_up_until = time.monotonic() - 1.0

        self.audio._check_timeout()

        self.assertTrue(self.audio.awake)
        self.assertGreater(self.audio.follow_up_until, time.monotonic())


if __name__ == "__main__":
    unittest.main()
