"""Budget de récupération de session (v1.9.x) — reproduction du run 40370419959.

Défaut reproduit : une rafale « 1008 Requested entity was not found » a fait
monter le compteur de sessions jusqu'à g45 — chaque session mourant avant
d'avoir servi UN tour, reconnexifiée en boucle par le superviseur sans borne,
avec la raison par défaut ``unexpected_after_normal_turn``. Ces tests
verrouillent :

1. fermeture normale après ``turn_complete`` : raison explicite, budget zéro ;
2. 1008 à la reprise : handle refusé puis JAMAIS réutilisé ;
3. nouvelle session propre : le rejeu part exactement UNE fois ;
4. échec définitif après le budget (SessionRecoveryExhausted) ;
5. nombre borné de connexions (pas de boucle infinie) ;
6. erreur réseau transitoire récupérable (tour servi → budget remis à zéro) ;
7. erreur non récupérable (AuthError) : ni retry ni épuisement ;
8. keeper du validateur : arrêt explicite, tâche terminée (pas d'orphelin).
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from google.genai import errors as genai_errors  # noqa: E402

from src import gemini_live as gl  # noqa: E402
from src.conversation import ConversationContext  # noqa: E402
from tests import sdk_live_fake as fake  # noqa: E402

MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"


class _DyingSession(fake.SdkLikeSession):
    """Session serveur qui tue la connexion avant le commit du rejeu.

    C'est le pattern exact du run 40370419959 : ``SESSION_CONNECT`` tracé,
    puis le WebSocket meurt (1008 « Requested entity was not found »)
    pendant l'attente de la confirmation du rejeu — chaque session meurt
    avant d'avoir servi UN tour utilisateur.
    """

    async def receive(self):
        raise genai_errors.ClientError(
            1008, {"error": {"message": "Requested entity was not found."}}
        )
        yield  # pragma: no cover - rend le générateur async


class FakeApiError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"APIError: {code} {message} [code={code}]")
        self.code = code


class _OpeningCase(unittest.IsolatedAsyncioTestCase):
    """GeminiLive réel, ouvertures de session simulées (aucun réseau)."""

    def setUp(self) -> None:
        self.seed_patch = mock.patch.object(gl, "SEED_COMMIT_TIMEOUT_SECONDS", 0.05)
        self.seed_patch.start()
        self.addCleanup(self.seed_patch.stop)

    def _make_gemini(self) -> gl.GeminiLive:
        conversation = ConversationContext(max_turns=20, max_tokens=8192)
        # Du contexte à rejouer : le run réel n'ouvre JAMAIS une session
        # vide (le rejeu de contexte est ce que le serveur tue).
        conversation.add_user_message("Bonjour Jarvis.")
        conversation.add_assistant_message("Bonjour !")
        gemini = gl.GeminiLive(
            "cle-de-test", MODEL, "Test",
            on_audio=lambda pcm: None,
            conversation=conversation,
        )
        self.addAsyncCleanup(gemini.close)
        return gemini

    @staticmethod
    def _dead_session() -> "_DyingSession":
        """Session qui tue la connexion avant le commit du rejeu."""
        return _DyingSession()

    def mock_openings(self, gemini: gl.GeminiLive, sessions: list[fake.SdkLikeSession],
                      first_rejects_handle: bool = False) -> list[dict]:
        """Programme N ouvertures ; retourne la config reçue à chacune."""
        configs: list[dict] = []
        index = 0

        class MockCtx:
            def __init__(self, session_obj):
                self._session = session_obj

            async def __aenter__(self):
                return self._session

            async def __aexit__(self, exc_type, exc, tb):
                pass

        def _capture(model, config):
            nonlocal index
            configs.append({
                "handle": getattr(getattr(config, "session_resumption", None),
                                  "handle", None),
            })
            if index == 0 and first_rejects_handle:
                index += 1
                raise FakeApiError(1008, "Requested entity was not found.")
            session_obj = sessions[min(index, len(sessions) - 1)]
            index += 1
            return MockCtx(session_obj)

        gemini.client = mock.MagicMock()
        gemini.client.aio.live.connect = mock.MagicMock(side_effect=_capture)
        return configs

    async def _open_and_die(self, gemini: gl.GeminiLive) -> None:
        """Une ouverture complète dont la session meurt avant tout tour.

        Comme le keeper de production : l'exception (1008 pendant le rejeu
        de contexte) remonte de ``connect()`` et est absorbée par le
        superviseur, qui relance — jusqu'à épuisement du budget.
        """
        import contextlib

        with contextlib.suppress(Exception):
            await gemini.connect()
            # Si l'ouverture a survécu (pas de rejeu à faire), la boucle de
            # réception découvre la mort de la session, comme le keeper.
            with contextlib.suppress(Exception):
                await asyncio.wait_for(gemini.receive_loop(), 2)


class OpeningExhaustionTests(_OpeningCase):
    async def test_echec_definitif_apres_le_budget_et_connexions_bornees(self):
        """5 ouvertures mortes consécutives → arrêt explicite, 6e refusée."""
        gemini = self._make_gemini()
        # Une seule session morte réutilisée : chaque ouverture meurt avant
        # tout tour (c'est la rafale du run 40370419959).
        self.mock_openings(gemini, [self._dead_session()])

        for _ in range(gemini._recovery_max_failures):
            await self._open_and_die(gemini)

        self.assertTrue(gemini.session_recovery_exhausted)
        kinds = [e["kind"] for e in gemini.trace_events()]
        self.assertEqual(kinds.count("SESSION_RECOVERY_FAILURE"),
                         gemini._recovery_max_failures)
        self.assertEqual(kinds.count("SESSION_RECOVERY_EXHAUSTED"), 1)

        # Le budget épuisé : connect() lève IMMÉDIATEMENT (aucune ouverture
        # supplémentaire, aucune boucle infinie).
        attempts_before = gemini.client.aio.live.connect.call_count
        with self.assertRaises(gl.SessionRecoveryExhausted):
            await gemini.connect()
        self.assertEqual(
            gemini.client.aio.live.connect.call_count, attempts_before,
            "aucune ouverture ne doit être tentée après l'épuisement",
        )
        # La levée explicitement utile au superviseur (message porteur).
        with self.assertRaisesRegex(gl.SessionRecoveryExhausted, "épuisée"):
            await gemini.connect()

    async def test_backoff_croissant_borne_puis_remise_a_zero(self):
        gemini = self._make_gemini()
        self.mock_openings(gemini, [self._dead_session()])
        self.assertEqual(gemini.session_recovery_backoff_seconds(), 0.0)

        seen = []
        for expected in (0.5, 1.0, 2.0, 4.0):
            await self._open_and_die(gemini)
            seen.append(gemini.session_recovery_backoff_seconds())
        self.assertEqual(seen, [0.5, 1.0, 2.0, 4.0])
        self.assertLessEqual(
            gemini.session_recovery_backoff_seconds(),
            gemini._recovery_max_backoff,
            "le backoff doit rester borné",
        )

        # Une session qui sert un tour complet remet le budget à zéro.
        fresh = fake.SdkLikeSession()
        self.mock_openings(gemini, [fresh])
        gemini.session_recovery_exhausted = False  # comme un reset opérateur
        gemini.reset_session_recovery()
        await gemini.connect()
        gemini._finish_turn()  # tour complet servi sur CETTE session
        self.assertEqual(gemini._recovery_failures, 0)
        self.assertEqual(gemini.session_recovery_backoff_seconds(), 0.0)
        self.assertFalse(gemini.session_recovery_exhausted)

    async def test_1008_a_la_reprise_handle_jamais_reutilise_et_rejeu_unique(self):
        conversation = ConversationContext(max_turns=20, max_tokens=8192)
        conversation.add_user_message("Mon prénom est Simon.")
        conversation.add_assistant_message("Bien noté, Simon.")
        gemini = gl.GeminiLive(
            "cle-de-test", MODEL, "Test",
            on_audio=lambda pcm: None,
            conversation=conversation,
        )
        self.addAsyncCleanup(gemini.close)
        fresh = fake.SdkLikeSession()
        configs = self.mock_openings(
            gemini, [fresh], first_rejects_handle=True
        )
        gemini.resumption_handle = "handle-mourant"
        gemini._resumption_handle_message_count = 2

        await gemini.connect()

        # Le handle a été refusé puis INVALIDÉ : la deuxième ouverture (et
        # toute suivante) part sans handle — jamais de reprise réessayée.
        self.assertIsNone(gemini.resumption_handle)
        self.assertEqual(len(configs), 2)
        self.assertEqual(configs[0]["handle"], "handle-mourant")
        self.assertIsNone(
            configs[1]["handle"],
            "le handle refusé ne doit pas être réutilisé",
        )
        self.assertIn("SESSION_RESUME_REJECTED",
                      [e["kind"] for e in gemini.trace_events()])
        # Rejeu contrôlé : exactement UN clientContent sur la session neuve
        # (pas de double envoi des messages déjà traités).
        seed_sends = [entry for entry in fresh.sent if entry[0] == "client_content"]
        self.assertEqual(len(seed_sends), 1)


class CleanCloseReasonTests(_OpeningCase):
    async def test_fermeture_normale_apres_tour_raison_explicite_budget_zero(self):
        """Un tour complet servi puis fermeture propre : raison explicite,
        budget intact, pas de « unexpected_after_normal_turn »."""
        gemini = self._make_gemini()
        session = fake.SdkLikeSession()
        gemini.session = session
        gemini._loop = asyncio.get_running_loop()
        task = asyncio.create_task(gemini.receive_loop())
        for _ in range(50):
            await asyncio.sleep(0.005)
            if gemini._turn_closed and gemini._current_session_served_a_turn:
                break
        session.push(fake.user("Bonjour Jarvis."))
        session.push(*fake.answer("Bonjour !"))
        for _ in range(50):
            await asyncio.sleep(0.005)
            if gemini._turn_closed:
                break
        session.close()
        await asyncio.wait_for(task, 5)

        self.assertEqual(
            gemini._reconnect_reason, "session_closed_cleanly",
            "une fermeture propre doit porter une raison explicite",
        )
        self.assertEqual(gemini._recovery_failures, 0,
                         "une session qui a servi un tour n'épuise rien")
        self.assertFalse(gemini.session_recovery_exhausted)

    async def test_raison_legitime_goaway_nest_pas_ecrasee(self):
        gemini = self._make_gemini()
        session = fake.SdkLikeSession()
        gemini.session = session
        gemini._loop = asyncio.get_running_loop()
        gemini.reconnect_requested = True   # GoAway déjà traité en interne
        gemini._reconnect_reason = "goaway"
        task = asyncio.create_task(gemini.receive_loop())
        await asyncio.wait_for(task, 5)
        self.assertEqual(gemini._reconnect_reason, "goaway",
                         "la raison légitime doit survivre à la sortie propre")

    async def test_erreur_reseau_transitoire_compte_un_seul_echec(self):
        """Session morte sans tour servi = UN échec compté, récupérable."""
        gemini = self._make_gemini()
        self.mock_openings(gemini, [self._dead_session()])
        await self._open_and_die(gemini)
        self.assertEqual(gemini._recovery_failures, 1)
        self.assertFalse(gemini.session_recovery_exhausted)
        self.assertGreater(gemini.session_recovery_backoff_seconds(), 0.0)

    async def test_auth_error_ni_retry_ni_epuisement(self):
        gemini = self._make_gemini()
        gemini.client = mock.MagicMock()

        def _raise_auth(model, config):
            raise gl.AuthError("Clé Gemini API refusée")

        gemini.client.aio.live.connect = mock.MagicMock(side_effect=_raise_auth)
        with self.assertRaises(gl.AuthError):
            await gemini.connect()
        self.assertEqual(gemini._recovery_failures, 0,
                         "une erreur d'authentification n'épuise pas le budget")
        self.assertEqual(gemini.client.aio.live.connect.call_count, 1,
                         "aucun retry sur une erreur non récupérable")


class ValidationRunnerStopTests(unittest.IsolatedAsyncioTestCase):
    async def test_keeper_s_arrete_proprement_sur_budget_epuise(self):
        """Le superviseur du validateur : arrêt explicite, tâche terminée."""
        import scripts.validate_real_gemini as vrg

        runner = vrg.RealRunner.__new__(vrg.RealRunner)
        # Construction minimale : on ne veut ni client réseau, ni micro.
        runner.errors = []
        runner._stop = asyncio.Event()
        runner._boot_failed = asyncio.Event()
        runner._connected = asyncio.Event()
        runner._task = None
        runner._mic_task = None
        runner.gemini = mock.MagicMock()
        runner.gemini.reconnect_requested = False
        runner.gemini.session_recovery_backoff_seconds.return_value = 0.0
        runner.gemini.close = mock.AsyncMock()
        runner.gemini._shutdown_session = mock.AsyncMock()
        runner.gemini.session_recovery_exhausted = False

        async def _exhausted():
            raise gl.SessionRecoveryExhausted(
                "récupération de session épuisée : 5 ouvertures sans tour"
            )

        runner.gemini.connect = _exhausted

        with self.assertRaisesRegex(RuntimeError, "épuisée"):
            await runner.start()

        self.assertTrue(runner._boot_failed.is_set())
        self.assertFalse(runner._connected.is_set(),
                         "aucune session utilisable ne doit être annoncée")
        self.assertIn("épuisée", runner.errors[-1])

        # Arrêt sans tâche orpheline.
        await runner.stop()
        self.assertTrue(runner._task.done() or runner._task is None)
        runner.gemini.close.assert_awaited()


if __name__ == "__main__":
    unittest.main()
