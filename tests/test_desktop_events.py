"""Évènements réels backend → Desktop Mode (v1.7.0) et non-régression.

Ces tests vérifient que le Desktop Mode est piloté par des **faits** du
pipeline existant (transcription 1.6.0, appels d'outils, TTS, interruption) et
non par des minuteurs, et que rien de tout cela ne change le comportement du
backend quand personne n'écoute.
"""

from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest

# Aucun téléchargement de modèles pendant les tests (hors ligne).
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))


def _install_fake_sounddevice() -> None:
    """Même stratégie que ``tests/test_audio_controls.py`` : aucun matériel."""
    if "sounddevice" in sys.modules:
        return
    sd = types.ModuleType("sounddevice")

    class _FakeStream:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

        def stop(self):
            pass

        def close(self):
            pass

    sd.RawInputStream = _FakeStream
    sd.RawOutputStream = _FakeStream
    sys.modules["sounddevice"] = sd


_install_fake_sounddevice()

from UI.desktop.events import DesktopEventBridge  # noqa: E402
from UI.desktop.state import DesktopEvent, DesktopState, DesktopStateMachine  # noqa: E402


class BridgeTests(unittest.TestCase):
    """Le pont est sans Qt, thread-safe et ne casse jamais le backend."""

    def setUp(self) -> None:
        self.bridge = DesktopEventBridge()
        self.seen: list[tuple[str, dict]] = []

    def test_no_handler_is_a_no_op(self) -> None:
        self.assertFalse(self.bridge.attached())
        self.bridge.hotword()  # ne doit rien faire, ni lever

    def test_handler_receives_events(self) -> None:
        self.bridge.set_handler(lambda name, payload: self.seen.append((name, payload)))
        self.bridge.hotword()
        self.bridge.tool_start("music_play")
        self.bridge.tool_end("music_play", True)
        self.bridge.transcript("bonjour", final=True)
        self.bridge.response("salut")
        self.bridge.interrupted()
        self.bridge.error("réseau")
        self.bridge.level(0.42)
        names = [name for name, _ in self.seen]
        self.assertEqual(
            names,
            [
                DesktopEvent.HOTWORD,
                DesktopEvent.TOOL_START,
                DesktopEvent.TOOL_END,
                DesktopEvent.TRANSCRIPT,
                DesktopEvent.RESPONSE,
                DesktopEvent.INTERRUPTED,
                DesktopEvent.ERROR,
                "level",
            ],
        )
        self.assertEqual(self.seen[1][1]["name"], "music_play")
        self.assertAlmostEqual(self.seen[-1][1]["value"], 0.42)

    def test_last_handler_wins(self) -> None:
        other: list[str] = []
        self.bridge.set_handler(lambda name, payload: self.seen.append((name, payload)))
        self.bridge.set_handler(lambda name, payload: other.append(name))
        self.bridge.hotword()
        self.assertEqual(self.seen, [])
        self.assertEqual(other, [DesktopEvent.HOTWORD])

    def test_failing_handler_never_propagates(self) -> None:
        def _boom(_name, _payload):
            raise RuntimeError("interface morte")

        self.bridge.set_handler(_boom)
        self.bridge.hotword()  # ne doit pas lever

    def test_level_is_clamped_and_tolerant(self) -> None:
        self.bridge.set_handler(lambda name, payload: self.seen.append((name, payload)))
        self.bridge.level(12.0)
        self.bridge.level(-4.0)
        self.bridge.level("oups")
        self.assertEqual([round(p["value"], 3) for _, p in self.seen], [1.0, 0.0])

    def test_empty_event_is_ignored(self) -> None:
        self.bridge.set_handler(lambda name, payload: self.seen.append((name, payload)))
        self.bridge.emit("")
        self.assertEqual(self.seen, [])

    def test_detaching_stops_delivery(self) -> None:
        self.bridge.set_handler(lambda name, payload: self.seen.append((name, payload)))
        self.bridge.set_handler(None)
        self.bridge.hotword()
        self.assertEqual(self.seen, [])


# ---------------------------------------------------------------------------
# Intégration Gemini Live : les évènements viennent du pipeline existant
# ---------------------------------------------------------------------------


class _FakePart:
    def __init__(self) -> None:
        self.inline_data = None


class _FakeModelTurn:
    parts: list = []


class _FakeServerContent:
    def __init__(self, **kwargs) -> None:
        self.input_transcription = kwargs.get("input_transcription")
        self.output_transcription = kwargs.get("output_transcription")
        self.model_turn = kwargs.get("model_turn")
        self.interrupted = kwargs.get("interrupted", False)
        self.turn_complete = kwargs.get("turn_complete", False)


