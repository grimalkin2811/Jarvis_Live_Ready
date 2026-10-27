"""Verdicts des validateurs réels (v1.9.0) — S4, S6 et agrégation finale.

Trois défauts corrigés ici, chacun figé par des tests de régression :

1. **S4 (verdict proxy)** — ``OUTILS RÉELS`` passait dès que l'outil était
   nommé ET que le code apparaissait dans la réponse. La chaîne d'événements
   exigée (réception → validation → exécution → envoi du résultat → clôture
   du tour outil) n'était consultée qu'en cas d'ÉCHEC, donc jamais prouvée
   sur un PASS. Elle est désormais la condition du PASS, et chaque maillon
   manquant est rapporté nommément.

2. **S6 (transcription vs contexte)** — une question mal entendue
   (« mon frère » au lieu de « mon prénom ») donnait ``FAIL``, imputant au
   contexte une faute de l'ASR. Le verdict est maintenant ``NON TESTABLE``
   SAUF si le contexte est vérifié indépendamment de l'ASR (relance en texte,
   qui traverse la même session sans reconnaissance vocale).

3. **Agrégation** — un ``NON TESTABLE`` ou un ``FAIL`` doit faire échouer le
   job. Aucun des deux ne peut être absorbé en vert.

Ces tests ne disent rien du service réel : ils figent la sincérité des
verdicts et la stricte conservation des preuves.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import validate_real_gemini as vrg  # noqa: E402
import validate_reconnect_v174_real as v174  # noqa: E402


def _event(kind: str, ts: float, **fields) -> dict:
    return {"kind": kind, "ts": ts, **fields}


def _full_chain(ts0: float = 10.0, tool: str = "validation_ping") -> list[dict]:
    """Les cinq maillons d'un aller-retour d'outil réussi, dans l'ordre."""
    return [
        _event("TOOL_CALL_RECEIVED", ts0 + 0.0, names=[tool], count=1),
        _event("TOOL_CALL_VALIDATED", ts0 + 0.1, name=tool),
        _event("TOOL_CALL_EXECUTED", ts0 + 0.2, name=tool),
        _event("TOOL_RESPONSE_SENT", ts0 + 0.3, names=[tool]),
        _event("TOOL_RESULT_TURN_COMPLETED", ts0 + 0.4, has_assistant_text=True),
    ]


def _runner_with(events: list[dict]):
    return SimpleNamespace(
        gemini=SimpleNamespace(trace_events=lambda: list(events)),
    )


class ToolRoundtripChainTests(unittest.TestCase):
    """S4 : la chaîne de preuves, maillon par maillon."""

    def test_chaine_complete_aucun_maillon_manquant(self):
        chain = vrg._tool_roundtrip_chain(_runner_with(_full_chain()), 0.0, "validation_ping")
        self.assertEqual(chain["missing"], [])
        self.assertEqual(chain["observed"], [label for label, _, _ in vrg.TOOL_CHAIN_STEPS])

    def test_chaque_maillon_manquant_est_nomme(self):
        """Retirer UN maillon doit être détecté, et ce maillon précisément."""
        for index, (label, kind, _key) in enumerate(vrg.TOOL_CHAIN_STEPS):
            with self.subTest(maillon=f"{label} ({kind})"):
                events = _full_chain()
                del events[index]
                chain = vrg._tool_roundtrip_chain(_runner_with(events), 0.0, "validation_ping")
                self.assertIn(label, chain["missing"])
                self.assertNotIn(label, chain["observed"])

    def test_ordre_strict_un_maillon_inverse_est_manquant(self):
        """Une clôture de tour AVANT l'envoi du résultat ne prouve rien."""
        events = _full_chain()
        # La clôture survient chronologiquement AVANT l'envoi du résultat.
        events[4]["ts"] = events[3]["ts"] - 0.05
        chain = vrg._tool_roundtrip_chain(_runner_with(events), 0.0, "validation_ping")
        self.assertIn("clôture du tour outil", chain["missing"])

    def test_un_autre_outil_ne_credite_pas_s4(self):
        """S1-S3 ont pu exécuter ``recall`` : cela ne prouve pas S4."""
        events = _full_chain(tool="recall")
        chain = vrg._tool_roundtrip_chain(_runner_with(events), 0.0, "validation_ping")
        # Les quatre maillons NOMMÉS manquent ; la clôture de tour, qui ne
        # porte aucun nom d'outil, ne suffit évidemment pas à créditer S4.
        self.assertEqual(
            chain["missing"],
            ["réception", "validation", "exécution", "envoi du résultat"],
        )
        self.assertEqual(chain["observed"], ["clôture du tour outil"])

    def test_evenements_anterieurs_au_scenario_ignores(self):
        """La trace couvre toute la session : seul le tour de S4 compte."""
        events = _full_chain(ts0=1.0)
        chain = vrg._tool_roundtrip_chain(_runner_with(events), 10.0, "validation_ping")
        self.assertEqual(chain["observed"], [])

    def test_appel_multiple_conjoint_reste_valide(self):
        """Un tool_call groupé nomme plusieurs outils : le nôtre doit compter."""
        events = _full_chain()
        events[0]["names"] = ["recall", "validation_ping"]
        events[0]["count"] = 2
        chain = vrg._tool_roundtrip_chain(_runner_with(events), 0.0, "validation_ping")
        self.assertEqual(chain["missing"], [])

    def test_un_maillon_sans_nom_dexploitation_ne_suffit_pas(self):
        """``TOOL_CALL_VALIDATED`` sans nom ne prouve pas quel outil a été validé."""
        events = _full_chain()
        events[1].pop("name")
        chain = vrg._tool_roundtrip_chain(_runner_with(events), 0.0, "validation_ping")
        self.assertIn("validation", chain["missing"])


