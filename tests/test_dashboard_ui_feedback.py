"""Retours d'interface constatés pendant la validation PR #50.

* Overlay Desktop « à l'écoute » pendant le traitement d'un message ÉCRIT :
  ``submit_text`` publiait la demande comme une transcription (« l'utilisateur
  parle »), ce qui fait passer un halo caché en LISTENING. Désormais l'état
  « réflexion » est publié d'abord.
* Double notification pour une seule panne audio : « Backend vocal
  indisponible » ET « Problème audio ».

Machine d'états et hub RÉELS ; GeminiLive contre le faux serveur (aucun réseau).
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.gemini_live import GeminiLive  # noqa: E402
from tests.live_harness import FakeClient, FakeLiveServer  # noqa: E402
from tests.voice_harness import VoiceHarness  # noqa: E402
from UI.desktop.state import DesktopEvent, DesktopState, DesktopStateMachine  # noqa: E402


class DesktopOverlayForWrittenMessagesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.server = FakeLiveServer(semantics="2.x")
        self.server.text_turns_use_oracle = True
        self.harness = VoiceHarness(self.server)
        self.gemini: GeminiLive = self.harness.gemini
        self.gemini.client = FakeClient(self.server)
        # Câblage de src/ui.py : on_thinking -> presence_hook("thinking"),
        # on_speaking -> presence_hook("speaking"), transcriptions -> évènements Desktop.
        self.machine = DesktopStateMachine()
        self.states: list[str] = []
        self.gemini.on_thinking = lambda: self._record(self.machine.handle_presence("thinking"))
        self.gemini.on_speaking = lambda: self._record(self.machine.handle_presence("speaking"))
        self.gemini.on_user_transcript = lambda text, final=False: self._record(
            self.machine.handle(DesktopEvent.TRANSCRIPT, {"text": text, "final": final}))
        await self.harness.start()
        await self.harness.wait_ready()

    async def asyncTearDown(self) -> None:
        await self.harness.stop()

    def _record(self, transition) -> None:
        if transition is not None and transition.changed:
            self.states.append(transition.current)

    async def test_written_message_never_shows_listening_while_jarvis_works(self):
        self.assertEqual(self.machine.state, DesktopState.HIDDEN)
        before = len(self.harness.turns)
        await self.gemini.submit_text("Quelle est la capitale de l'Espagne ?")
        for _ in range(200):
            if len(self.harness.turns) > before:
                break
            await asyncio.sleep(0.005)
        self.assertNotIn(DesktopState.LISTENING, self.states,
                         f"un message écrit n'est pas une écoute : {self.states}")
        self.assertEqual(self.states[0], DesktopState.THINKING)
        self.assertIn(DesktopState.SPEAKING, self.states, "la réponse passe bien en « Jarvis parle »")

    async def test_voice_transcript_still_shows_listening(self):
        """Non-régression : une VRAIE transcription vocale garde l'état d'écoute."""
        from tests.live_harness import msg_user_transcript

        await self.server.current._emit(msg_user_transcript("Bonjour Jarvis"))
        for _ in range(100):
            if self.states:
                break
            await asyncio.sleep(0.005)
        self.assertEqual(self.states[:1], [DesktopState.LISTENING])


class AudioFailureNotificationTests(unittest.TestCase):
    def setUp(self) -> None:
        from PySide6.QtWidgets import QApplication

        from tests.dashboard_support import isolated_jarvis
        from UI.dashboard.config import ConfigStore
        from UI.dashboard.hub import DashboardHub

        self.app = QApplication.instance() or QApplication([])
        self._env = isolated_jarvis()
        data = self._env.__enter__()
        self.store = ConfigStore(os.path.join(str(data), "state.json"))
        self.hub = DashboardHub(self.store)

    def tearDown(self) -> None:
        try:
            self.hub.shutdown()
        finally:
            self._env.__exit__(None, None, None)

    def _titles(self) -> list[str]:
        return [item.title for item in self.hub.notifications.items()]

    def test_one_audio_failure_raises_one_notification(self):
        # Séquence de src/ui.py quand audio.start() échoue.
        self.hub._on_engine({"engine_state": "starting", "audio_state": "failed",
                             "audio_detail": "démarrage audio impossible : périphérique absent"})
        self.hub._on_engine({"engine_state": "failed", "engine_detail": "pipeline audio indisponible",
                             "audio_state": "failed",
                             "audio_detail": "démarrage audio impossible : périphérique absent"})
        self.assertEqual(self._titles(), ["Problème audio"])
        self.assertIn("périphérique absent", self.hub.notifications.items()[0].message)

    def test_non_audio_engine_failure_is_still_notified(self):
        self.hub._on_engine({"engine_state": "failed", "engine_detail": "configuration invalide"})
        self.assertEqual(self._titles(), ["Backend vocal indisponible"])


if __name__ == "__main__":
    unittest.main()
