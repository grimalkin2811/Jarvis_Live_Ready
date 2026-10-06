"""Intégration du contexte conversationnel dans la boucle vocale (v1.6.0).

Ces tests font tourner ``GeminiLive`` avec une fausse session Live (aucun
réseau) et vérifient le cycle complet :

* les transcriptions entrée/sortie alimentent le contexte dans le bon ordre ;
* un appel d'outil est enregistré entre la demande et la réponse ;
* le contexte est rejoué dans une session neuve, mais pas quand le serveur
  reprend lui-même la session ;
* « nouvelle conversation » est traitée localement (aucun appel LLM) ;
* un échec de session laisse un état déterministe ;
* la mémoire persistante et les changements de mode ne sont pas affectés.
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import tools  # noqa: E402
from src.conversation import (  # noqa: E402
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    ConversationContext,
    set_default_conversation_context,
)
from src.gemini_live import GeminiLive  # noqa: E402
from src.memory import MemoryManager, set_default_memory_manager  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Faux SDK Gemini Live
# ---------------------------------------------------------------------------


class _Transcript:
    def __init__(self, text: str) -> None:
        self.text = text


class _Part:
    def __init__(self, data: bytes | None) -> None:
        self.inline_data = type("_Inline", (), {"data": data})() if data else None


class _ModelTurn:
    def __init__(self, audio: bytes) -> None:
        self.parts = [_Part(audio)]


class _ServerContent:
    def __init__(
        self,
        user_text: str | None = None,
        model_text: str | None = None,
        audio: bytes | None = None,
        interrupted: bool = False,
        turn_complete: bool = False,
    ) -> None:
        self.input_transcription = _Transcript(user_text) if user_text else None
        self.output_transcription = _Transcript(model_text) if model_text else None
        self.model_turn = _ModelTurn(audio) if audio else None
        self.interrupted = interrupted
        self.turn_complete = turn_complete


class _Call:
    def __init__(self, name: str, args: dict | None = None, cid: str = "1") -> None:
        self.name = name
        self.args = args or {}
        self.id = cid


class _ToolCall:
    def __init__(self, calls: list[_Call]) -> None:
        self.function_calls = calls


class _Message:
    def __init__(self, server_content=None, tool_call=None) -> None:
        self.server_content = server_content
        self.tool_call = tool_call
        self.session_resumption_update = None


def _user(text: str) -> _Message:
    return _Message(_ServerContent(user_text=text))


def _model(text: str) -> _Message:
    return _Message(_ServerContent(model_text=text))


def _audio(data: bytes = b"\x00\x01") -> _Message:
    return _Message(_ServerContent(audio=data))


def _end() -> _Message:
    return _Message(_ServerContent(turn_complete=True))


def _interrupted() -> _Message:
    return _Message(_ServerContent(interrupted=True))


def _tool(name: str, args: dict | None = None) -> _Message:
    return _Message(tool_call=_ToolCall([_Call(name, args)]))


class _FakeSession:
    def __init__(self, messages=()) -> None:
        self.messages = list(messages)
        self.client_content = []
        self.tool_responses = []
        self.audio_sent = []

    async def send_client_content(self, turns=None, turn_complete=True):
        self.client_content.append({"turns": turns, "turn_complete": turn_complete})

    async def send_tool_response(self, function_responses):
        self.tool_responses.append(function_responses)

    async def send_realtime_input(self, audio=None):
        self.audio_sent.append(audio)

    def receive(self):
        """Fidélité au protocole réel (v1.7.4) : chaque appel à ``receive()``
        consomme les messages restants (pas de rejeu) et se termine
        naturellement à un ``turn_complete``/``interrupted`` — comme le SDK
        réel (confirmé par Google, googleapis/python-genai#1224 : « the
        receive() method throws you out of the loop if turn is complete »).
        Depuis le correctif reconnexion-par-tour, ``GeminiLive`` rappelle
        ``receive()`` sur la MÊME session pour le tour suivant : un ancien
        générateur qui rejouait toute la liste à chaque appel bouclerait
        indéfiniment.
        """

        async def gen():
            while self.messages:
                message = self.messages.pop(0)
                yield message
                server_content = getattr(message, "server_content", None)
                if server_content is not None and (
                    getattr(server_content, "turn_complete", False)
                    or getattr(server_content, "interrupted", False)
                ):
                    return

        return gen()


class _FakeCtx:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session
        self.exited = False

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *args):
        self.exited = True
        return False


class _FakeLive:
    def __init__(self, ctx: _FakeCtx) -> None:
        self.ctx = ctx
        self.captured: dict = {}

    def connect(self, model, config):
        self.captured["model"] = model
        self.captured["config"] = config
        return self.ctx


class _FakeClient:
    def __init__(self, live: _FakeLive) -> None:
        self.aio = type("_Aio", (), {"live": live})()


# ---------------------------------------------------------------------------
# Socle commun
# ---------------------------------------------------------------------------


class _PipelineCase(unittest.TestCase):
    """Base : un contexte dédié par test, jamais l'instance globale."""

    def setUp(self) -> None:
        self.context = ConversationContext(max_turns=12, max_tokens=4000)
        self.audio_out: list[bytes] = []
        self.notifications: list[str] = []
        def _capture(message, title="Jarvis"):
            self.notifications.append(message)
            return {"success": True}

        for target in ("src.routine_actions.notify_user", "src.tools.notify_user"):
            patcher = mock.patch(target, side_effect=_capture)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(set_default_conversation_context, None)

    def make_gemini(self, session: _FakeSession | None = None, **kwargs) -> GeminiLive:
        gemini = GeminiLive(
            key="test-key",
            model="test-model",
            user="Test",
            on_audio=self.audio_out.append,
            conversation=self.context,
            **kwargs,
        )
        if session is not None:
            gemini.session = session
        return gemini

    def run_loop(self, gemini: GeminiLive) -> None:
        asyncio.run(gemini.receive_loop())

    def texts(self):
        return [(m.role, m.text) for m in self.context.get_messages()]

    def roles(self):
        return [m.role for m in self.context.get_messages()]