class _FakeResponse:
    def __init__(self, server_content=None, tool_call=None) -> None:
        self.server_content = server_content
        self.tool_call = tool_call
        self.session_resumption_update = None
        self.go_away = None
        self.usage_metadata = None


class _FakeSession:
    """Fidélité au protocole réel (v1.7.4) : ``receive()`` consomme les
    réponses restantes (pas de rejeu à chaque appel) et se termine à un
    ``turn_complete``/``interrupted`` — comme le SDK réel (cf.
    googleapis/python-genai#1224). Depuis le correctif reconnexion-par-tour,
    ``GeminiLive`` rappelle ``receive()`` sur la MÊME session tant qu'elle
    reste ouverte.
    """

    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.tool_responses = []

    def receive(self):
        async def _gen():
            while self._responses:
                item = self._responses.pop(0)
                yield item
                content = getattr(item, "server_content", None)
                if content is not None and (
                    getattr(content, "turn_complete", False)
                    or getattr(content, "interrupted", False)
                ):
                    return

        return _gen()

    async def send_tool_response(self, function_responses=None):
        self.tool_responses.append(function_responses)


def _make_gemini(**hooks):
    """Construit un GeminiLive sans réseau (client Gemini neutralisé)."""
    from src import gemini_live as module

    real_client = module.genai.Client
    module.genai.Client = lambda api_key=None: types.SimpleNamespace(aio=None)
    try:
        instance = module.GeminiLive("clé", "modèle", "Testeur", on_audio=lambda data: None, **hooks)
    finally:
        module.genai.Client = real_client
    return instance


class GeminiLiveEventTests(unittest.TestCase):
    """Les nouveaux callbacks sont alimentés par les données déjà présentes."""

    def test_callbacks_default_to_none(self) -> None:
        gemini = _make_gemini()
        self.assertIsNone(gemini.on_tool_start)
        self.assertIsNone(gemini.on_tool_end)
        self.assertIsNone(gemini.on_user_transcript)
        self.assertIsNone(gemini.on_assistant_transcript)

    def test_transcripts_are_forwarded(self) -> None:
        user: list[str] = []
        assistant: list[str] = []
        gemini = _make_gemini(
            on_user_transcript=lambda text, final: user.append(text),
            on_assistant_transcript=assistant.append,
        )
        gemini.session = _FakeSession(
            [
                _FakeResponse(
                    _FakeServerContent(
                        input_transcription=types.SimpleNamespace(text="quelle heure")
                    )
                ),
                _FakeResponse(
                    _FakeServerContent(
                        input_transcription=types.SimpleNamespace(text="est-il")
                    )
                ),
                _FakeResponse(
                    _FakeServerContent(
                        output_transcription=types.SimpleNamespace(text="il est midi")
                    )
                ),
            ]
        )
        asyncio.run(gemini.receive_loop())
        self.assertEqual(user, ["quelle heure", "quelle heure est-il"])
        self.assertEqual(assistant, ["il est midi"])

    def test_transcript_comes_from_the_conversation_buffer(self) -> None:
        """Pas de second système : c'est le tampon du contexte 1.6.0."""
        seen: list[tuple[str, str]] = []
        gemini = None

        def _capture(text, _final):
            # On compare, AU MOMENT DE L'ÉMISSION, le texte transmis à
            # l'interface et le tampon qui alimente le contexte 1.6.0.
            seen.append((text, " ".join(gemini._turn_user_text).strip()))

        gemini = _make_gemini(on_user_transcript=_capture)
        gemini.session = _FakeSession(
            [
                _FakeResponse(
                    _FakeServerContent(
                        input_transcription=types.SimpleNamespace(text="bonjour")
                    )
                )
            ]
        )
        asyncio.run(gemini.receive_loop())
        self.assertEqual(seen, [("bonjour", "bonjour")])

    def test_tool_start_and_end_are_emitted(self) -> None:
        from src import tools as tools_module

        started: list[str] = []
        finished: list[tuple[str, bool]] = []
        gemini = _make_gemini(
            on_tool_start=started.append,
            on_tool_end=lambda name, ok: finished.append((name, ok)),
        )
        name = next(iter(tools_module.TOOL_FUNCTIONS))
        original = tools_module.TOOL_FUNCTIONS[name]
        tools_module.TOOL_FUNCTIONS[name] = lambda **kwargs: {"success": True}
        call = types.SimpleNamespace(name=name, args={}, id="1")
        gemini.session = _FakeSession(
            [_FakeResponse(tool_call=types.SimpleNamespace(function_calls=[call]))]
        )
        try:
            asyncio.run(gemini.receive_loop())
        finally:
            tools_module.TOOL_FUNCTIONS[name] = original
        self.assertEqual(started, [name])
        self.assertEqual(finished, [(name, True)])

    def test_tool_end_reports_failure(self) -> None:
        from src import tools as tools_module

        finished: list[tuple[str, bool]] = []
        gemini = _make_gemini(on_tool_end=lambda name, ok: finished.append((name, ok)))
        name = next(iter(tools_module.TOOL_FUNCTIONS))
        original = tools_module.TOOL_FUNCTIONS[name]

        def _fail(**kwargs):
            raise RuntimeError("cassé")

        tools_module.TOOL_FUNCTIONS[name] = _fail
        call = types.SimpleNamespace(name=name, args={}, id="1")
        gemini.session = _FakeSession(
            [_FakeResponse(tool_call=types.SimpleNamespace(function_calls=[call]))]
        )
        try:
            asyncio.run(gemini.receive_loop())
        finally:
            tools_module.TOOL_FUNCTIONS[name] = original
        self.assertEqual(finished, [(name, False)])

    def test_a_failing_ui_callback_does_not_break_the_loop(self) -> None:
        def _boom(*_args):
            raise RuntimeError("UI morte")

        gemini = _make_gemini(on_user_transcript=_boom)
        gemini.session = _FakeSession(
            [
                _FakeResponse(
                    _FakeServerContent(
                        input_transcription=types.SimpleNamespace(text="bonjour")
                    )
                ),
                _FakeResponse(_FakeServerContent(turn_complete=True)),
            ]
        )
        asyncio.run(gemini.receive_loop())
        # La boucle est allée jusqu'au bout : la demande a bien été versée au
        # contexte malgré l'exception levée par l'interface.
        self.assertGreater(gemini.conversation.size(), 0)


