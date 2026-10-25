"""Traces S4 côté moteur + invalidation du handle sur erreur d'entité.

Défaut reproduit (validation réelle 2026-10-25, run 40228733615) :
* S4 « OUTILS RÉELS : FAIL » avec ``outils=[]`` — impossible de distinguer
  sur les seules traces d'alors « le modèle n'a jamais appelé l'outil » de
  « la réponse d'outil n'a jamais été envoyée ». Correction : le moteur
  trace désormais TOOL_RESPONSE_SENT (envoi réussi) / TOOL_RESPONSE_SEND_
  FAILED (échec d'envoi) — croisés avec le miroir câble du validateur.
* S2/S3 : un « Requested entity was not found » (1008) tuant la session
  OUVERTE laissait le handle de la session morte en place : la reconnexion
  suivante l'essayait, se faisait refuser (1008 répétés), puis seulement
  retombait sur session neuve + rejeu. Correction : le handle est invalidé
  dès l'erreur (trace SESSION_RESUME_REJECTED, source ``receive_loop``) —
  la reconnexion repart en session neuve + rejeu contrôlé du premier coup.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import gemini_live as gl  # noqa: E402
from src.conversation import ConversationContext  # noqa: E402
from tests import sdk_live_fake as fake  # noqa: E402
from google.genai import errors as genai_errors  # noqa: E402

MODEL_25 = "gemini-2.5-flash-native-audio-preview-12-2025"


def _fake_validation_probe(**kwargs):
    return {"success": True, "code": "KILO-7", "message": "canal outil fonctionnel"}


class _TraceCase(unittest.IsolatedAsyncioTestCase):
    """Une ``GeminiLive`` réelle branchée sur une session scriptée du SDK."""

    def setUp(self) -> None:
        os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")
        self.events: list[str] = []
        self.turns: list[dict] = []
        self.current: dict = {"user": "", "assistant": ""}
        self.conversation = ConversationContext(max_turns=40, max_tokens=8192)
        patcher = mock.patch.dict(
            gl.TOOL_FUNCTIONS, {"validation_probe": _fake_validation_probe}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncSetUp(self) -> None:
        self.gemini = gl.GeminiLive(
            "cle-de-test", MODEL_25, "Test",
            on_audio=lambda pcm: None,
            on_turn_complete=self._complete,
            on_interrupted=lambda: self.events.append("interrupted"),
            on_turn_open=lambda: self.events.append("open"),
            on_turn_resolved=lambda: self.events.append("resolved"),
            on_user_transcript=lambda text, final=False: self.current.__setitem__("user", text),
            on_assistant_transcript=lambda text: self.current.__setitem__("assistant", text),
            conversation=self.conversation,
        )
        self.session = fake.SdkLikeSession()
        self.gemini.session = self.session
        self.gemini._loop = asyncio.get_running_loop()
        self.task = asyncio.create_task(self.gemini._receive_loop())

    async def asyncTearDown(self) -> None:
        self.session.close()
        try:
            await asyncio.wait_for(self.task, 2)
        except Exception:
            self.task.cancel()
        await self.gemini.close()

    def _complete(self) -> None:
        self.events.append("complete")
        self.turns.append(dict(self.current))
        self.current = {"user": "", "assistant": ""}

    def trace_kinds(self) -> list[str]:
        return [e["kind"] for e in self.gemini.trace_events()]

    async def settle(self) -> None:
        for _ in range(20):
            await asyncio.sleep(0.005)


class ToolResponseTraceTests(_TraceCase):
    async def test_envoi_reponse_outil_trace(self) -> None:
        """Une réponse d'outil effectivement envoyée laisse TOOL_RESPONSE_SENT."""
        self.session.push(fake.user("Utilise l'outil validation_probe et dis le code."))
        await self.settle()
        self.session.push(fake.tool_call("validation_probe", {}, "call-1"))
        await self.settle()

        self.assertIn("tool_response", self.session.kinds())
        sent = [e for e in self.gemini.trace_events() if e.get("kind") == "TOOL_RESPONSE_SENT"]
        self.assertEqual(len(sent), 1, "l'envoi de la réponse d'outil doit être tracé")
        self.assertEqual(sent[0].get("names"), ["validation_probe"])
        self.assertNotIn("TOOL_RESPONSE_SEND_FAILED", self.trace_kinds())

    async def test_echec_envoi_reponse_outil_trace_et_propage(self) -> None:
        """Un échec d'envoi est tracé, l'aller-retour n'est PAS armé, l'erreur
        remonte (comme le SDK réel la remonterait au receive_loop)."""
        failures: list[BaseException] = []

        async def _send_fails(function_responses=None) -> None:
            raise RuntimeError("websocket mort pendant l'envoi")

        self.session.send_tool_response = _send_fails  # type: ignore[method-assign]
        self.task.add_done_callback(lambda t: failures.append(t.exception()))

        self.session.push(fake.user("Utilise l'outil validation_probe et dis le code."))
        await self.settle()
        self.session.push(fake.tool_call("validation_probe", {}, "call-1"))
        await self.settle()

        failed = [
            e for e in self.gemini.trace_events()
            if e.get("kind") == "TOOL_RESPONSE_SEND_FAILED"
        ]
        self.assertEqual(len(failed), 1, "l'échec d'envoi doit être tracé nommément")
        self.assertIn("websocket mort", str(failed[0].get("error")))
        self.assertFalse(
            self.gemini._tool_roundtrip_open,
            "aucun aller-retour armé quand la réponse n'a pas pu partir",
        )
        self.assertNotIn("TOOL_RESPONSE_SENT", self.trace_kinds())
        self.assertTrue(self.task.done(), "l'erreur doit remonter au receive_loop")
        self.assertIsInstance(failures[0], RuntimeError)