class S4VerdictTests(unittest.TestCase):
    """S4 : la décision elle-même, pilotée par la chaîne."""

    def test_pass_exige_chaine_complete_et_code_restitue(self):
        chain = vrg._tool_roundtrip_chain(_runner_with(_full_chain()), 0.0, "validation_ping")
        ok, reasons = vrg._s4_verdict(f"C'était {vrg.VALIDATION_CODE}.", chain)
        self.assertTrue(ok, reasons)
        self.assertEqual(reasons, [])

    def test_chaine_incomplete_est_un_echec_meme_si_le_code_est_present(self):
        """Le cœur du correctif : le proxy ne suffit plus."""
        events = _full_chain()
        del events[3]  # plus de TOOL_RESPONSE_SENT
        chain = vrg._tool_roundtrip_chain(_runner_with(events), 0.0, "validation_ping")
        ok, reasons = vrg._s4_verdict(f"C'était {vrg.VALIDATION_CODE}.", chain)
        self.assertFalse(ok)
        self.assertTrue(any("INCOMPLÈTE" in r and "envoi du résultat" in r for r in reasons), reasons)

    def test_echec_si_le_code_absent_malgre_une_chaine_complete(self):
        chain = vrg._tool_roundtrip_chain(_runner_with(_full_chain()), 0.0, "validation_ping")
        ok, reasons = vrg._s4_verdict("Je ne sais pas.", chain)
        self.assertFalse(ok)
        self.assertTrue(any("n'a pas été retrouvé" in r for r in reasons), reasons)

    def test_les_deux_causes_sont_rapportees_ensemble(self):
        chain = {"observed": [], "missing": ["réception"], "details": []}
        ok, reasons = vrg._s4_verdict("", chain)
        self.assertFalse(ok)
        self.assertEqual(len(reasons), 2, reasons)


