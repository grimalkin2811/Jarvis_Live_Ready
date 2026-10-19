"""Scripts officiels de validation RÉELLE (E11 : ``validate_real_gemini.py``,
E12 : ``validate_reconnect_v174_real.py``) — ce qu'ils déclarent avoir exercé.

Validation Windows du 2026-10-16 (commit 28234b0) : E11 rapportait AUDIO RÉEL,
RECONNEXION RÉELLE et SESSION RESUMPTION RÉELLE « NON TESTABLE » alors que la
clé, le réseau et le service étaient disponibles (S1 et les outils passaient).
Causes dans le script : toute exception devenait « NON TESTABLE » (un délai
dépassé en attendant la réponse est un ÉCHEC) ; l'attente de reconnexion
s'appuyait sur un évènement resté levé depuis la PREMIÈRE connexion (rejeux
comptés avant la reconnexion) ; l'énoncé était envoyé puis le flux COUPÉ.

Ces tests ne disent rien du service réel : ils figent la sincérité des verdicts.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import validate_real_gemini as vrg  # noqa: E402
import validate_reconnect_v174_real as v174  # noqa: E402
from src import gemini_live as gl  # noqa: E402


class _ApiError(Exception):
    def __init__(self, message: str, code: int | None = None, status: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


class VerdictClassificationTests(unittest.TestCase):
    CASES = [
        (vrg.TurnTimeout("aucune réponse 60 s après l'énoncé", "{}"), "FAIL"),
        (TimeoutError("aucune réponse reçue"), "FAIL"),
        (_ApiError("keepalive ping timeout", code=1011, status="INTERNAL"), "FAIL"),
        (RuntimeError("session fermée"), "FAIL"),
        (gl.AuthError("Clé Gemini API refusée"), "NON TESTABLE"),
        (_ApiError("API key not valid", code=400, status="INVALID_ARGUMENT"), "NON TESTABLE"),
        (_ApiError("Resource has been exhausted (e.g. check quota).", code=429, status="RESOURCE_EXHAUSTED"),
         "NON TESTABLE"),
        (OSError("[Errno 11001] getaddrinfo failed"), "NON TESTABLE"),
    ]

    def test_only_missing_prerequisites_are_not_testable(self):
        for exc, expected in self.CASES:
            with self.subTest(exc=repr(exc)):
                self.assertEqual(vrg.classify_failure(exc)[0], expected)
                self.assertEqual(v174.scenario_verdict(exc)[0], expected)

    def test_reasons_are_explicit_and_keys_masked(self):
        cle = "AIza" + "SyD-0123456789abcdefghijkl"
        verdict, reason = vrg.classify_failure(RuntimeError(f"connexion refusée pour {cle}"))
        self.assertEqual(verdict, "FAIL")
        self.assertNotIn(cle, reason)
        _verdict, reason = vrg.classify_failure(gl.AuthError("Clé refusée"))
        self.assertIn("prérequis absent", reason)
        verdict, reason = v174.scenario_verdict(RuntimeError(f"erreur {cle}"))
        self.assertNotIn(cle, reason)

    def test_a_timeout_carries_the_engine_diagnostic(self):
        verdict, reason = vrg.classify_failure(vrg.TurnTimeout("aucune réponse", '{"moteur": {"speaking": false}}'))
        self.assertEqual(verdict, "FAIL")
        self.assertIn('"speaking": false', reason)


class _StubRunner:
    """Runner sans réseau : S1 répond, puis plus rien (délai dépassé)."""

    def __init__(self) -> None:
        self.gemini = type("G", (), {"session_generation": 1, "resumption_handle": None,
                                     "context_seed_confirmed": False, "trace_events": lambda self: []})()
        self.calls = 0

    async def ask(self, prompt, mode, client=None, tts_model=""):
        self.calls += 1
        if self.calls == 1:
            return {"user": prompt, "assistant": "Oui, je t'entends.", "tools": []}
        raise vrg.TurnTimeout("aucune réponse 60 s après l'énoncé", '{"moteur": {}}')

    def diagnostic(self) -> str:
        return "{}"


class ScenarioVerdictTests(unittest.IsolatedAsyncioTestCase):
    async def test_unanswered_questions_are_failures_not_untestable(self):
        with contextlib.redirect_stdout(io.StringIO()):
            verdicts, notes = await vrg.run_scenarios(_StubRunner(), "tts", None, "")
        self.assertEqual(verdicts["GEMINI LIVE RÉEL"], "PASS")
        for name in ("AUDIO RÉEL", "RECONNEXION RÉELLE", "SESSION RESUMPTION RÉELLE", "OUTILS RÉELS"):
            self.assertEqual(verdicts[name], "FAIL", name)
        self.assertTrue(any("aucune réponse" in note for note in notes))

    async def test_selftest_observes_the_seed_after_the_real_reconnection(self):
        """Le rejeu est compté APRÈS la reconnexion effective (ancien code : avant,
        donc toujours 0) ; la reprise par handle est réellement observée."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = await vrg.run_selftest()
        text = out.getvalue()
        self.assertEqual(code, 0, text[-2000:])
        self.assertIn("1 clientContent après reconnexion", text)
        self.assertIn("session reprise=True, 0 clientContent", text)
        for name in vrg.VERDICT_NAMES:
            self.assertIn(f"{name} : PASS", text)


class ContinuousMicrophoneTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_microphone_keeps_streaming_after_the_utterance(self):
        """Comme le pont micro de production : des blocs continuent de partir
        après l'énoncé (la VAD du serveur voit la fin de la parole)."""
        from tests.live_harness import FakeClient, FakeLiveServer

        server = FakeLiveServer("2.x")
        runner = vrg.RealRunner("cle-factice", vrg.DEFAULT_MODEL, None)
        runner.gemini.client = FakeClient(server)
        with contextlib.redirect_stdout(io.StringIO()):
            await runner.start()
            try:
                sent_before = runner.mic_blocks_sent
                await asyncio.sleep(0.45)
                self.assertGreaterEqual(runner.mic_blocks_sent - sent_before, 3,
                                        "le micro simulé doit émettre en continu (100 ms)")
            finally:
                await runner.stop()


if __name__ == "__main__":
    unittest.main()
