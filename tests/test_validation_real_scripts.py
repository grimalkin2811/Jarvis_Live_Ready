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
import time
import unittest
from types import SimpleNamespace
from unittest import mock
from unittest.mock import AsyncMock

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

    async def test_text_mode_never_reports_real_audio_as_pass(self):
        class TextModeRunner:
            def __init__(self):
                self.traces = []
                self.gemini = SimpleNamespace(
                    session_generation=1,
                    resumption_handle=None,
                    context_seed_confirmed=True,
                    trace_events=lambda: self.traces,
                )

            async def ask(self, prompt, mode, client=None, tts_model=""):
                if prompt == "J'habite à Tours.":
                    self.gemini.resumption_handle = "handle"
                    return {"user": prompt, "assistant": "Tu habites à Tours.", "tools": []}
                if "validation_ping" in prompt:
                    return {"user": prompt, "assistant": vrg.VALIDATION_CODE, "tools": ["validation_ping"]}
                if "code" in prompt.lower():
                    return {"user": prompt, "assistant": vrg.VALIDATION_CODE, "tools": []}
                answer = "Simon" if "prénom" in prompt.lower() else "Tours"
                return {"user": prompt, "assistant": answer, "tools": []}

            async def drop_connection(self, keep_handle):
                previous = self.gemini.session_generation
                self.gemini.session_generation += 1
                if keep_handle:
                    self.traces.append({"kind": "SESSION_RESUME",
                                        "session_generation": self.gemini.session_generation,
                                        "ts": time.monotonic()})
                return previous

            async def wait_reconnected(self, generation):
                return None

            def diagnostic(self):
                return "{}"

        runner = TextModeRunner()
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
            vrg, "wire_since", side_effect=[[{"kind": "client_content", "turn_complete": True}], []]
        ):
            verdicts, _notes = await vrg.run_scenarios(runner, "text", None, "")

        self.assertEqual(verdicts["GEMINI LIVE RÉEL"], "PASS")
        self.assertEqual(verdicts["AUDIO RÉEL"], "NON TESTABLE")
        self.assertEqual(verdicts["RECONNEXION RÉELLE"], "PASS")
        self.assertEqual(verdicts["SESSION RESUMPTION RÉELLE"], "PASS")
        self.assertEqual(verdicts["OUTILS RÉELS"], "PASS")

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


class _ResumptionRunner:
    """Runner sans réseau : parcours nominal S1-S4, reprise S3 paramétrable.

    ``drop_traces`` : évènements de trace émis par la coupure (reprise
    confirmée, repli checkpoint périmé, handle refusé…). ``reseed`` : nombre
    de ``clientContent`` observés sur le câble pendant la fenêtre S3.
    """

    def __init__(self, drop_traces: list[dict], reseed: int) -> None:
        self.traces: list[dict] = []
        self._drop_traces = drop_traces
        self._reseed = reseed
        self.errors: list[str] = []
        self.gemini = SimpleNamespace(
            session_generation=1,
            resumption_handle=None,
            context_seed_confirmed=True,
            trace_events=lambda: list(self.traces),
        )

    async def ask(self, prompt, mode, client=None, tts_model=""):
        if "J'habite à Tours." in prompt:
            self.gemini.resumption_handle = "handle-frais"
            return {"user": prompt, "assistant": "Bien noté.", "tools": []}
        if "Où est-ce que j'habite" in prompt:
            return {"user": prompt, "assistant": "Tu habites à Tours.", "tools": []}
        if "validation_ping" in prompt:
            return {
                "user": prompt,
                "assistant": f"Le code est {vrg.VALIDATION_CODE}.",
                "tools": ["validation_ping"],
            }
        if "prénom" in prompt.lower():
            return {"user": prompt, "assistant": "Simon", "tools": []}
        return {"user": prompt, "assistant": "Oui, je t'entends.", "tools": []}

    async def drop_connection(self, keep_handle):
        previous = self.gemini.session_generation
        self.gemini.session_generation += 1
        for event in self._drop_traces:
            self.traces.append({"ts": time.monotonic(), **event})
        return previous

    async def wait_reconnected(self, generation):
        return None

    def diagnostic(self) -> str:
        return "{}"


