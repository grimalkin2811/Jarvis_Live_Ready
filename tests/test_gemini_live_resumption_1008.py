"""Tests de régression pour la reprise de session et l'erreur 1008.

Couvre :
1. La détection des erreurs d'invalidation de handle (1007, 1008 Requested entity was not found).
2. Le repli automatique de connect() vers une session neuve avec rejeu de contexte
   lorsque le serveur refuse le handle de reprise avec une erreur 1008.
"""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest import mock

os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

from src import gemini_live as gl  # noqa: E402
from src.conversation import ConversationContext  # noqa: E402
from tests import sdk_live_fake as fake  # noqa: E402

MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"


class FakeApiError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"APIError: {code} {message} [code={code}]")
        self.code = code
        self.message = message


class InvalidHandleErrorDetectionTests(unittest.TestCase):
    def test_detects_1007_code(self):
        exc = FakeApiError(1007, "Invalid session handle")
        self.assertTrue(gl._is_invalid_handle_error(exc))

    def test_detects_1008_code(self):
        exc = FakeApiError(1008, "Requested entity was not found.")
        self.assertTrue(gl._is_invalid_handle_error(exc))

    def test_detects_requested_entity_not_found_string(self):
        exc = Exception("APIError: Requested entity was not found.")
        self.assertTrue(gl._is_invalid_handle_error(exc))

    def test_detects_invalid_session_handle_string(self):
        exc = Exception("Connection closed: Invalid session handle")
        self.assertTrue(gl._is_invalid_handle_error(exc))

    def test_ignores_unrelated_errors(self):
        self.assertFalse(gl._is_invalid_handle_error(Exception("Connection reset by peer")))
        self.assertFalse(gl._is_invalid_handle_error(Exception("TimeoutError")))
        self.assertFalse(gl._is_invalid_handle_error(FakeApiError(403, "Permission denied")))


class ResumptionCheckpointTests(unittest.TestCase):
    def test_finishing_a_turn_does_not_advance_an_earlier_handle_checkpoint(self):
        conversation = ConversationContext(max_turns=20)
        gemini = gl.GeminiLive(
            "fake-key", MODEL, "TestVoice",
            on_audio=lambda pcm: None,
            conversation=conversation,
        )
        gemini._turn_closed = False
        gemini._turn_user_text.append("Question après le checkpoint")
        gemini.resumption_handle = "handle-reçu-avant-le-commit"
        gemini._resumption_handle_message_count = 0
        gemini._resumption_handle_received_this_turn = True

        gemini._finish_turn()

        self.assertEqual(len(conversation.get_messages()), 1)
        self.assertEqual(gemini._resumption_handle_message_count, 0)
        self.assertFalse(gemini._resumption_handle_received_this_turn)


class SessionResumption1008FallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_stale_checkpoint_starts_a_fresh_session_before_resume(self):
        conversation = ConversationContext(max_turns=20)
        conversation.add_user_message("Question précédente")
        conversation.add_assistant_message("Réponse précédente")
        gemini = gl.GeminiLive(
            "fake-key", MODEL, "TestVoice",
            on_audio=lambda pcm: None,
            conversation=conversation,
        )
        gemini.resumption_handle = "handle-before-last-turn"
        gemini._resumption_handle_message_count = 1
        gemini._seed_context = mock.AsyncMock(return_value=True)

        session = fake.SdkLikeSession()
        configs = []

        class MockCtx:
            async def __aenter__(self):
                return session

            async def __aexit__(self, exc_type, exc, tb):
                pass

        def mock_connect(model, config):
            configs.append(config)
            return MockCtx()

        gemini.client = mock.MagicMock()
        gemini.client.aio.live.connect = mock.MagicMock(side_effect=mock_connect)

        await gemini.connect()

        self.assertEqual(len(configs), 1)
        self.assertIsNone(configs[0].session_resumption.handle)
        self.assertIsNone(gemini.resumption_handle)
        traces = [event["kind"] for event in gemini.trace_events()]
        self.assertIn("SESSION_RESUME_STALE_CHECKPOINT", traces)
        await gemini.close()

    async def test_rejected_resumed_session_is_closed_before_fresh_retry(self):
        gemini = gl.GeminiLive("fake-key", MODEL, "TestVoice", on_audio=lambda pcm: None)
        gemini.resumption_handle = "handle-that-expires-after-connect"
        closed: list[str] = []
        fresh_session = fake.SdkLikeSession()
        attempts = 0

        class MockCtx:
            def __init__(self, name, session):
                self.name = name
                self.session = session

            async def __aexit__(self, exc_type, exc, tb):
                closed.append(self.name)

        async def mock_connect_once():
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                gemini.session = fake.SdkLikeSession()
                gemini.ctx = MockCtx("rejected", gemini.session)
                raise FakeApiError(1008, "Requested entity was not found.")
            self.assertIsNone(gemini.session, "la session refusée doit être fermée avant la nouvelle")
            self.assertEqual(closed, ["rejected"])
            gemini.session = fresh_session
            gemini.ctx = MockCtx("fresh", fresh_session)

        gemini._connect_once = mock_connect_once
        await gemini.connect()

        self.assertEqual(attempts, 2)
        self.assertIs(gemini.session, fresh_session)
        self.assertIsNone(gemini.resumption_handle)
        await gemini.close()

    async def test_connect_falls_back_to_fresh_session_on_1008(self):
        conversation = ConversationContext(max_turns=20)
        conversation.add_user_message("Bonjour, je m'appelle Simon.")
        conversation.add_assistant_message("Bonjour Simon !")

        gemini = gl.GeminiLive(
            "fake-key", MODEL, "TestVoice",
            on_audio=lambda pcm: None,
            conversation=conversation,
        )

        attempts = 0
        fresh_session = fake.SdkLikeSession()

        class MockCtx:
            def __init__(self, session_to_return, raises=None):
                self._session = session_to_return
                self._raises = raises

            async def __aenter__(self):
                if self._raises:
                    raise self._raises
                return self._session

            async def __aexit__(self, exc_type, exc, tb):
                pass

        def mock_connect(model, config):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                # Première tentative : handle fourni -> serveur répond 1008 Requested entity was not found
                raise FakeApiError(1008, "Requested entity was not found.")
            # Deuxième tentative : session neuve réussie
            return MockCtx(fresh_session)

        gemini.client = mock.MagicMock()
        gemini.client.aio.live.connect = mock.MagicMock(side_effect=mock_connect)
        gemini.resumption_handle = "stale-handle-from-previous-session"
        gemini._resumption_handle_message_count = 2

        # connect() doit absorber l'erreur 1008, vider le handle, et réussir sur la 2e tentative
        await gemini.connect()

        self.assertEqual(attempts, 2)
        self.assertIsNone(gemini.resumption_handle)
        self.assertIsNotNone(gemini.session)

        # Vérifier que SESSION_RESUME_REJECTED a été tracé
        traces = [e["kind"] for e in gemini.trace_events()]
        self.assertIn("SESSION_RESUME_REJECTED", traces)

        await gemini.close()


if __name__ == "__main__":
    unittest.main()
