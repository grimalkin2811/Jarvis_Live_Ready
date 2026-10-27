"""Matrice discriminante 1008 : le plan doit être SOUND avant tout appel réel.

La matrice de ``scripts/diag_1008_matrix.py`` ne vaut que si les quatre cases
produisent réellement quatre configurations DIFFÉRENTES sur le câble. Sans
cette vérification, on lancerait des appels API réels en croyant comparer des
choses distinctes alors que deux cases seraient identiques.

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
            # La compression reste active : un seul levier à la fois.
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
        """Documente la suspicion : la cible de la fenêtre glissante est None.

        Ce test ne dit PAS que c'est la cause du 1008 — il fige le fait que
        c'est bien cette forme-là qui est envoyée aujourd'hui, pour que la
        matrice 1008 soit interprétable (case A = cette forme exacte).
        """
        server = self.make_server()
        await self.make_harness(server)
        window = getattr(server.last_config.context_window_compression, "sliding_window", None)
        self.assertIsNotNone(window)
        self.assertIsNone(
            getattr(window, "target_tokens", "absent"),
            "si target_tokens devient renseigné, la matrice doit être relue",
        )


class MatrixPlanTests(unittest.TestCase):
    """Le plan est factoriel complet : une seule variable par ligne."""

    def test_plan_factoriel_complet_2x2(self) -> None:
        combos = {(h, c) for _k, _l, h, c in diag.COMBOS}
        self.assertEqual(combos, {(True, True), (False, True), (True, False), (False, False)})

    def test_une_seule_variable_change_par_opposition(self) -> None:
        par_cle = {k: (h, c) for k, _l, h, c in diag.COMBOS}
        # A/B ne diffèrent que par history ; A/C que par compression.
        self.assertEqual(par_cle["A"][1], par_cle["B"][1])
        self.assertNotEqual(par_cle["A"][0], par_cle["B"][0])
        self.assertEqual(par_cle["A"][0], par_cle["C"][0])
        self.assertNotEqual(par_cle["A"][1], par_cle["C"][1])

    def test_dry_run_ne_consomme_aucun_appel_et_reussit(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = diag.main(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("aucun appel réseau", buffer.getvalue())

    def test_sans_cle_le_script_est_non_testable(self) -> None:
        """Jamais de faux succès : sortie 2 et mention explicite."""
        buffer = io.StringIO()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GEMINI_API_KEY", None)
            os.environ.pop("GOOGLE_API_KEY", None)
            with contextlib.redirect_stdout(buffer):
                code = diag.main([])
        self.assertEqual(code, 2)
        self.assertIn("NON TESTABLE", buffer.getvalue())

    def test_classification_1008_et_handle(self) -> None:
        class FakeApiError(Exception):
            code = 1008

        code, detail = diag.classify(FakeApiError("Requested entity was not found."),
                                     "reprise de session", True)
        self.assertEqual(code, "1008")
        self.assertIn("handle_reçu=True", detail)

    def test_la_cle_est_masquee(self) -> None:
        """Aucune clé ne doit jamais apparaître dans une trace."""
        self.assertNotIn("AIza", diag.redact("erreur avec AIzaSyD1234567890abcdef"))
        self.assertIn("<clé masquée>", diag.redact("clé=AIzaSyD1234567890abcdef"))


if __name__ == "__main__":
    unittest.main()