# ---------------------------------------------------------------------------
# Connexion : transcriptions, rejeu, liaison
# ---------------------------------------------------------------------------


class ConnectWiringTests(_PipelineCase):
    def _connect(self, gemini: GeminiLive, live: _FakeLive) -> None:
        gemini.client = _FakeClient(live)
        asyncio.run(gemini.connect())

    def test_les_transcriptions_sont_activees(self) -> None:
        session = _FakeSession()
        live = _FakeLive(_FakeCtx(session))
        self._connect(self.make_gemini(), live)
        config = live.captured["config"]
        self.assertIsNotNone(getattr(config, "input_audio_transcription", None))
        self.assertIsNotNone(getattr(config, "output_audio_transcription", None))

    def test_connexion_declare_le_provider_gemini(self) -> None:
        self.context.set_provider("ollama")
        session = _FakeSession()
        self._connect(self.make_gemini(), _FakeLive(_FakeCtx(session)))
        self.assertEqual(self.context.provider, "gemini")

    def test_rejeu_du_contexte_dans_une_session_neuve(self) -> None:
        self.context.add_user_message("Quelle est la capitale de l'Italie ?")
        self.context.add_assistant_message("Rome.")
        session = _FakeSession()
        self._connect(self.make_gemini(), _FakeLive(_FakeCtx(session)))
        self.assertEqual(len(session.client_content), 1)
        replay = session.client_content[0]
        # Protocole v1.7.1 : le rejeu est CLOTURÉ (turn_complete=True) — un
        # clientContent laissé en attente n'est pas rappelé par le tour audio
        # suivant sur les modèles audio 2.x (régression corrigée).
        self.assertTrue(replay["turn_complete"])
        # Le seed se termine par un tour USER (exigence des modèles 2.x) :
        # l'historique se termine par une réponse assistant, un tour user vide
        # est donc ajouté.
        self.assertEqual(
            [turn["role"] for turn in replay["turns"]], ["user", "model", "user"]
        )
        self.assertIn("Italie", replay["turns"][0]["parts"][0]["text"])
        self.assertIn("Rome", replay["turns"][1]["parts"][0]["text"])
        self.assertEqual(replay["turns"][2]["parts"][0]["text"], " ")

    def test_rejeu_conserve_un_historique_fini_par_l_utilisateur(self) -> None:
        # Tour resté sans réponse (échec de session) : le seed se termine
        # déjà par un tour user, aucun tour vide n'est ajouté.
        self.context.add_user_message("Mon prénom est Simon.")
        self.context.add_assistant_message("Enchanté.")
        self.context.add_user_message("et ma couleur préférée ?")
        self.context.fail_open_turn("coupure")
        session = _FakeSession()
        self._connect(self.make_gemini(), _FakeLive(_FakeCtx(session)))
        replay = session.client_content[0]
        self.assertTrue(replay["turn_complete"])
        self.assertEqual(
            [turn["role"] for turn in replay["turns"]], ["user", "model", "user"]
        )
        self.assertIn("couleur", replay["turns"][2]["parts"][0]["text"])

    def test_pas_de_rejeu_quand_le_serveur_reprend_la_session(self) -> None:
        self.context.add_user_message("Quelle est la capitale de l'Italie ?")
        session = _FakeSession()
        gemini = self.make_gemini()
        gemini.resumption_handle = "handle-123"
        self._connect(gemini, _FakeLive(_FakeCtx(session)))
        self.assertEqual(session.client_content, [])

    def test_pas_de_rejeu_quand_le_contexte_est_vide(self) -> None:
        session = _FakeSession()
        self._connect(self.make_gemini(), _FakeLive(_FakeCtx(session)))
        self.assertEqual(session.client_content, [])

    def test_un_rejeu_en_echec_ne_casse_pas_la_connexion(self) -> None:
        self.context.add_user_message("Bonjour")

        class _BrokenSession(_FakeSession):
            async def send_client_content(self, turns=None, turn_complete=True):
                raise RuntimeError("websocket fermé")

        session = _BrokenSession()
        gemini = self.make_gemini()
        with self.assertLogs("jarvis.gemini", level="WARNING"):
            self._connect(gemini, _FakeLive(_FakeCtx(session)))
        self.assertIsNotNone(gemini.session)
        self.assertEqual(len(self.context.get_messages()), 1)

    def test_close_delie_le_gestionnaire_de_reset(self) -> None:
        session = _FakeSession()
        ctx = _FakeCtx(session)
        gemini = self.make_gemini()

        async def scenario():
            gemini.client = _FakeClient(_FakeLive(ctx))
            await gemini.connect()
            await gemini.close()

        asyncio.run(scenario())
        gemini.session = session  # la session a été fermée, on simule un résidu
        gemini.reconnect_requested = False
        self.context.start_new_conversation(reason="test")
        self.assertFalse(gemini.reconnect_requested)


