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


class V174FixturesBackendTests(unittest.TestCase):
    """Backend PRIMAIRE v174 : les 5 phrases des scénarios ont une fixture.

    Défaut reproduit (run réel 2026-10-25) : pyttsx3 absent en CI → TTS
    Gemini 429 (quota) → repli Live conversationnel ayant envoyé une phrase
    ne correspondant PAS à la question (« Sunday, October 25, 2026 at 8:03
    PM UTC ») → 5/5 NON TESTABLE. Les fixtures versionnées suppriment cette
    chaîne : zéro requête TTS pour les phrases couvertes.
    """

    def test_les_cinq_phrases_de_scenarios_sont_couvertes(self) -> None:
        cache, problems = v174.synthesize_from_fixtures(v174.PROMPTS)
        self.assertEqual(problems, [])
        self.assertEqual(sorted(cache), sorted(v174.PROMPTS))
        for pcm in cache.values():
            self.assertIsInstance(pcm, bytes)
            self.assertGreater(len(pcm), 1600)

    def test_prompt_hors_liste_retombe_explicitement(self) -> None:
        cache, problems = v174.synthesize_from_fixtures(["Phrase hors scénarios."])
        self.assertEqual(cache, {})
        self.assertEqual(len(problems), 1)


class S6CauseSeparationTests(unittest.TestCase):
    """S6 sépare transcription, récitation d'outil, génération et contexte.

    Run 40370419959 : « Quel est mon frère ? » entendu au lieu de « Quel est
    mon prénom ? » et « [appel outil] recall(...) » prononcé au lieu d'un
    toolCall natif — deux causes qui ne doivent PAS être rapportées comme un
    échec de contexte, et qui ne doivent JAMAIS permettre un PASS.
    """

    def _runner(self, heard: str, answer: str, tool_events: list[dict]):
        lab = SimpleNamespace(
            gemini_trace=lambda: list(tool_events),
        )
        runner = v174.ScenarioRunner(lab, {})
        runner.verdicts = {}
        runner.notes = []
        runner.say = lambda prompt, **kw: {
            "user": heard, "assistant": answer, "tools": []
        }
        return runner

    def test_transcription_inexacte_rapportee_comme_telle(self):
        runner = self._runner(
            "Quel est mon frère ?", "Tu t'appelles Simon.", []
        )
        runner.scenario_6_real_context()
        self.assertEqual(runner.verdicts["S6 CONTEXTE REEL APRES RECONNEXION"], "FAIL")
        note = runner.notes[0]
        self.assertIn("TRANSCRIPTION inexacte", note)
        self.assertIn("Quel est mon frère ?", note)
        self.assertNotIn("RÉCITATION", note)
        # Le contexte n'est PAS déclaré restauré : aucune mention de succès.
        self.assertNotIn("PASS", note)

    def test_recitation_appel_outil_rapportee_comme_telle(self):
        runner = self._runner(
            "Quel est mon prénom ?",
            "[appel outil]  recall(limit=1,  query='prénom')",
            [{"kind": "TOOL_CALL_RECEIVED"}, {"kind": "TOOL_CALL_EXECUTED"}],
        )
        runner.scenario_6_real_context()
        self.assertEqual(runner.verdicts["S6 CONTEXTE REEL APRES RECONNEXION"], "FAIL")
        note = runner.notes[0]
        self.assertIn("RÉCITATION d'appel d'outil", note)
        self.assertIn("toolCall natif", note)

    def test_reponse_fidele_sans_fait_memorise_cause_contexte(self):
        runner = self._runner(
            "Quel est mon prénom ?", "Je ne sais pas ton prénom.", []
        )
        runner.scenario_6_real_context()
        self.assertEqual(runner.verdicts["S6 CONTEXTE REEL APRES RECONNEXION"], "FAIL")
        note = runner.notes[0]
        self.assertIn("fait mémorisé attendu", note)
        self.assertNotIn("TRANSCRIPTION inexacte", note)
        self.assertNotIn("RÉCITATION", note)

    def test_preuve_complete_passe(self):
        runner = self._runner(
            "Quel est mon prénom ?", "Ton prénom est Simon.", []
        )
        runner.scenario_6_real_context()
        self.assertEqual(runner.verdicts["S6 CONTEXTE REEL APRES RECONNEXION"], "PASS")
        self.assertEqual(runner.notes, [])

    def test_normalisation_tolere_ponctuation_et_casse(self):
        normalise = v174.ScenarioRunner._normalize_speech
        self.assertEqual(
            normalise("Quel est mon prénom ?"), normalise("quel est mon prenom")
        )
        self.assertNotEqual(
            normalise("Quel est mon prénom ?"), normalise("Quel est mon frère ?")
        )