# ---------------------------------------------------------------------------
# Intégration AudioIO : niveau de la voix de Jarvis
# ---------------------------------------------------------------------------


class AudioOutputLevelTests(unittest.TestCase):
    def _audio(self, hook=None):
        from src.audio import AudioIO

        return AudioIO(lambda pcm: None, output_level_hook=hook)

    def test_hook_is_optional(self) -> None:
        audio = self._audio()
        self.assertIsNone(audio.output_level_hook)
        audio.running = True
        audio.play(b"\x00\x01" * 64)  # ne doit pas lever

    def test_level_is_emitted_while_speaking(self) -> None:
        import numpy as np

        levels: list[float] = []
        audio = self._audio(levels.append)
        audio.running = True
        loud = (np.ones(512, dtype=np.int16) * 9000).tobytes()
        audio.play(loud)
        self.assertEqual(len(levels), 1)
        self.assertGreater(levels[0], 0.5)

    def test_level_is_throttled(self) -> None:
        import numpy as np

        levels: list[float] = []
        audio = self._audio(levels.append)
        audio.running = True
        block = (np.ones(256, dtype=np.int16) * 1000).tobytes()
        for _ in range(25):
            audio.play(block)
        # ~20 Hz : 25 appels immédiats ne produisent qu'une mesure.
        self.assertEqual(len(levels), 1)

    def test_failing_hook_never_breaks_playback(self) -> None:
        def _boom(_level):
            raise RuntimeError("UI morte")

        audio = self._audio(_boom)
        audio.running = True
        audio.play(b"\x10\x00" * 128)
        self.assertGreater(audio._queued_bytes, 0)


# ---------------------------------------------------------------------------
# Non-régression : le pipeline 1.6.0 continue de piloter l'affichage
# ---------------------------------------------------------------------------


class PresenceNonRegressionTests(unittest.TestCase):
    def test_audio_presence_sequence_still_drives_states(self) -> None:
        machine = DesktopStateMachine()
        # Séquence réellement émise par AudioIO en 1.6.0.
        for presence, expected in (
            ("loading", DesktopState.LOADING),
            ("hidden", DesktopState.HIDDEN),
            ("listening", DesktopState.LISTENING),
            ("speaking", DesktopState.SPEAKING),
            ("listening", DesktopState.FOLLOW_UP),
            ("hidden", DesktopState.HIDDEN),
        ):
            machine.handle_presence(presence)
            self.assertEqual(machine.state, expected, presence)

    def test_audio_module_still_emits_the_same_strings(self) -> None:
        """Le contrat de présence de AudioIO n'a pas bougé."""
        import inspect

        from src import audio as audio_module

        source = inspect.getsource(audio_module)
        for state in ("listening", "hidden"):
            self.assertIn(f'_emit_presence("{state}")', source)
        # Et le hook historique reste un paramètre du constructeur.
        signature = inspect.signature(audio_module.AudioIO.__init__)
        self.assertIn("presence_hook", signature.parameters)
        self.assertIn("voice_hook", signature.parameters)
        self.assertIn("output_level_hook", signature.parameters)


if __name__ == "__main__":
    unittest.main()