# ---------------------------------------------------------------------------
# Transcription -> contexte
# ---------------------------------------------------------------------------


class TranscriptionToContextTests(_PipelineCase):
    def test_un_tour_simple_est_enregistre(self) -> None:
        session = _FakeSession([
            _user("Quelle est la capitale de l'Italie ?"),
            _model("La capitale de l'Italie est Rome."),
            _audio(),
            _end(),
        ])
        self.run_loop(self.make_gemini(session))
        self.assertEqual(
            self.texts(),
            [
                (ROLE_USER, "Quelle est la capitale de l'Italie ?"),
                (ROLE_ASSISTANT, "La capitale de l'Italie est Rome."),
            ],
        )
        self.assertEqual(self.context.turn_count(), 1)

    def test_transcription_fragmentee_forme_un_seul_message(self) -> None:
        session = _FakeSession([
            _user("Quelle est"),
            _user(" la capitale"),
            _user(" de l'Italie ?"),
            _model("Rome."),
            _end(),
        ])
        self.run_loop(self.make_gemini(session))
        messages = self.context.get_messages()
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].role, ROLE_USER)
        self.assertIn("Quelle est", messages[0].text)
        self.assertIn("capitale", messages[0].text)
        self.assertIn("Italie", messages[0].text)

    def test_deux_tours_permettent_une_reference(self) -> None:
        session = _FakeSession([
            _user("Quelle est la capitale de l'Italie ?"),
            _model("Rome."),
            _end(),
            _user("Et sa population ?"),
            _model("Environ 2,8 millions d'habitants."),
            _end(),
        ])
        self.run_loop(self.make_gemini(session))
        self.assertEqual(self.context.turn_count(), 2)
        self.assertEqual(
            self.roles(), [ROLE_USER, ROLE_ASSISTANT, ROLE_USER, ROLE_ASSISTANT]
        )
        # Le deuxième tour reste résoluble : « sa » renvoie à Rome, présent
        # dans le contexte envoyé au fournisseur.
        contents = self.context.messages_for_provider("gemini")
        joined = " ".join(
            part["text"] for content in contents for part in content["parts"]
        )
        self.assertIn("Rome", joined)
        self.assertIn("Et sa population ?", joined)

    def test_audio_sans_transcription_de_sortie_conserve_la_demande(self) -> None:
        session = _FakeSession([_user("Monte le son"), _audio(), _end()])
        self.run_loop(self.make_gemini(session))
        self.assertEqual(self.texts(), [(ROLE_USER, "Monte le son")])
        self.assertFalse(self.context.get_messages()[0].failed)

    def test_interruption_conserve_le_debut_de_reponse(self) -> None:
        session = _FakeSession([
            _user("Raconte-moi l'histoire de Rome"),
            _model("Rome a été fondée en 753"),
            _interrupted(),
            _end(),
        ])
        self.run_loop(self.make_gemini(session))
        self.assertEqual(
            self.texts(),
            [
                (ROLE_USER, "Raconte-moi l'histoire de Rome"),
                (ROLE_ASSISTANT, "Rome a été fondée en 753"),
            ],
        )

    def test_un_tour_sans_parole_utilisateur_ne_cree_rien(self) -> None:
        session = _FakeSession([_audio(), _end()])
        self.run_loop(self.make_gemini(session))
        self.assertTrue(self.context.is_empty())


