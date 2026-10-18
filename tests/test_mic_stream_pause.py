"""Pause prolongée du flux micro : ``audioStreamEnd`` vers la Live API.

Référence Gemini Live : quand le flux audio est mis en pause plus d'une
seconde, le client doit envoyer ``send_realtime_input(audio_stream_end=True)``
pour vider l'audio en cache et clore l'activité VAD côté serveur. Le correctif
E01 branche cette alerte sur la porte anti-écho de ``AudioIO`` : sans elle, la
VAD serveur peut rester suspendue au milieu d'une parole et interpréter la
reprise du micro comme un barge-in (``TTS_START`` puis ``INTERRUPTION``).

Ces tests ne démarrent aucune carte son : ils vérifient le déclenchement
unique et le réarmement du crochet, sans dépendre du temps réel.
"""

from __future__ import annotations

import os
import unittest
import unittest.mock as mock

os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

# Installe le faux ``sounddevice`` avant d'importer AudioIO.
from tests.echo_loop_harness import AudioIO  # noqa: E402


class MicPauseHookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.audio = AudioIO(lambda pcm: None)
        self.hits: list[float] = []
        self.audio.mic_pause_hook = lambda: self.hits.append(1.0)

    def _at(self, now: float, open_now: bool) -> None:
        with mock.patch("src.audio.time.monotonic", return_value=now):
            self.audio._trace_mic_gate(open_now)

    def test_hook_fires_once_after_one_second_and_rearms_on_reopen(self) -> None:
        self._at(100.0, False)
        self.assertEqual(self.hits, [], "aucun envoi pour une micro-pause")
        self._at(100.9, False)
        self.assertEqual(self.hits, [])
        self._at(101.1, False)
        self.assertEqual(len(self.hits), 1, "audioStreamEnd attendu après 1 s de pause")
        self._at(101.5, False)
        self.assertEqual(len(self.hits), 1, "l'alerte ne doit pas se répéter pendant la même pause")
        # Le prochain bloc réel rouvre la porte : la pause est réarmée.
        self._at(102.0, True)
        self.assertEqual(self.audio._mic_stream_pause_notified, False)
        self._at(102.5, False)
        self.assertEqual(len(self.hits), 1)
        self._at(103.7, False)
        self.assertEqual(len(self.hits), 2, "une nouvelle pause doit être signalée")

    def test_failed_hook_does_not_break_the_audio_worker(self) -> None:
        def boom() -> None:
            raise RuntimeError("backend indisponible")

        self.audio.mic_pause_hook = boom
        self._at(10.0, False)
        self._at(11.2, False)  # ne doit pas lever
        events = [e for e in self.audio.trace_events() if e["kind"] == "MIC_PAUSE_HOOK_FAILED"]
        self.assertEqual(len(events), 1)


if __name__ == "__main__":
    unittest.main()
