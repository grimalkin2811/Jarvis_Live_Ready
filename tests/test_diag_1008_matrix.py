"""Matrice discriminante 1008 : le plan doit être SOUND avant tout appel réel.

La matrice de ``scripts/diag_1008_matrix.py`` ne vaut que si les sondes
produisent réellement des configurations DIFFÉRENTES sur le câble. Sans cette
vérification, on lancerait des appels API réels en croyant comparer des choses
distinctes alors que deux sondes seraient identiques.

Point de vigilance central : le script ne doit JAMAIS conclure « cycle de vie
fautif » simplement parce que les quatre cases du plan factoriel échouent. La
sonde E (aucun handle réutilisé) existe pour trancher, et le verdict doit
refuser de conclure quand elle infirme la piste.

Ces tests tournent hors ligne, contre le faux serveur Live, et ne consomment
aucun quota. Ils vérifient aussi que le script ne peut pas produire de faux
succès sans clé.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import diag_1008_matrix as diag  # noqa: E402
from src import gemini_live as gl  # noqa: E402
from tests.test_live_context_harness import _HarnessCase  # noqa: E402


class ConfigLeversTests(_HarnessCase):
    """Les deux leviers changent bien ce qui part sur le câble."""

    async def test_reference_active_history_et_compression(self) -> None:
        server = self.make_server()
        await self.make_harness(server)
        config = server.last_config
        self.assertIsNotNone(getattr(config, "history_config", None))
        compression = getattr(config, "context_window_compression", None)
        self.assertIsNotNone(compression)
        self.assertIsNotNone(getattr(compression, "sliding_window", None))

    async def test_sans_history_le_champ_disparait(self) -> None:
        with mock.patch.object(gl, "HISTORY_CONFIG_ENABLED", False):
            server = self.make_server()
            await self.make_harness(server)
            self.assertIsNone(getattr(server.last_config, "history_config", None))
            self.assertIsNotNone(
                getattr(server.last_config, "context_window_compression", None)
            )

    async def test_sans_compression_le_champ_disparait(self) -> None:
        with mock.patch.object(gl, "CONTEXT_COMPRESSION_ENABLED", False):
            server = self.make_server()
            await self.make_harness(server)
            self.assertIsNone(
                getattr(server.last_config, "context_window_compression", None)
            )
            self.assertIsNotNone(getattr(server.last_config, "history_config", None))

    async def test_sans_les_deux_aucun_des_champs(self) -> None:
        with mock.patch.object(gl, "HISTORY_CONFIG_ENABLED", False), \
             mock.patch.object(gl, "CONTEXT_COMPRESSION_ENABLED", False):
            server = self.make_server()
            await self.make_harness(server)
            config = server.last_config
            self.assertIsNone(getattr(config, "history_config", None))
            self.assertIsNone(getattr(config, "context_window_compression", None))

    async def test_sliding_window_sans_target_tokens_est_bien_ce_qui_part(self) -> None:
        """Documente la suspicion sans l'ériger en conclusion.

        Ce test ne dit PAS que c'est la cause du 1008 — il fige le fait que
        c'est bien cette forme-là qui est envoyée aujourd'hui, pour que la
        matrice soit interprétable (sonde A = cette forme exacte).
        """
        server = self.make_server()
        await self.make_harness(server)
        window = getattr(server.last_config.context_window_compression, "sliding_window", None)
        self.assertIsNotNone(window)
        self.assertIsNone(
            getattr(window, "target_tokens", "absent"),
            "si target_tokens devient renseigné, la matrice doit être relue",
        )


class ProbePlanTests(unittest.TestCase):
    """Le plan est factoriel complet, et E teste vraiment l'absence de reprise."""

    def _spec(self, probe_id: str) -> dict:
        return next(p for p in diag.PROBES if p["id"] == probe_id)

    def test_plan_factoriel_complet_2x2(self) -> None:
        combos = {
            (p["history"], p["compression"])
            for p in diag.PROBES if p["id"] in diag.FACTORIAL_IDS
        }
        self.assertEqual(combos, {(True, True), (False, True), (True, False), (False, False)})

    def test_une_seule_variable_change_par_opposition(self) -> None:
        a, b, c = self._spec("A"), self._spec("B"), self._spec("C")
        self.assertEqual(a["compression"], b["compression"])
        self.assertNotEqual(a["history"], b["history"])
        self.assertEqual(a["history"], c["history"])
        self.assertNotEqual(a["compression"], c["compression"])

    def test_sonde_e_ne_reutilise_aucun_handle(self) -> None:
        self.assertFalse(self._spec("E")["resume"])
        for pid in diag.FACTORIAL_IDS:
            self.assertTrue(self._spec(pid)["resume"])

    def test_la_sonde_e_est_hors_du_plan_factoriel(self) -> None:
        self.assertEqual(diag.FACTORIAL_IDS, ("A", "B", "C", "D"))
        self.assertEqual(len(diag.PROBES), 5)