# ---------------------------------------------------------------------------
# Outils dans le contexte
# ---------------------------------------------------------------------------


class ToolContextTests(_PipelineCase):
    def setUp(self) -> None:
        super().setUp()
        from src import gemini_live as gl

        self.original_tools = dict(gl.TOOL_FUNCTIONS)
        self.addCleanup(self._restore_tools)
        self.gl = gl

    def _restore_tools(self) -> None:
        self.gl.TOOL_FUNCTIONS.clear()
        self.gl.TOOL_FUNCTIONS.update(self.original_tools)

    def test_ordre_demande_outil_reponse(self) -> None:
        self.gl.TOOL_FUNCTIONS["fake_search"] = lambda **kw: {
            "success": True,
            "message": "2 résultats",
            "results": {"tracks": [{"title": "One More Time", "artist": "Daft Punk"}]},
        }
        session = _FakeSession([
            _user("Cherche Daft Punk"),
            _tool("fake_search", {"query": "Daft Punk"}),
            _model("J'ai trouvé deux titres."),
            _end(),
        ])
        self.run_loop(self.make_gemini(session))
        self.assertEqual(
            self.roles(), [ROLE_USER, ROLE_TOOL, ROLE_TOOL, ROLE_ASSISTANT]
        )
        messages = self.context.get_messages()
        self.assertIn("fake_search", messages[1].text)
        self.assertIn("Daft Punk", messages[1].text)
        self.assertIn("One More Time", messages[2].text)

    def test_le_resultat_est_compact(self) -> None:
        self.gl.TOOL_FUNCTIONS["fake_big"] = lambda **kw: {
            "success": True,
            "message": "ok",
            "results": {
                "tracks": [
                    {"title": f"Titre {i}", "artist": "X", "payload": "z" * 400}
                    for i in range(40)
                ]
            },
        }
        session = _FakeSession([
            _user("Cherche tout"),
            _tool("fake_big", {"query": "tout"}),
            _end(),
        ])
        self.run_loop(self.make_gemini(session))
        result = self.context.get_messages()[2]
        self.assertLess(len(result.text), 600)
        self.assertNotIn("zzzzzzzzzz", result.text)

    def test_echec_d_outil_reste_dans_le_contexte(self) -> None:
        def _boom(**kwargs):
            raise RuntimeError("service indisponible")

        self.gl.TOOL_FUNCTIONS["fake_fail"] = _boom
        session = _FakeSession([
            _user("Lance la musique"),
            _tool("fake_fail", {}),
            _model("Je n'ai pas réussi."),
            _end(),
        ])
        self.run_loop(self.make_gemini(session))
        messages = self.context.get_messages()
        self.assertEqual(len(messages), 4)
        self.assertIn("service indisponible", messages[2].text)
        self.assertIn("échec", messages[2].text.lower())
        self.assertEqual(messages[3].role, ROLE_ASSISTANT)
        self.assertFalse(self.context.has_open_turn())

    def test_outil_inconnu_est_trace(self) -> None:
        session = _FakeSession([
            _user("Fais un truc"),
            _tool("outil_inexistant", {}),
            _end(),
        ])
        self.run_loop(self.make_gemini(session))
        self.assertIn("Outil inconnu", self.context.get_messages()[2].text)


