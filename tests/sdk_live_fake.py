"""Faux service Gemini Live au plus près du SDK RÉEL (correctif PR #50).

Différence avec ``tests/live_harness.py`` (``FakeLiveServer``) : les messages
sont de VRAIS objets ``google.genai.types`` (``LiveServerMessage``,
``LiveServerContent``, ``InteractionStatus``…) et ``receive()`` s'arrête avec
la règle EXACTE du SDK installé (``google.genai.live._is_interaction_complete``) :
un ``turn_complete`` accompagné de ``interaction_status=IN_PROGRESS`` ne termine
PAS l'itération, seul ``IDLE`` (ou un ``turn_complete`` sans statut) la termine.

Règles de protocole reproduites (référence Live API, ai.google.dev/api/live) :

* « interrupted > turn_complete » : une génération interrompue se termine par
  un ``turn_complete`` qui lui appartient (``generationComplete``) ;
* un ``clientContent`` « interrompt toute génération en cours »
  (``BidiGenerateContentClientContent``) ;
* ``interactionStatus`` est toujours envoyé avec ``turnComplete``.

Ne prouve RIEN sur le service réel : sert à figer, de façon déterministe, les
séquences documentées que le service réel produit.
"""

from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from google.genai import types
from google.genai.live import _is_interaction_complete

IDLE = types.InteractionStatus.IDLE
IN_PROGRESS = types.InteractionStatus.IN_PROGRESS

#: 0,3 s de « voix » à 24 kHz : au-dessus du pré-tampon de lecture d'AudioIO.
VOICE = b"\x10\x00" * 7200


def user(text: str) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(input_transcription=types.Transcription(text=text))
    )


def model_text(text: str) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(output_transcription=types.Transcription(text=text))
    )


def model_audio(data: bytes = VOICE) -> types.LiveServerMessage:
    part = types.Part(inline_data=types.Blob(data=data, mime_type="audio/pcm;rate=24000"))
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(model_turn=types.Content(role="model", parts=[part]))
    )


def interrupted() -> types.LiveServerMessage:
    return types.LiveServerMessage(server_content=types.LiveServerContent(interrupted=True))


def turn_complete(status: types.InteractionStatus | None = IDLE, *, waiting_for_input: bool | None = None,
                  reason: str | None = None, generation_complete: bool | None = None) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        server_content=types.LiveServerContent(
            turn_complete=True,
            interaction_status=status,
            waiting_for_input=waiting_for_input,
            turn_complete_reason=reason,
            generation_complete=generation_complete,
        )
    )


def answer(text: str, status: types.InteractionStatus | None = IDLE) -> list[types.LiveServerMessage]:
    """Réponse complète : transcription, audio, fin de génération puis fin de tour."""
    return [model_text(text), model_audio(), turn_complete(status, generation_complete=True)]


def resumption(handle: str) -> types.LiveServerMessage:
    return types.LiveServerMessage(
        session_resumption_update=types.LiveServerSessionResumptionUpdate(new_handle=handle, resumable=True)
    )


class SdkLikeSession:
    """Session : file de messages serveur + journal de tout ce que Jarvis envoie."""

    def __init__(self, session_id: str = "fake-session") -> None:
        self.session_id = session_id
        self._queue: asyncio.Queue = asyncio.Queue()
        self.sent: list[tuple] = []
        self.closed = False
        #: Réaction scriptée à un ``clientContent`` : ``await hook(turns, turn_complete)``.
        self.on_client_content: Callable[[Any, bool], Awaitable[None]] | None = None

    # -- côté serveur ------------------------------------------------------------
    def push(self, *messages) -> None:
        for message in messages:
            self._queue.put_nowait(message)

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            self._queue.put_nowait(None)

    async def receive(self):
        while True:
            message = await self._queue.get()
            if message is None:
                return
            yield message
            if _is_interaction_complete(message.server_content):
                return

    # -- côté Jarvis ---------------------------------------------------------------
    async def send_client_content(self, turns=None, turn_complete: bool = True) -> None:
        if self.closed:
            raise RuntimeError("session fermée")
        self.sent.append(("client_content", turns, turn_complete))
        if self.on_client_content is not None:
            await self.on_client_content(turns, turn_complete)

    async def send_realtime_input(self, audio=None, audio_stream_end=None, **_kwargs) -> None:
        if self.closed:
            raise RuntimeError("session fermée")
        if audio_stream_end:
            self.sent.append(("audio_stream_end",))
        elif audio is not None:
            self.sent.append(("audio", len(getattr(audio, "data", b"") or b"")))

    async def send_tool_response(self, function_responses=None) -> None:
        self.sent.append(("tool_response", function_responses))

    # -- lecture -------------------------------------------------------------------
    def kinds(self) -> list[str]:
        return [entry[0] for entry in self.sent]

    def client_texts(self) -> list[str]:
        texts = []
        for kind, *rest in self.sent:
            if kind != "client_content":
                continue
            for turn in rest[0] or []:
                for part in (turn.get("parts") if isinstance(turn, dict) else []) or []:
                    if isinstance(part, dict) and part.get("text", "").strip():
                        texts.append(part["text"])
        return texts


