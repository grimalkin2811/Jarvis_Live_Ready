"""Runner vocal : le VRAI pipeline Jarvis branché sur le faux serveur Live.

Ce module exécute ``GeminiLive`` exactement comme le font ``src/main.py`` et
``src/ui.py`` (boucle connect -> receive_loop -> close -> reconnexion), mais
avec :

* le micro remplacé par ``harness.speak(texte)`` qui pousse des chunks PCM via
  ``gemini.send_audio`` -> ``send_realtime_input`` (le canal audio réel) ;
* le haut-parleur remplacé par un enregistrement des callbacks
  (``on_audio``, ``on_assistant_transcript``, ``on_turn_complete``…) ;
* le modèle remplacé par l'oracle sémantique du faux serveur.

La réponse retournée par ``speak()`` ne peut être correcte que si l'information
a réellement atteint le « modèle » sous une forme exploitable. C'est le test de
vérité demandé par la mission (§9 : chemin réel micro -> Live -> transcription
-> modèle -> réponse vocale, pas uniquement des mocks).
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

from src.conversation import ConversationContext
from src.gemini_live import AuthError, GeminiLive

from .live_harness import FakeClient, FakeLiveServer

#: Timeout par défaut d'un tour vocal (le faux serveur répond instantanément).
TURN_TIMEOUT = 5.0


class TurnLog:
    """Un tour observé par le harness (côté interface)."""

    def __init__(self) -> None:
        self.user_text: str = ""
        self.assistant_text: str = ""
        self.interrupted: bool = False
        self.tools: list[str] = []

    def __repr__(self) -> str:  # pragma: no cover - debug
        return f"Turn(user={self.user_text!r}, assistant={self.assistant_text!r}, tools={self.tools})"


class VoiceHarness:
    """Cycle de vie complet d'un Jarvis vocal simulé, observable et pilotable."""

    def __init__(
        self,
        server: FakeLiveServer,
        *,
        model: str = "gemini-2.5-flash-native-audio-preview-12-2025",
        conversation: ConversationContext | None = None,
        reconnect_delay: float = 0.05,
        **gemini_kwargs: Any,
    ) -> None:
        self.server = server
        self.conversation = conversation if conversation is not None else ConversationContext(
            max_turns=20, max_tokens=4096
        )
        self.reconnect_delay = reconnect_delay
        self.turns: list[TurnLog] = []
        self.audio_chunks: list[bytes] = []
        self.notifications: list[str] = []
        self.auth_errors = 0
        self._current = TurnLog()
        self._turn_done: asyncio.Event = asyncio.Event()
        self._turn_done.set()
        self._connected: asyncio.Event = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self.gemini = GeminiLive(
            key="test-key",
            model=model,
            user="Test",
            on_audio=self._on_audio,
            on_turn_complete=self._on_turn_complete,
            on_interrupted=self._on_interrupted,
            on_speaking=lambda: None,
            on_tool_start=lambda name: self._current.tools.append(name),
            on_tool_end=lambda name, ok=True: None,
            on_user_transcript=self._on_user_transcript,
            on_assistant_transcript=self._on_assistant_transcript,
            conversation=self.conversation,
            **gemini_kwargs,
        )
        self.gemini.client = FakeClient(server)

    # -- callbacks du pipeline -------------------------------------------------

    def _on_audio(self, pcm: bytes) -> None:
        self.audio_chunks.append(pcm)

    def _on_user_transcript(self, text: str, final: bool = False) -> None:
        if not self._current.user_text:
            self._turn_done.clear()

    def _on_assistant_transcript(self, text: str) -> None:
        self._current.assistant_text = text

    def _on_turn_complete(self) -> None:
        self.turns.append(self._current)
        self._current = TurnLog()
        self._turn_done.set()

    def _on_interrupted(self) -> None:
        self._current.interrupted = True

    # -- cycle de vie ------------------------------------------------------------

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        await asyncio.wait_for(self._connected.wait(), timeout=TURN_TIMEOUT)

    async def stop(self) -> None:
        self._stop.set()
        # Coupure immédiate de la session courante (équivalent d'une perte
        # réseau) : sans elle, receive_loop() resterait bloquée sur la file et
        # stop() brûlerait son timeout de 5 s à chaque test.
        try:
            self.server.drop_connection()
        except Exception:
            pass
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=TURN_TIMEOUT)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        try:
            await self.gemini.close()
        except Exception:
            pass

    async def _run(self) -> None:
        """Boucle de reconnexion, calquée sur src/main.py / src/ui.py."""
        gemini = self.gemini
        while not self._stop.is_set():
            gemini.reconnect_requested = False
            try:
                await gemini.connect()
                self._connected.set()
                await gemini.receive_loop()
            except AuthError:
                self.auth_errors += 1
                break
            except asyncio.CancelledError:
                break
            except Exception:
                if not gemini.reconnect_requested:
                    await asyncio.sleep(self.reconnect_delay)
            else:
                await asyncio.sleep(0)
            finally:
                try:
                    await gemini.close()
                except Exception:
                    pass

    # -- pilotage ------------------------------------------------------------------

    async def _settle(self, cycles: int = 8) -> None:
        for _ in range(cycles):
            await asyncio.sleep(0)

    async def _wait_sendable(self, timeout: float = TURN_TIMEOUT) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while not self.gemini.can_send():
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError("Jarvis n'accepte pas l'audio (can_send bloqué)")
            await asyncio.sleep(0)

    async def speak(self, text: str, chunks: int = 3, timeout: float = TURN_TIMEOUT) -> str:
        """« Parle » à Jarvis par le canal audio réel et retourne la réponse."""
        await self._settle()
        await self._wait_sendable()
        session = self.server.current
        session.queue_utterance(text, chunks)
        self._turn_done.clear()
        pcm = b"\x01\x02" * 160
        for _ in range(chunks):
            await self._wait_sendable()
            await self.gemini.send_audio(pcm)
            await self._settle(2)
        await asyncio.wait_for(self._turn_done.wait(), timeout=timeout)
        await self._settle()
        return self.turns[-1].assistant_text if self.turns else ""

    async def wait_sessions(self, count: int, timeout: float = TURN_TIMEOUT) -> None:
        deadline = asyncio.get_event_loop().time() + timeout
        while len(self.server.sessions) < count:
            if asyncio.get_event_loop().time() > deadline:
                raise TimeoutError(
                    f"{len(self.server.sessions)} session(s) ouverte(s), {count} attendues"
                )
            await asyncio.sleep(0)

    @property
    def last_turn(self) -> TurnLog:
        return self.turns[-1] if self.turns else TurnLog()

    def context_texts(self) -> list[tuple[str, str]]:
        return [(m.role, m.text) for m in self.conversation.get_messages()]