# ---------------------------------------------------------------------------
# Réinitialisation
# ---------------------------------------------------------------------------


class ResetTests(_PipelineCase):
    def test_commande_vocale_reinitialise_sans_appel_llm(self) -> None:
        self.context.add_user_message("Question précédente")
        self.context.add_assistant_message("Réponse précédente")
        first_id = self.context.conversation_id

        session = _FakeSession([_user("Nouvelle conversation")])
        gemini = self.make_gemini(session)
        gemini.conversation.bind_session(gemini._handle_context_reset)
        self.run_loop(gemini)

        self.assertTrue(self.context.is_empty())
        self.assertNotEqual(self.context.conversation_id, first_id)
        self.assertEqual(session.client_content, [])  # aucun tour renvoyé au modèle
        self.assertEqual(session.tool_responses, [])  # aucun outil exécuté
        self.assertTrue(gemini.reconnect_requested)
        self.assertTrue(
            any("réinitialisée" in message.lower() for message in self.notifications)
        )

    def test_la_commande_vocale_n_est_pas_enregistree_comme_un_tour(self) -> None:
        session = _FakeSession([_user("efface le contexte"), _end()])
        gemini = self.make_gemini(session)
        gemini.conversation.bind_session(gemini._handle_context_reset)
        self.run_loop(gemini)
        self.assertTrue(self.context.is_empty())
        self.assertEqual(gemini._turn_user_text, [])

    def test_une_phrase_proche_ne_declenche_pas_de_reset(self) -> None:
        session = _FakeSession([
            _user("Reprenons la conversation sur Rome"),
            _model("Avec plaisir."),
            _end(),
        ])
        gemini = self.make_gemini(session)
        gemini.conversation.bind_session(gemini._handle_context_reset)
        self.run_loop(gemini)
        self.assertEqual(self.context.turn_count(), 1)
        self.assertFalse(gemini.reconnect_requested)

    def test_outil_reset_conversation(self) -> None:
        set_default_conversation_context(self.context)
        self.context.add_user_message("Question")
        self.context.add_assistant_message("Réponse")
        result = tools.TOOL_FUNCTIONS["reset_conversation"]()
        self.assertTrue(result["success"])
        self.assertEqual(result["tours_effaces"], 1)
        self.assertTrue(self.context.is_empty())
        self.assertEqual(result["conversation_id"], self.context.conversation_id)

    def test_outil_get_conversation_state(self) -> None:
        set_default_conversation_context(self.context)
        self.context.add_user_message("Question")
        state = tools.TOOL_FUNCTIONS["get_conversation_state"]()
        self.assertTrue(state["success"])
        self.assertEqual(state["conversation_id"], self.context.conversation_id)
        self.assertEqual(state["messages"], 1)
        # Diagnostic uniquement : aucun contenu d'échange n'est exposé.
        self.assertNotIn("Question", str(state))

    def test_reset_pendant_une_session_demande_une_reconnexion(self) -> None:
        session = _FakeSession()
        gemini = self.make_gemini(session)
        gemini.resumption_handle = "handle-abc"
        gemini.conversation.bind_session(gemini._handle_context_reset)
        gemini._turn_user_text.append("bla")
        self.context.start_new_conversation(reason="test")
        self.assertTrue(gemini.reconnect_requested)
        self.assertIsNone(gemini.resumption_handle)
        self.assertEqual(gemini._turn_user_text, [])

    def test_reset_hors_session_ne_demande_pas_de_reconnexion(self) -> None:
        gemini = self.make_gemini()
        gemini.conversation.bind_session(gemini._handle_context_reset)
        self.context.start_new_conversation(reason="test")
        self.assertFalse(gemini.reconnect_requested)