class ProtocolServer:
    """Serveur scripté : rejeu de contexte avec réponse (2.x), interruption par
    ``clientContent``, réponses calculées sur l'historique RÉELLEMENT reçu.

    ``seed_reply_delay`` : délai de la réponse du modèle au rejeu de contexte —
    au-delà de ``SEED_COMMIT_TIMEOUT_SECONDS``, elle arrive APRÈS l'ouverture de
    la porte (cas E08 : historique long après une coupure réseau).
    """

    def __init__(self, *, seed_reply_delay: float = 0.0, answer_delay: float = 0.05) -> None:
        self.seed_reply_delay = seed_reply_delay
        self.answer_delay = answer_delay
        self.sessions: list[SdkLikeSession] = []
        self.history: dict[str, list[tuple[str, str]]] = {}
        self.handles: dict[str, list[tuple[str, str]]] = {}
        self._generating: dict[str, asyncio.Task] = {}
        self.interruptions_by_client = 0

    # -- fabrique (client.aio.live.connect) ----------------------------------------
    def client(self) -> SimpleNamespace:
        server = self

        class _Ctx:
            def __init__(self, config) -> None:
                handle = getattr(getattr(config, "session_resumption", None), "handle", None)
                self.session = server._open(handle)

            async def __aenter__(self):
                return self.session

            async def __aexit__(self, *exc):
                self.session.close()
                return False

        return SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=lambda model, config: _Ctx(config))))

    def _open(self, handle: str | None) -> SdkLikeSession:
        session = SdkLikeSession(f"s{len(self.sessions) + 1}")
        self.sessions.append(session)
        self.history[session.session_id] = list(self.handles.get(handle, [])) if handle else []

        async def on_client_content(turns, complete) -> None:
            await self._client_content(session, turns, complete)

        session.on_client_content = on_client_content
        return session

    # -- comportement ---------------------------------------------------------------
    def _texts(self, turns) -> list[tuple[str, str]]:
        out = []
        for turn in turns or []:
            role = turn.get("role", "user")
            text = " ".join(p.get("text", "") for p in turn.get("parts", []) if isinstance(p, dict)).strip()
            out.append((role, text))
        return out

    async def _client_content(self, session: SdkLikeSession, turns, complete: bool) -> None:
        sid = session.session_id
        running = self._generating.get(sid)
        if running is not None and not running.done():
            # « A message here will interrupt any current model generation. »
            running.cancel()
            self.interruptions_by_client += 1
            session.push(interrupted(), turn_complete(IN_PROGRESS))
        entries = self._texts(turns)
        self.history[sid].extend(e for e in entries if e[1])
        if not complete:
            return
        is_seed = len(entries) > 1 or (entries and not entries[-1][1])
        delay = self.seed_reply_delay if is_seed else self.answer_delay
        reply = "Je suis là, je t'écoute." if is_seed else self.oracle(sid, entries[-1][1])
        self._generating[sid] = asyncio.ensure_future(self._reply(session, reply, delay))

    async def _reply(self, session: SdkLikeSession, text: str, delay: float) -> None:
        await asyncio.sleep(delay)
        session.push(*answer(text))
        self.history[session.session_id].append(("model", text))
        handle = f"{session.session_id}-h{len(self.history[session.session_id])}"
        self.handles[handle] = list(self.history[session.session_id])
        session.push(resumption(handle))

    def voice_turn(self, session: SdkLikeSession, text: str) -> None:
        """L'utilisateur parle (VAD serveur) : transcription puis réponse."""
        self.history[session.session_id].append(("user", text))
        session.push(user(text))
        self._generating[session.session_id] = asyncio.ensure_future(
            self._reply(session, self.oracle(session.session_id, text), self.answer_delay)
        )

    def oracle(self, sid: str, question: str) -> str:
        seen = " ".join(text for _role, text in self.history[sid])
        asks = question.rstrip().endswith("?") or re.match(r"(rappelle|quel|comment|dis)", question, re.IGNORECASE)
        if not asks:
            return "D'accord."
        if re.search(r"code", question, re.IGNORECASE):
            match = re.search(r"ORION-\d", seen)
            return f"Le code est {match.group(0)}." if match else "Je ne connais aucun code."
        if re.search(r"prénom|appelle", question, re.IGNORECASE):
            return "Tu t'appelles Ada." if "Ada" in seen else "Je ne sais pas."
        return "D'accord."