class ToolDeclarationShapeTests(unittest.TestCase):
    """Les déclarations d'outils (produit + validateur) survivent au SDK.

    Garde-fou contre une malformation silencieuse des déclarations (le
    modèle cesse alors d'appeler l'outil — symptôme « aucun tool_call ») :
    la forme restreinte OpenAPI est exigée, et la sérialisation SDK réelle
    doit produire un câble valide.
    """

    def test_declaration_validation_ping_forme_restriete(self):
        from unittest import mock as _mock

        patchers = vrg._install_validation_tool()
        for patcher in patchers:
            patcher.start()
        try:
            declarations = vrg.gl.TOOL_DECLARATIONS
            self.assertEqual([d["name"] for d in declarations], ["validation_ping"])
            params = declarations[0]["parameters"]
            self.assertEqual(params["type"], "object")
            self.assertIsInstance(params["properties"], dict)
            self.assertIsInstance(params["required"], list)
            # La sérialisation SDK réelle produit un câble valide.
            wire = vrg.gl.types.FunctionDeclaration(
                name=declarations[0]["name"],
                description=declarations[0]["description"],
                parameters=params,
            ).model_dump(exclude_none=True)
            self.assertEqual(wire["name"], "validation_ping")
            type_value = wire["parameters"]["type"]
            self.assertEqual(getattr(type_value, "name", str(type_value)),
                             "OBJECT")
        finally:
            for patcher in patchers:
                patcher.stop()

    def test_toutes_les_declarations_du_produit_sont_en_forme_restriete(self):
        from src.tools import TOOL_DECLARATIONS

        allowed_top = {"name", "description", "parameters"}
        for decl in TOOL_DECLARATIONS:
            self.assertTrue(set(decl) <= allowed_top,
                            f"{decl.get('name')} : clés hors forme restreinte : {set(decl)}")
            params = decl.get("parameters")
            if params is not None:
                self.assertEqual(params.get("type"), "object",
                                 f"{decl['name']} : type de paramètres non restreint")
                self.assertIsInstance(params.get("properties", {}), dict)
                self.assertIsInstance(params.get("required", []), list)


class _DiagnosticRunner:
    """Runner minimal pour ``_tool_failure_diagnostic`` (aucune API)."""

    def __init__(self, traces: list[dict], wire_kinds: list[str], can_send=(True, "ok")) -> None:
        self.gemini = SimpleNamespace(
            trace_events=lambda: list(traces),
            _can_send_now=lambda: can_send,
        )
        self._wire_kinds = wire_kinds

    def diagnostic(self) -> str:
        return '{"etat": "test"}'


class ToolFailureDiagnosticTests(unittest.TestCase):
    """Le diagnostic S4 distingue les 4 causes d'un « OUTILS RÉELS : FAIL ».

    Exigence mission 3 : le test réel doit tracer et distinguer « aucun
    appel reçu » / « appel non exécuté » / « réponse d'outil envoyée » /
    « réponse finale absente » — sinon la note ``outils=[]`` du run réel
    reste ininterprétable.
    """

    QUESTION = {"user": "Utilise l'outil validation_ping.", "assistant": "", "tools": []}

    def _run(self, runner, wire_kinds):
        with mock.patch.object(vrg, "WIRE", [{"kind": k} for k in wire_kinds]):
            return vrg._tool_failure_diagnostic(runner, dict(self.QUESTION))

    def test_cas_1_aucun_appel_recu(self) -> None:
        runner = _DiagnosticRunner([], wire_kinds=["client_content"])
        diag = self._run(runner, ["client_content"])
        self.assertIn("appels_outil_executes=0 (aucun tool_call du serveur)", diag)
        # !r échappe les apostrophes avec des guillemets doubles — accepter
        # les deux rendus.
        heard = diag.split("question_entendue=", 1)[1].split(" ; ", 1)[0]
        self.assertIn("Utilise l'outil validation_ping.", heard)
        self.assertIn("reponses_outil_envoyees=0", diag)

    def test_cas_2_appel_execute_reponse_non_envoyee(self) -> None:
        runner = _DiagnosticRunner(
            [{"kind": "TOOL_CALL_EXECUTED", "name": "validation_ping"}],
            wire_kinds=["tool_call"],
        )
        diag = self._run(runner, ["tool_call"])
        self.assertIn("appels_outil_executes=1 ['validation_ping']", diag)
        self.assertIn("reponses_outil_envoyees=0", diag)
        self.assertIn("tool_response_sur_cable=0", diag)

    def test_cas_3_reponse_envoyee_sur_le_cable(self) -> None:
        runner = _DiagnosticRunner(
            [
                {"kind": "TOOL_CALL_EXECUTED", "name": "validation_ping"},
                {"kind": "TOOL_RESPONSE_SENT", "names": ["validation_ping"]},
            ],
            wire_kinds=["tool_call", "tool_response"],
        )
        diag = self._run(runner, ["tool_call", "tool_response"])
        self.assertIn("reponses_outil_envoyees=1", diag)
        self.assertIn("tool_response_sur_cable=1", diag)

    def test_cas_4_reponse_envoyee_mais_reponse_finale_absente(self) -> None:
        runner = _DiagnosticRunner(
            [
                {"kind": "TOOL_CALL_EXECUTED", "name": "validation_ping"},
                {"kind": "TOOL_RESPONSE_SENT", "names": ["validation_ping"]},
                {"kind": "EMPTY_GENERATION_RETRY", "attempt": 1},
                {"kind": "EMPTY_GENERATION_RETRY", "attempt": 2},
            ],
            wire_kinds=["tool_call", "tool_response"],
            can_send=(False, "generation vide"),
        )
        diag = self._run(runner, ["tool_call", "tool_response"])
        self.assertIn("relances_generation_vide=2", diag)
        self.assertIn("porte_micro=False:generation vide", diag)
        self.assertIn('{"etat": "test"}', diag)


if __name__ == "__main__":
    unittest.main()