# ---------------------------------------------------------------------------
# Comportement déterministe en cas d'échec
# ---------------------------------------------------------------------------


class FailureTests(_PipelineCase):
    def test_erreur_de_session_marque_le_tour_en_echec(self) -> None:
        class _BrokenSession(_FakeSession):
            def receive(self):
                async def gen():
                    yield _user("Quelle heure est-il ?")
                    raise RuntimeError("websocket coupé")

                return gen()

        gemini = self.make_gemini(_BrokenSession())
        with self.assertRaises(RuntimeError):
            self.run_loop(gemini)
        messages = self.context.get_messages()
        # Comportement retenu : la demande est CONSERVÉE (« reprends » doit
        # marcher après une coupure) mais explicitement marquée en échec.
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].text, "Quelle heure est-il ?")
        self.assertTrue(messages[0].failed)
        self.assertFalse(self.context.has_open_turn())

    def test_fin_de_session_avant_reponse_marque_le_tour(self) -> None:
        session = _FakeSession([_user("Quelle heure est-il ?")])
        self.run_loop(self.make_gemini(session))
        messages = self.context.get_messages()
        self.assertEqual(len(messages), 1)
        self.assertTrue(messages[0].failed)
        self.assertFalse(self.context.has_open_turn())

    def test_reponse_partielle_conservee_si_la_session_tombe(self) -> None:
        class _BrokenSession(_FakeSession):
            def receive(self):
                async def gen():
                    yield _user("Raconte-moi une histoire")
                    yield _model("Il était une fois")
                    raise RuntimeError("websocket coupé")

                return gen()

        gemini = self.make_gemini(_BrokenSession())
        with self.assertRaises(RuntimeError):
            self.run_loop(gemini)
        self.assertEqual(
            self.texts(),
            [
                (ROLE_USER, "Raconte-moi une histoire"),
                (ROLE_ASSISTANT, "Il était une fois"),
            ],
        )

    def test_le_tour_suivant_repart_proprement(self) -> None:
        session = _FakeSession([_user("Première question")])
        self.run_loop(self.make_gemini(session))
        session2 = _FakeSession([_user("Deuxième question"), _model("Réponse."), _end()])
        self.run_loop(self.make_gemini(session2))
        self.assertEqual(self.context.turn_count(), 2)
        messages = self.context.get_messages()
        self.assertTrue(messages[0].failed)
        self.assertFalse(messages[-1].failed)


# ---------------------------------------------------------------------------
# Séparation contexte / mémoire persistante
# ---------------------------------------------------------------------------