class _KilledSession(fake.SdkLikeSession):
    """Session dont la réception lève immédiatement l'erreur fournie."""

    def __init__(self, error: BaseException) -> None:
        super().__init__()
        self._error = error

    async def receive(self):
        raise self._error
        yield  # pragma: no cover - rend le générateur async


class ReceiveLoopEntityErrorTests(_TraceCase):
    async def _run_receive_loop(self, session) -> BaseException:
        """Lance receive_loop sur la session et rend l'exception terminale."""
        self.gemini.session = session
        task = asyncio.create_task(self.gemini.receive_loop())
        for _ in range(100):
            if task.done():
                break
            await asyncio.sleep(0.005)
        self.assertTrue(task.done(), "receive_loop doit terminer après l'erreur")
        return task.exception()

    async def test_1008_en_pleine_session_invalide_le_handle(self) -> None:
        """1008 sur session ouverte : handle invalidé AVANT la reconnexion."""
        self.gemini.resumption_handle = "handle-mourant"
        self.gemini._resumption_handle_message_count = 3
        self.gemini._resumption_handle_received_this_turn = True

        error = genai_errors.APIError(
            1008, {"error": {"message": "Requested entity was not found."}}
        )
        exc = await self._run_receive_loop(_KilledSession(error))

        self.assertIs(exc, error, "l'erreur doit remonter (le keeper reconnecte)")
        self.assertIsNone(
            self.gemini.resumption_handle,
            "le handle d'une session déclarée introuvable doit être invalidé",
        )
        self.assertIsNone(self.gemini._resumption_handle_message_count)
        rejected = [
            e for e in self.gemini.trace_events()
            if e.get("kind") == "SESSION_RESUME_REJECTED"
        ]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0].get("source"), "receive_loop")
        self.assertEqual(rejected[0].get("code"), 1008)

    async def test_erreur_reseau_generique_conserve_le_handle(self) -> None:
        """Une erreur réseau banale ne doit PAS invalider le handle : la
        session peut être reprise telle quelle si le serveur la garde."""
        self.gemini.resumption_handle = "handle-valide"
        self.gemini._resumption_handle_message_count = 2

        await self._run_receive_loop(_KilledSession(RuntimeError("connexion réinitialisée")))

        self.assertEqual(
            self.gemini.resumption_handle, "handle-valide",
            "seule une erreur d'entité introuvable invalide le handle",
        )
        self.assertNotIn("SESSION_RESUME_REJECTED", self.trace_kinds())


if __name__ == "__main__":
    unittest.main()