class BuildConfigTests(unittest.TestCase):
    """Chaque sonde produit réellement la configuration annoncée."""

    def test_reference_a_tout(self) -> None:
        config = diag.build_config(next(p for p in diag.PROBES if p["id"] == "A"))
        self.assertTrue(config.history_config.initial_history_in_client_content)
        self.assertIsNotNone(config.context_window_compression.sliding_window)
        self.assertIsNotNone(config.session_resumption)

    def test_sonde_b_retire_history(self) -> None:
        config = diag.build_config(next(p for p in diag.PROBES if p["id"] == "B"))
        self.assertIsNone(config.history_config)
        self.assertIsNotNone(config.context_window_compression)

    def test_sonde_c_retire_compression(self) -> None:
        config = diag.build_config(next(p for p in diag.PROBES if p["id"] == "C"))
        self.assertIsNone(config.context_window_compression)
        self.assertIsNotNone(config.history_config)

    def test_sonde_d_retire_les_deux(self) -> None:
        config = diag.build_config(next(p for p in diag.PROBES if p["id"] == "D"))
        self.assertIsNone(config.history_config)
        self.assertIsNone(config.context_window_compression)

    def test_sonde_e_nexpose_aucun_session_resumption(self) -> None:
        """Clé de l'interprétation : E ne peut pas réutiliser de handle."""
        config = diag.build_config(next(p for p in diag.PROBES if p["id"] == "E"))
        self.assertIsNone(
            getattr(config, "session_resumption", None),
            "la sonde E doit être indiscernable d'une session neuve côté reprise",
        )
        # … mais elle garde la configuration de référence pour le reste.
        self.assertIsNotNone(config.history_config)
        self.assertIsNotNone(config.context_window_compression)

    def test_le_handle_transmis_est_celui_attendu(self) -> None:
        config = diag.build_config(
            next(p for p in diag.PROBES if p["id"] == "A"), handle="H-123"
        )
        self.assertEqual(config.session_resumption.handle, "H-123")


class CloseCodeTests(unittest.TestCase):
    """Le code de fermeture est une DONNÉE, lue telle quelle."""

    def test_code_entier_sur_lexception(self) -> None:
        class ApiError(Exception):
            code = 1008

        code, reason = diag.close_details(ApiError("Requested entity was not found."))
        self.assertEqual(code, 1008)
        self.assertIn("Requested entity", reason)

    def test_code_absent_mais_present_dans_le_texte(self) -> None:
        code, _ = diag.close_details(RuntimeError("socket closed with 1008"))
        self.assertEqual(code, 1008)

    def test_aucun_code(self) -> None:
        code, _ = diag.close_details(RuntimeError("boum"))
        self.assertIsNone(code)
        self.assertIsNone(diag.close_details(None)[0])

    def test_classification_1008_conserve_le_code(self) -> None:
        class ApiError(Exception):
            code = 1008

        verdict, detail, close_code = diag.classify(
            ApiError("Requested entity was not found."), "reprise de session", True
        )
        self.assertEqual(verdict, "1008")
        self.assertEqual(close_code, 1008)
        self.assertIn("handle_reçu=True", detail)