class MemorySeparationTests(_PipelineCase):
    def setUp(self) -> None:
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.memory = MemoryManager(
            os.path.join(self.tmp.name, "memory.db"),
            enabled=True,
            max_results=5,
            min_importance=1,
        )
        self.addCleanup(set_default_memory_manager, None)

    def _count_memories(self) -> int:
        result = self.memory.list_memories(limit=50)
        return len(result.get("memories", []))

    def test_une_conversation_ordinaire_n_ecrit_pas_en_memoire(self) -> None:
        session = _FakeSession([
            _user("Quelle est la capitale de l'Italie ?"),
            _model("Rome."),
            _end(),
        ])
        self.run_loop(self.make_gemini(session, memory_manager=self.memory))
        self.assertEqual(self.context.turn_count(), 1)
        self.assertEqual(self._count_memories(), 0)

    def test_demande_explicite_ecrit_en_memoire_et_dans_le_contexte(self) -> None:
        session = _FakeSession([
            _user("Souviens-toi que je préfère les réponses courtes."),
            _model("C'est noté."),
            _end(),
        ])
        self.run_loop(self.make_gemini(session, memory_manager=self.memory))
        self.assertEqual(self._count_memories(), 1)
        self.assertEqual(self.context.turn_count(), 1)

    def test_le_reset_du_contexte_ne_touche_pas_la_memoire(self) -> None:
        self.memory.add_memory("L'utilisateur s'appelle Marius.", category="identity", importance=5)
        set_default_conversation_context(self.context)
        self.context.add_user_message("Question")
        self.context.add_assistant_message("Réponse")
        tools.TOOL_FUNCTIONS["reset_conversation"]()
        self.assertTrue(self.context.is_empty())
        self.assertEqual(self._count_memories(), 1)
        found = self.memory.search_memories("Marius")
        self.assertGreaterEqual(found["count"], 1)

    def test_la_memoire_n_est_pas_alimentee_par_les_reponses_de_jarvis(self) -> None:
        session = _FakeSession([
            _user("Et sinon ?"),
            _model("Souviens-toi que la tour Eiffel mesure 330 mètres."),
            _end(),
        ])
        self.run_loop(self.make_gemini(session, memory_manager=self.memory))
        self.assertEqual(self._count_memories(), 0)


# ---------------------------------------------------------------------------
# Modes et redémarrage
# ---------------------------------------------------------------------------


class ModeAndRestartTests(_PipelineCase):
    def _populate(self) -> None:
        set_default_conversation_context(self.context)
        self.context.add_user_message("Quelle est la capitale de l'Italie ?")
        self.context.add_assistant_message("Rome.")

    def test_switch_blob_desktop_conserve_le_contexte(self) -> None:
        self._populate()
        with mock.patch("src.settings.set_interface_mode", side_effect=lambda mode, *a, **k: mode):
            for mode in ("desktop", "blob", "desktop"):
                result = tools.TOOL_FUNCTIONS["set_interface_mode"](mode)
                self.assertTrue(result["success"])
        self.assertEqual(self.context.turn_count(), 1)
        self.assertEqual(len(self.context.get_messages()), 2)

    def test_mode_focus_et_jeu_conservent_le_contexte(self) -> None:
        self._populate()
        manager = mock.Mock()
        manager.activate_focus_mode.return_value = {"success": True}
        manager.disable_mode.return_value = {"success": True}
        manager.block_for_tool.return_value = None
        with mock.patch("src.tools.get_default_mode_manager", return_value=manager):
            tools.TOOL_FUNCTIONS["activate_focus_mode"](duration_minutes=25)
            tools.TOOL_FUNCTIONS["disable_jarvis_mode"]()
        self.assertEqual(self.context.turn_count(), 1)
        self.assertEqual(len(self.context.get_messages()), 2)

    def test_le_changement_de_provider_conserve_le_contexte(self) -> None:
        self._populate()
        self.context.set_provider("ollama")
        self.assertEqual(len(self.context.messages_for_provider()), 2)
        self.context.set_provider("gemini")
        self.assertEqual(len(self.context.get_messages()), 2)

    def test_un_nouveau_processus_demarre_sans_contexte(self) -> None:
        # Le contexte vit en mémoire : un nouvel objet (= un redémarrage de
        # Jarvis) est nécessairement vide.
        self.assertTrue(ConversationContext().is_empty())

    def test_les_points_d_entree_ouvrent_une_conversation_neuve(self) -> None:
        for name in ("src/main.py", "src/ui.py"):
            source = (REPO_ROOT / name).read_text(encoding="utf-8")
            self.assertIn("get_default_conversation_context", source, name)
            self.assertIn("start_new_conversation", source, name)
            self.assertIn("conversation=conversation", source, name)


if __name__ == "__main__":
    unittest.main()