class S6ContextVsTranscriptionTests(unittest.TestCase):
    """S6 : ne jamais imputer à la mémoire une faute de reconnaissance vocale."""

    EXPECTED = "Quel est mon prénom ?"
    #: Ordre des verdicts imprimés par ``validate_reconnect_v174_real``.
    V174_NAMES = [
        "S1+S4 SILENCE PUIS EXPIRATION",
        "S2 DEUX TOURS RAPIDES",
        "S3 TROIS TOURS",
        "S5 RECONNEXION REELLE",
        "S6 CONTEXTE REEL APRES RECONNEXION",
    ]

    def _runner(self, *, heard, answer, submit=None, text_turn=None):
        lab = SimpleNamespace(
            gemini_trace=lambda: [],
            turns=[1],
            gemini=SimpleNamespace(**({"submit_text_threadsafe": submit} if submit else {})),
            wait_for_new_turn=lambda since, timeout: text_turn,
        )
        runner = v174.ScenarioRunner(lab, {})
        runner.verdicts = {}
        runner.notes = []
        runner.say = lambda prompt, **kw: {"user": heard, "assistant": answer}
        return runner

    def test_transcription_fidele_reponse_correcte_pass(self):
        runner = self._runner(heard=self.EXPECTED, answer="Votre prénom est Simon.")
        runner.scenario_6_real_context()
        self.assertEqual(runner.verdicts["S6 CONTEXTE REEL APRES RECONNEXION"], "PASS")

    def test_transcription_fidele_reponse_sans_le_fait_est_un_echec(self):
        """Une entrée correcte + réponse fausse = VRAI échec de contexte."""
        runner = self._runner(heard=self.EXPECTED, answer="Je ne sais pas.")
        runner.scenario_6_real_context()
        self.assertEqual(runner.verdicts["S6 CONTEXTE REEL APRES RECONNEXION"], "FAIL")

    def test_transcription_inexacte_sans_verification_independante_non_testable(self):
        """« mon frère » ≠ échec de contexte : le test n'a pas eu lieu."""
        runner = self._runner(heard="Quel est mon frère ?", answer="Simon.")
        runner.scenario_6_real_context()
        self.assertEqual(
            runner.verdicts["S6 CONTEXTE REEL APRES RECONNEXION"], "NON TESTABLE"
        )

    def test_transcription_inexacte_avec_relance_texte_concluante_pass(self):
        """L'ASR a échoué, mais le TEXTE prouve le contexte : PASS légitime."""
        import concurrent.futures
        fut = concurrent.futures.Future()
        fut.set_result({"ok": True})
        runner = self._runner(
            heard="Quel est mon frère ?",
            answer="Simon.",
            submit=lambda text: fut,
            text_turn={"user": self.EXPECTED, "assistant": "Votre prénom est Simon."},
        )
        runner.scenario_6_real_context()
        self.assertEqual(runner.verdicts["S6 CONTEXTE REEL APRES RECONNEXION"], "PASS")
        self.assertTrue(
            any("VÉRIFIÉ INDÉPENDAMMENT" in n for n in runner.notes), runner.notes
        )

    def test_transcription_inexacte_relance_texte_muette_non_testable(self):
        """Une relance texte qui ne restitue rien ne transforme pas en FAIL."""
        import concurrent.futures
        fut = concurrent.futures.Future()
        fut.set_result({"ok": True})
        runner = self._runner(
            heard="Quel est mon frère ?",
            answer="Simon.",
            submit=lambda text: fut,
            text_turn={"user": self.EXPECTED, "assistant": "Je ne sais pas."},
        )
        runner.scenario_6_real_context()
        self.assertEqual(
            runner.verdicts["S6 CONTEXTE REEL APRES RECONNEXION"], "NON TESTABLE"
        )

    def test_recitation_interdit_le_pass(self):
        runner = self._runner(
            heard=self.EXPECTED, answer="[appel outil] recall(...) Simon.")
        runner.scenario_6_real_context()
        self.assertEqual(runner.verdicts["S6 CONTEXTE REEL APRES RECONNEXION"], "FAIL")

    def test_verification_independante_absente_si_moteur_sans_texte(self):
        lab = SimpleNamespace(gemini_trace=lambda: [], turns=[], gemini=SimpleNamespace())
        runner = v174.ScenarioRunner(lab, {})
        self.assertIsNone(runner._verify_context_without_asr(self.EXPECTED))