class SessionResumptionVerdictTests(unittest.IsolatedAsyncioTestCase):
    """Le verdict S3 distingue reprise réelle, replis documentés et anomalies.

    Défaut reproduit (run réel 2026-10-25, PR #51 — « SESSION RESUMPTION
    RÉELLE : FAIL », « 1008 — Requested entity was not found », « la nouvelle
    session n'a pas été ouverte en reprise (handle refusé ?) », « un rejeu a
    été envoyé alors que le checkpoint couvrait l'historique (doublon
    potentiel) ») : le repli documenté du moteur (checkpoint périmé détecté
    avant connexion ou handle refusé → session neuve + rejeu qui PRÉSERVE le
    contexte) était compté comme un échec, car les preuves correspondantes
    (``SESSION_RESUME_STALE_CHECKPOINT`` source=preconnect,
    ``SESSION_RESUME_REJECTED``) sont tracées avec l'ancienne génération et le
    verdict ne les consultait pas.
    """

    async def _run(self, runner: _ResumptionRunner, reseed_s3: int):
        replay = {"kind": "client_content", "turn_complete": True}
        # 1er appel wire_since = fenêtre S2 (1 rejeu après reconnexion sans
        # handle) ; 2e appel = fenêtre S3 (reseed_s3 rejeus).
        with contextlib.redirect_stdout(io.StringIO()), mock.patch.object(
            vrg,
            "wire_since",
            side_effect=[[replay], [replay] * reseed_s3],
        ):
            verdicts, notes = await vrg.run_scenarios(runner, "text", None, "")
        return verdicts, notes

    async def test_resume_refuse_avec_rejeu_justifie_est_un_pass_documente(self):
        runner = _ResumptionRunner(
            [
                {"kind": "SESSION_RESUME_REJECTED", "session_generation": 1,
                 "error": "APIError 1008 Requested entity was not found", "code": 1008},
            ],
            1,
        )
        verdicts, notes = await self._run(runner, reseed_s3=1)
        self.assertEqual(verdicts["SESSION RESUMPTION RÉELLE"], "PASS")
        self.assertTrue(any("handle refusé" in note for note in notes), notes)
        self.assertFalse(any("doublon potentiel" in note for note in notes))

    async def test_checkpoint_perime_avant_connexion_avec_rejeu_est_un_pass(self):
        runner = _ResumptionRunner(
            [
                {"kind": "SESSION_RESUME_STALE_CHECKPOINT", "session_generation": 1,
                 "checkpoint": 4, "local_messages": 6, "source": "preconnect"},
            ],
            1,
        )
        verdicts, notes = await self._run(runner, reseed_s3=1)
        self.assertEqual(verdicts["SESSION RESUMPTION RÉELLE"], "PASS")
        self.assertTrue(any("checkpoint périmé" in note for note in notes), notes)

    async def test_rejeu_sans_preuve_reste_un_echec_doublon(self):
        """Strictesse conservée : un rejeu sans preuve de reprise impossible
        reste un doublon potentiel (FAIL) — aucun assouplissement."""
        runner = _ResumptionRunner([], 1)
        verdicts, notes = await self._run(runner, reseed_s3=1)
        self.assertEqual(verdicts["SESSION RESUMPTION RÉELLE"], "FAIL")
        self.assertTrue(any("doublon potentiel" in note for note in notes), notes)

    async def test_ni_reprise_ni_repli_est_undemontrable(self):
        runner = _ResumptionRunner([], 0)
        verdicts, notes = await self._run(runner, reseed_s3=0)
        self.assertEqual(verdicts["SESSION RESUMPTION RÉELLE"], "FAIL")
        self.assertTrue(any("indémontrable" in note for note in notes), notes)

    async def test_reprise_reelle_sans_rejeu_passe(self):
        runner = _ResumptionRunner(
            [{"kind": "SESSION_RESUME", "session_generation": 2}], 0
        )
        verdicts, _notes = await self._run(runner, reseed_s3=0)
        self.assertEqual(verdicts["SESSION RESUMPTION RÉELLE"], "PASS")


class ContinuousMicrophoneTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_validation_uses_the_production_submit_text_path(self):
        class FakeGemini:
            def __init__(self):
                self.submitted = []
                self.session = self

            async def submit_text(self, text):
                self.submitted.append(text)
                return {"accepted": True}

            async def send_realtime_input(self, **kwargs):
                raise AssertionError(f"validator bypassed submit_text: {kwargs}")

        runner = object.__new__(vrg.RealRunner)
        runner.gemini = FakeGemini()
        runner.turn_log = []
        runner._turn_done = asyncio.Event()
        runner._audio_chunks = []
        runner._wait_sendable = AsyncMock()
        runner._wait_answer = AsyncMock(return_value={"user": "Bonjour", "assistant": "Oui."})

        result = await runner.speak_text("Bonjour")

        self.assertEqual(runner.gemini.submitted, ["Bonjour"])
        runner._wait_answer.assert_awaited_once_with(0, unittest.mock.ANY, "le texte")
        self.assertEqual(result["assistant"], "Oui.")

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


class S6FailureDiagnosticTests(unittest.IsolatedAsyncioTestCase):
    """Un échec S6 doit s'auto-diagnostiquer : contexte restauré ou pas ?

    Défaut reproduit (run réel 2026-10-25, PR #51 — « S6 … : FAIL »,
    ``réponse=''``) : la note ne disait RIEN de l'état du rejeu de contexte ni
    des relances « génération vide » (bug serveur documenté
    googleapis/python-genai#2117), rendant chaque échec réel inexploitable
    sans une exécution supplémentaire. Le moteur trace déjà les preuves ; la
    note S6 les embarque désormais (lecture seule).
    """

    EVENTS = [
        {"kind": "SESSION_READY", "context_seeded": True, "context_seed_confirmed": True},
        {"kind": "EMPTY_GENERATION_RETRY", "session_generation": 2, "attempt": 1,
         "max_attempts": 2},
        {"kind": "EMPTY_GENERATION_RETRY", "session_generation": 2, "attempt": 2,
         "max_attempts": 2},
        {"kind": "MODEL_TURN_RECEIVED", "parts_count": 1, "has_inline_audio": False,
         "has_text_part": True},
        {"kind": "TURN_COMPLETE", "has_user_text": True, "has_model_content": False,
         "interaction_status": "IDLE", "waiting_for_input": False},
    ]

    def _runner(self):
        lab = SimpleNamespace(gemini_trace=lambda: list(self.EVENTS))
        return v174.ScenarioRunner(lab, {})

    def test_le_diagnostic_moteur_distingue_contexte_et_generation_vide(self):
        diag = self._runner()._engine_failure_diagnostic()
        self.assertIn("rejeu_envoyé=True", diag)
        self.assertIn("rejeu_confirmé=True", diag)
        self.assertIn("relances_generation_vide=2", diag)
        self.assertIn("tours_avec_question_sans_réponse=1", diag)
        self.assertIn("model_turn_sans_audio=1", diag)
        self.assertIn("attempt=2/2", diag)

    async def test_la_note_s6_embarque_les_preuves_moteur(self):
        runner = self._runner()
        runner.verdicts = {}
        runner.notes = []
        runner.say = lambda prompt: {"user": prompt, "assistant": ""}
        runner.scenario_6_real_context()
        self.assertEqual(runner.verdicts["S6 CONTEXTE REEL APRES RECONNEXION"], "FAIL")
        self.assertEqual(len(runner.notes), 1)
        self.assertIn("réponse=''", runner.notes[0])
        self.assertIn("rejeu_confirmé=True", runner.notes[0])
        self.assertIn("relances_generation_vide=2", runner.notes[0])


if __name__ == "__main__":
    unittest.main()