class VerdictLogicTests(unittest.TestCase):
    """Cœur de la vigilance : ne jamais conclure « lifecycle » à la légère."""

    @staticmethod
    def _row(probe_id: str, code: str) -> dict:
        return {"id": probe_id, "code": code}

    def test_quatre_echecs_ne_concluent_pas_lifecycle(self) -> None:
        rows = [self._row(i, "1008") for i in diag.FACTORIAL_IDS]
        verdict = diag._verdict_factorial(rows)
        self.assertIn("discriminante", verdict)
        self.assertNotIn("lifecycle", verdict.lower())

    def test_sonde_e_en_1008_ecarte_la_reprise(self) -> None:
        rows = [self._row(i, "1008") for i in diag.FACTORIAL_IDS]
        rows.append(self._row("E", "1008"))
        verdict = diag._verdict_lifecycle(rows)
        self.assertIn("n'est PAS la cause", verdict)
        self.assertIn("close code", verdict)

    def test_sonde_e_saine_implique_le_cycle_de_vie(self) -> None:
        rows = [self._row(i, "1008") for i in diag.FACTORIAL_IDS]
        rows.append(self._row("E", "OK"))
        verdict = diag._verdict_lifecycle(rows)
        self.assertIn("IMPLIQUÉ", verdict)

    def test_parametre_isole(self) -> None:
        rows = [self._row("A", "1008"), self._row("B", "OK"),
                self._row("C", "OK"), self._row("D", "OK"), self._row("E", "OK")]
        self.assertIn("A", diag._verdict_factorial(rows))
        self.assertIn("paramètre de configuration isolé", diag._verdict_lifecycle(rows))

    def test_aucun_1008(self) -> None:
        rows = [self._row(i, "OK") for i in diag.FACTORIAL_IDS] + [self._row("E", "OK")]
        self.assertIn("aucun 1008", diag._verdict_factorial(rows))

    def test_sonde_e_absente_ne_conclut_rien(self) -> None:
        rows = [self._row(i, "1008") for i in diag.FACTORIAL_IDS]
        self.assertIn("non testé", diag._verdict_lifecycle(rows))

    def test_rendu_complet_ne_conclut_pas_lifecycle_si_e_echoue(self) -> None:
        """Le rendu texte complet doit porter l'avertissement explicite."""
        rows = []
        for spec in diag.PROBES:
            rows.append({
                "id": spec["id"], "history": spec["history"],
                "compression": spec["compression"], "resume": spec["resume"],
                "model": diag.MODEL, "tour1": "ok", "handle": True,
                "reprise": "-", "tour2": "-", "code": "1008",
                "close_code": 1008, "where": "tour 1", "detail": "1008",
            })
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            diag.print_matrix(rows, diag.MODEL)
        out = buffer.getvalue()
        self.assertIn("Ce n'est PAS une preuve que le", out)
        self.assertIn("gemini-3.8-live", out)
        self.assertIn("close code", out)


class NoFalseSuccessTests(unittest.TestCase):
    def test_dry_run_ne_consomme_aucun_appel_et_reussit(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = diag.main(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("aucun appel réseau", buffer.getvalue())

    def test_sans_cle_le_script_est_non_testable(self) -> None:
        buffer = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            os.environ.pop("GOOGLE_API_KEY", None)
            with contextlib.redirect_stdout(buffer):
                code = diag.main([])
        self.assertEqual(code, 2)
        self.assertIn("NON TESTABLE", buffer.getvalue())

    def test_le_modele_est_parametrable(self) -> None:
        """Permet de tester gemini-3.8-live sans modifier le code."""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            diag.main(["--dry-run", "--model", "gemini-3.8-live"])
        self.assertIn("gemini-3.8-live", buffer.getvalue())

    def test_la_cle_est_masquee(self) -> None:
        self.assertNotIn("AIza", diag.redact("erreur avec AIzaSyD1234567890abcdef"))
        self.assertIn("<clé masquée>", diag.redact("clé=AIzaSyD1234567890abcdef"))


if __name__ == "__main__":
    unittest.main()