class ConclusionAggregationTests(unittest.TestCase):
    """Étape « Conclusion » du workflow : ni FAIL ni NON TESTABLE ne passent."""

    WORKFLOW = Path(ROOT) / ".github" / "workflows" / "validate-gemini-live.yml"

    @staticmethod
    def _conclusion(logs: dict[str, str], outcomes: dict[str, str]) -> bool:
        """Réplique fidèle de la logique de l'étape Conclusion (FAILED==0)."""
        if not logs:
            return False
        failed = False
        for name, text in logs.items():
            if " : FAIL" in text:
                failed = True
            if "NON TESTABLE" in text:
                failed = True
        for outcome in outcomes.values():
            if outcome != "success":
                failed = True
        return not failed

    def _capture(self, printer, verdicts) -> str:
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            printer(verdicts, [])
        return buffer.getvalue()

    def test_tout_pass_est_vert(self):
        text = self._capture(vrg.print_verdicts, {n: "PASS" for n in vrg.VERDICT_NAMES})
        self.assertTrue(self._conclusion({"validation.log": text}, {"gemini": "success"}))

    def test_un_fail_rend_le_job_rouge(self):
        verdicts = {n: "PASS" for n in vrg.VERDICT_NAMES}
        verdicts["OUTILS RÉELS"] = "FAIL"
        text = self._capture(vrg.print_verdicts, verdicts)
        self.assertFalse(self._conclusion({"validation.log": text}, {"gemini": "success"}))

    def test_un_non_testable_rend_le_job_rouge(self):
        """NON TESTABLE ne peut JAMAIS être converti en PASS."""
        verdicts = {n: "PASS" for n in vrg.VERDICT_NAMES}
        verdicts["AUDIO RÉEL"] = "NON TESTABLE"
        text = self._capture(vrg.print_verdicts, verdicts)
        self.assertFalse(self._conclusion({"validation.log": text}, {"gemini": "success"}))

    def test_verdict_absent_est_non_testable_par_defaut(self):
        text = self._capture(vrg.print_verdicts, {})
        self.assertIn("NON TESTABLE", text)
        self.assertFalse(self._conclusion({"validation.log": text}, {"gemini": "success"}))

    def test_v174_partage_la_meme_propriete(self):
        verdicts = {n: "PASS" for n in S6ContextVsTranscriptionTests.V174_NAMES}
        verdicts["S6 CONTEXTE REEL APRES RECONNEXION"] = "NON TESTABLE"
        text = self._capture(v174.print_verdicts, verdicts)
        self.assertFalse(self._conclusion({"validation_v174.log": text}, {"v174": "success"}))

    def test_le_workflow_garde_les_deux_garde_fous(self):
        """Si quelqu'un retire un grep, l'agrégation deviendrait complaisante."""
        content = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("grep -q ' : FAIL'", content)
        self.assertIn("grep -q 'NON TESTABLE'", content)


class WiringTests(unittest.TestCase):
    """Le code corrigé est-il bien CELUI QUE LA CI EXÉCUTE ?

    Deux pièges possibles après une reprise de mission :
    * les correctifs existent mais ne sont jamais appelés (code mort) ;
    * la CI exécute un autre script que celui qu'on a corrigé.

    Ces tests ferment les deux.
    """

    SCRIPTS = Path(ROOT) / "scripts"
    WORKFLOW = Path(ROOT) / ".github" / "workflows" / "validate-gemini-live.yml"

    def test_s4_appelle_reellement_la_chaine_de_preuves(self):
        source = (self.SCRIPTS / "validate_real_gemini.py").read_text(encoding="utf-8")
        self.assertIn('_tool_roundtrip_chain(runner, s4_mark, "validation_ping")', source)
        self.assertIn("_s4_verdict(answer, chain)", source)

    def test_lancien_proxy_ne_suffit_plus_a_lui_seul(self):
        """Garde-fou anti-régression : la formule du proxy ne doit pas revenir."""
        source = (self.SCRIPTS / "validate_real_gemini.py").read_text(encoding="utf-8")
        self.assertNotIn('tool_ok = "validation_ping" in tools and VALIDATION_CODE', source)
        self.assertNotIn('tools = turn.get("tools", []) and VALIDATION_CODE', source)

    def test_s6_appelle_reellement_la_verification_independante(self):
        source = (self.SCRIPTS / "validate_reconnect_v174_real.py").read_text(encoding="utf-8")
        self.assertIn("_verify_context_without_asr(expected)", source)
        # La transcription inexacte ne doit plus produire FAIL par défaut.
        self.assertIn('self.verdicts[name] = "NON TESTABLE"', source)

    def test_la_ci_execute_les_scripts_corriges(self):
        """Le workflow doit lancer EXACTEMENT les deux fichiers corrigés."""
        content = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("python scripts/validate_real_gemini.py", content)
        self.assertIn("python scripts/validate_reconnect_v174_real.py", content)

    def test_la_ci_transmet_bien_le_secret_sans_lefficher(self):
        content = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("GEMINI_API_KEY: ${{ secrets.GEMINI_API_KEY }}", content)
        # Jamais d'écho de la clé.
        self.assertNotIn("echo $GEMINI_API_KEY", content)
        self.assertNotIn("echo \"$GEMINI_API_KEY\"", content)

    def test_lagregateur_est_le_dernier_mot(self):
        """Si la conclusion était neutralisée, un NON TESTABLE passerait."""
        content = self.WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("if [ \"$FAILED\" = \"1\" ]; then exit 1; fi", content)


if __name__ == "__main__":
    unittest.main()
