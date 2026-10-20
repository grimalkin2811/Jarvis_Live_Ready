"""Filet d'intention Dashboard (E02/E10/E11) : commande explicite, outil, résultat.

Le modèle peut clore un tour avec une transcription correcte mais AUCUN
appel d'outil (constat réel E02/E11 : ``tools=[]``, Dashboard fermé).
Ces tests prouvent le trajet déterministe transcription -> intention ->
exécution locale -> contexte, et que mentions/questions/négations ne
déclenchent jamais d'ouverture. Ils ne prouvent rien sur Gemini réel.
"""

from __future__ import annotations

import asyncio
import os
import time
import unittest
from unittest import mock

os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

from src import gemini_live as gl  # noqa: E402
from src.conversation import ConversationContext, parse_dashboard_command  # noqa: E402
from tests import sdk_live_fake as fake  # noqa: E402

MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"


class ParseDashboardCommandTests(unittest.TestCase):
    def test_explicit_open_with_page(self):
        self.assertEqual(parse_dashboard_command("Ouvre le tableau de bord sur la page mémoire."), ("open", "memoire"))

    def test_explicit_open_without_page(self):
        self.assertEqual(parse_dashboard_command("Affiche le Dashboard"), ("open", None))

    def test_explicit_close(self):
        self.assertEqual(parse_dashboard_command("Ferme le tableau de bord"), ("close", None))

    def test_mention_of_car_dashboard_does_not_open(self):
        self.assertIsNone(parse_dashboard_command(
            "Le tableau de bord de ma voiture affiche un voyant orange, qu'est-ce que ça peut vouloir dire ?"))

    def test_mention_of_excel_dashboard_does_not_open(self):
        self.assertIsNone(parse_dashboard_command("Mon tableau de bord Excel est trop lent, tu as une idée ?"))

    def test_question_does_not_open(self):
        self.assertIsNone(parse_dashboard_command("Peux-tu ouvrir le tableau de bord ?"))
        self.assertIsNone(parse_dashboard_command("Peux-tu ouvrir le tableau de bord"))
        self.assertIsNone(parse_dashboard_command("Tu peux ouvrir le Dashboard ?"))

    def test_negation_does_not_open(self):
        self.assertIsNone(parse_dashboard_command("N'ouvre pas le tableau de bord"))
        self.assertIsNone(parse_dashboard_command("Ne peux-tu pas ouvrir le tableau de bord"))
        self.assertIsNone(parse_dashboard_command("Je ne peux pas ouvrir le Dashboard"))

    def test_ctrl_token_is_not_a_command(self):
        self.assertIsNone(parse_dashboard_command("<ctrl46> <ctrl46>"))


class DashboardFallbackTurnTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls: list[tuple[str, dict]] = []
        self.tool_events: list[str] = []
        self.conversation = ConversationContext(max_turns=40, max_tokens=8192)
        self.gemini = gl.GeminiLive(
            "cle-de-test", MODEL, "Test",
            on_audio=lambda pcm: None,
            on_turn_complete=lambda: self.tool_events.append("complete"),
            on_tool_start=lambda name: self.tool_events.append(f"start:{name}"),
            on_tool_end=lambda name, ok: self.tool_events.append(f"end:{name}:{ok}"),
            conversation=self.conversation,
        )
        self.session = fake.SdkLikeSession()
        self.gemini.session = self.session
        self.gemini._loop = asyncio.get_running_loop()

        def fake_open(page=None):
            self.calls.append(("open_dashboard", {"page": page} if page else {}))
            return {"success": True, "action": "open_dashboard", "page": page or "accueil", "confirmed": True}

        self._patch = mock.patch.dict(gl.TOOL_FUNCTIONS, {"open_dashboard": fake_open})
        self._patch.start()
        self.task = asyncio.create_task(self.gemini._receive_loop())

    async def asyncTearDown(self):
        self.session.close()
        try:
            await asyncio.wait_for(self.task, 2)
        except Exception:
            self.task.cancel()
        self._patch.stop()
        await self.gemini.close()

    async def _settle(self):
        for _ in range(30):
            await asyncio.sleep(0.005)

    async def test_voice_command_without_model_tool_call_opens_dashboard(self):
        self.session.push(fake.user("Ouvre le tableau de bord sur la page mémoire."))
        await self._settle()
        # Le modèle répond verbalement mais n'émet AUCUN appel d'outil.
        self.session.push(*fake.answer("Bien sûr."))
        deadline = time.monotonic() + 3
        while not self.calls and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        self.assertEqual(self.calls, [("open_dashboard", {"page": "memoire"})])
        self.assertIn("start:open_dashboard", self.tool_events)
        self.assertIn("end:open_dashboard:True", self.tool_events)
        kinds = [e["kind"] for e in self.gemini.trace_events()]
        self.assertIn("TOOL_CALL_FALLBACK_DASHBOARD", kinds)
        texts = [(m.role, m.text) for m in self.conversation.get_messages()]
        self.assertTrue(any("appel outil" in t or "open_dashboard" in t for _r, t in texts), texts)

    async def test_mention_without_command_executes_nothing(self):
        self.session.push(fake.user("Le tableau de bord de ma voiture affiche un voyant orange."))
        await self._settle()
        self.session.push(*fake.answer("Cela peut être plusieurs choses."))
        await self._settle()
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
