"""Harness de test du contexte conversationnel : faux serveur Gemini Live.

Pourquoi ce module existe
-------------------------
La mission « contexte conversationnel fiable » impose de prouver que
l'information n'est pas seulement **stockée** localement (``ConversationContext``)
mais **réellement envoyée à Gemini sous une forme exploitable par le modèle**,
y compris lorsque la question suivante arrive en **audio** (chemin réel de
Jarvis : micro -> ``send_realtime_input`` -> réponse vocale).

Un mock naïf ne peut pas prouver cela : il affirmerait seulement « la méthode a
été appelée ». Ce module implémente donc un **faux serveur Live fidèle au
protocole et aux comportements documentés et rapportés** :

1. ``setup`` -> ``setupComplete`` (avec ``session_id``) ;
2. ``clientContent`` : un envoi avec ``turnComplete=false`` reste **en attente**
   (pending) et, sur les modèles audio 2.x (sémantique par défaut ici, conforme
   aux retours réels — thread Google AI 111617), **n'est pas visible** pour la
   génération déclenchée par l'audio qui suit ; ``turnComplete=true`` **commite**
   l'historique ;
3. ``historyConfig.initialHistoryInClientContent`` (modèles 3.x) : l'historique
   initial est committé **sans appel modèle** ; sans ce drapeau, un
   ``clientContent`` en début de session 3.x est refusé (constat
   googleapis/js-genai#1448) ;
4. sur les modèles 2.x, un seed doit se terminer par un tour **user** (pipecat :
   « *we append a blank user turn to satisfy the server* ») — un écart est
   tracé dans ``protocol_warnings`` ;
5. reprise de session : registre de handles ; un handle inconnu/expiré lève
   ``APIError 1007 Invalid session handle`` (constat
   googleapis/python-genai#2197) ;
6. ``sessionResumptionUpdate`` (``newHandle``/``resumable``) après chaque tour,
   ``goAway`` à la demande, interruption scriptable, appels d'outils ;
7. un **oracle sémantique déterministe** joue le rôle du modèle : il ne peut
   répondre correctement (« Simon », « Hans Zimmer », « le deuxième »…) que si
   l'information est présente dans le contexte **visible** de la génération.
   La réponse observée par le test est donc une preuve de bout en bout.

Le faux serveur n'émet jamais de secrets et n'accède jamais au réseau.
"""

from __future__ import annotations

import asyncio
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from google.genai import errors as genai_errors

# ---------------------------------------------------------------------------
# Construction des messages serveur (format attendu par GeminiLive._receive_loop)
# ---------------------------------------------------------------------------


class _Transcript:
    def __init__(self, text: str) -> None:
        self.text = text


class _Part:
    def __init__(self, data: bytes | None = None, text: str | None = None) -> None:
        if data is not None:
            self.inline_data = type("_Inline", (), {"data": data})()
            self.text = None
        else:
            self.inline_data = None
            self.text = text


class _ModelTurn:
    def __init__(self, parts: list[_Part]) -> None:
        self.parts = parts


class _ServerContent:
    def __init__(
        self,
        *,
        user_text: str | None = None,
        model_text: str | None = None,
        audio: bytes | None = None,
        interrupted: bool = False,
        turn_complete: bool = False,
    ) -> None:
        self.input_transcription = _Transcript(user_text) if user_text else None
        self.output_transcription = _Transcript(model_text) if model_text else None
        self.model_turn = _ModelTurn([_Part(data=audio)]) if audio else None
        self.interrupted = interrupted
        self.turn_complete = turn_complete


class _FunctionCall:
    def __init__(self, name: str, args: dict[str, Any], cid: str) -> None:
        self.name = name
        self.args = args
        self.id = cid


class _ToolCall:
    def __init__(self, calls: list[_FunctionCall]) -> None:
        self.function_calls = calls


class _ResumptionUpdate:
    def __init__(self, new_handle: str | None, resumable: bool) -> None:
        self.new_handle = new_handle
        self.resumable = resumable


class _GoAway:
    def __init__(self, time_left: str) -> None:
        self.time_left = time_left


class _SetupComplete:
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id


class _Message:
    """Équivalent local de ``types.LiveServerMessage`` (attributs optionnels)."""

    def __init__(
        self,
        server_content: _ServerContent | None = None,
        tool_call: _ToolCall | None = None,
        session_resumption_update: _ResumptionUpdate | None = None,
        go_away: _GoAway | None = None,
        setup_complete: _SetupComplete | None = None,
    ) -> None:
        self.server_content = server_content
        self.tool_call = tool_call
        self.session_resumption_update = session_resumption_update
        self.go_away = go_away
        self.setup_complete = setup_complete


def msg_setup_complete(session_id: str) -> _Message:
    return _Message(setup_complete=_SetupComplete(session_id))


def msg_user_transcript(text: str) -> _Message:
    return _Message(_ServerContent(user_text=text))


def msg_model_transcript(text: str, audio: bytes | None = None) -> _Message:
    return _Message(_ServerContent(model_text=text, audio=audio))


def msg_turn_complete() -> _Message:
    return _Message(_ServerContent(turn_complete=True))


def msg_interrupted() -> _Message:
    return _Message(_ServerContent(interrupted=True))


# ---------------------------------------------------------------------------
# Trace du « câble » : tout ce que Jarvis envoie, dans l'ordre
# ---------------------------------------------------------------------------


@dataclass
class WireEvent:
    """Un message client -> serveur, horodaté et rattaché à une session."""

    session_id: str
    generation: int
    kind: str  # "client_content" | "realtime_audio" | "realtime_text" | "tool_response"
    turn_complete: bool | None = None
    contents: list[dict[str, Any]] = field(default_factory=list)
    audio_bytes: int = 0
    tool_names: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.monotonic)

    def describe(self) -> str:
        roles = "/".join(c.get("role", "?") for c in self.contents)
        parts = sum(len(c.get("parts", [])) for c in self.contents)
        return (
            f"[{self.kind} gen={self.generation} roles={roles or '-'} "
            f"turn_complete={self.turn_complete} parts={parts} audio={self.audio_bytes}]"
        )

    def all_text(self) -> str:
        chunks: list[str] = []
        for content in self.contents:
            for part in content.get("parts", []):
                if part.get("text"):
                    chunks.append(part["text"])
        return " ".join(chunks)


# ---------------------------------------------------------------------------
# Oracle sémantique : le « modèle » du faux serveur
# ---------------------------------------------------------------------------

_RE_NAME = re.compile(
    r"\b(?:mon pr[eéè]nom est|je m'appelle)\s+([A-ZÉÈÀÇ][\wéèàçêîôû-]+)", re.IGNORECASE
)
_RE_ASK_NAME = re.compile(r"\bquel est mon pr[eéè]nom\b|\bcomment je m'appelle\b", re.IGNORECASE)
_KNOWN_ARTISTS = ("Hans Zimmer", "Two Steps From Hell", "Daft Punk", "Ludovico Einaudi")
_RE_ASK_COMPOSER = re.compile(r"\b(?:compositeur|artiste|groupe)\b", re.IGNORECASE)
_RE_ASK_OTHER = re.compile(r"\bquel autre\b", re.IGNORECASE)
_RE_ASK_SECOND = re.compile(r"\ble deuxi[eè]me\b", re.IGNORECASE)
_RE_ASK_PREVIOUS = re.compile(r"\bcelui d'avant\b|\bpr[eé]c[eé]dente?\b", re.IGNORECASE)
_RE_ASK_BEFORE = re.compile(r"\bqu'est[- ]ce que je t'avais demand[eé] juste avant\b", re.IGNORECASE)
_RE_LIST_ITEM = re.compile(r"(\d+)\.\s*([^;|]+)")


def _normalize(text: str) -> str:
    raw = unicodedata.normalize("NFD", str(text or "").lower())
    stripped = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    return " ".join(stripped.split())


class SemanticOracle:
    """« Modèle » déterministe : ne sait que ce qui est dans le contexte visible.

    Chaque méthode reçoit le contexte visible sous forme de paires
    ``(role, texte)`` (historique committé + tour courant). Si l'information
    n'y figure pas, la réponse est un échec explicite — c'est précisément ce
    que les tests doivent distinguer d'une réponse correcte.
    """

    def __init__(self) -> None:
        self.overrides: list[tuple[re.Pattern[str], Callable[[list[tuple[str, str]]], str]]] = []

    def register(
        self, pattern: str, answer: Callable[[list[tuple[str, str]]], str]
    ) -> None:
        self.overrides.append((re.compile(pattern, re.IGNORECASE), answer))

    # -- extraction depuis le contexte visible --------------------------------

    @staticmethod
    def known_name(visible: list[tuple[str, str]]) -> str | None:
        for _role, text in visible:
            match = _RE_NAME.search(text)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def mentioned_artists(visible: list[tuple[str, str]]) -> list[str]:
        joined = " ".join(text for _role, text in visible)
        normalized = _normalize(joined)
        return [a for a in _KNOWN_ARTISTS if _normalize(a) in normalized]

    @staticmethod
    def last_list_items(visible: list[tuple[str, str]]) -> list[str]:
        """Dernière liste numérotée visible (résultats d'outil compactés)."""
        for _role, text in reversed(visible):
            items = _RE_LIST_ITEM.findall(text)
            if len(items) >= 2:
                return [label.strip() for _, label in items]
        return []

    @staticmethod
    def user_requests(visible: list[tuple[str, str]]) -> list[str]:
        return [text for role, text in visible if role == "user" and text]

    # -- génération ------------------------------------------------------------

    def answer(self, question: str, visible: list[tuple[str, str]]) -> tuple[str, bool]:
        """Retourne (réponse, succès). ``succès=False`` => contexte insuffisant."""
        for pattern, answer in self.overrides:
            if pattern.search(question):
                return answer(visible), True

        if _RE_ASK_NAME.search(question):
            name = self.known_name(visible)
            return (
                f"Ton prénom est {name}."
                if name
                else "Je ne sais pas, tu ne me l'as pas dit.",
                bool(name),
            )
        if _RE_ASK_COMPOSER.search(question) and not _RE_ASK_OTHER.search(question):
            artists = self.mentioned_artists(visible)
            return (
                f"Tu m'as parlé de {artists[0]}."
                if artists
                else "Je ne sais pas, aucun compositeur dans notre échange.",
                bool(artists),
            )
        if _RE_ASK_OTHER.search(question):
            artists = self.mentioned_artists(visible)
            return (
                f"Tu m'as aussi parlé de {artists[1]}."
                if len(artists) > 1
                else "Je ne sais pas.",
                len(artists) > 1,
            )
        if _RE_ASK_SECOND.search(question):
            items = self.last_list_items(visible)
            return (
                f"Je lance le deuxième : {items[1]}."
                if len(items) >= 2
                else "Je ne sais pas ce que tu veux lancer.",
                len(items) >= 2,
            )
        if _RE_ASK_PREVIOUS.search(question):
            items = self.last_list_items(visible)
            return (
                f"Je reviens au précédent : {items[0]}." if items else "Je ne sais pas.",
                bool(items),
            )
        if _RE_ASK_BEFORE.search(question):
            requests = self.user_requests(visible)
            # La demande précédant la question courante (la question vient
            # d'être committée : c'est l'avant-dernière demande user).
            prior = [r for r in requests if r != question]
            if prior:
                return f"Tu m'avais demandé : {prior[-1]}.", True
            return "Je ne sais pas.", False
        # Réponse générique (acknowledgement) : le tour est bien traité.
        return "D'accord.", True


# ---------------------------------------------------------------------------
# Aides de conversion
# ---------------------------------------------------------------------------


def _as_content_dicts(turns: Any) -> list[dict[str, Any]]:
    if turns is None:
        return []
    if isinstance(turns, dict):
        return [turns]
    result: list[dict[str, Any]] = []
    for turn in turns:
        if isinstance(turn, dict):
            result.append(turn)
        else:
            parts = []
            for part in getattr(turn, "parts", []) or []:
                text = getattr(part, "text", None)
                if text:
                    parts.append({"text": text})
            if parts:
                result.append({"role": getattr(turn, "role", "user") or "user", "parts": parts})
    return result


def _history_config_enabled(config: Any) -> bool:
    history = getattr(config, "history_config", None)
    return bool(history and getattr(history, "initial_history_in_client_content", False))


def _resumption_handle(config: Any) -> str | None:
    resumption = getattr(config, "session_resumption", None)
    return getattr(resumption, "handle", None)


def _split_fragments(text: str, parts: int = 2) -> list[str]:
    """Découpe un texte en fragments sur des frontières de mots (comme la
    transcription réelle, qui n'est jamais coupée au milieu d'un mot)."""
    if parts <= 1 or not text:
        return [text]
    words = text.split(" ")
    middle = max(1, len(words) // parts)
    return [" ".join(words[:middle]), " ".join(words[middle:])]


def _render_function_response(name: str, response: Any) -> str:
    """Rendu texte d'une réponse d'outil, listes numérotées incluses."""
    if isinstance(response, dict):
        pieces: list[str] = []
        for key, value in response.items():
            if isinstance(value, list) and value:
                numbered = " ; ".join(f"{i}. {item}" for i, item in enumerate(value, start=1))
                pieces.append(f"{key} : {numbered}")
            elif isinstance(value, (str, int, float, bool)):
                pieces.append(f"{key}={value}")
        return f"[résultat outil] {name} -> " + " | ".join(pieces)
    return f"[résultat outil] {name} -> {response}"


# ---------------------------------------------------------------------------
# Session Live factice
# ---------------------------------------------------------------------------


class FakeLiveSession:
    """Session conforme au protocole vu par ``GeminiLive`` (SDK AsyncSession)."""

    def __init__(self, server: "FakeLiveServer", session_id: str, generation: int) -> None:
        self.server = server
        self.session_id = session_id
        self.generation = generation
        self.setup_complete = _SetupComplete(session_id)
        self._queue: asyncio.Queue[_Message | None] = asyncio.Queue()
        self._closed = False
        # État conversationnel côté « serveur ».
        self.committed: list[dict[str, Any]] = []
        self.pending: list[dict[str, Any]] = []
        self.model_outputs: list[str] = []
        self.initial_phase = True  # aucun realtime_input depuis le setup
        self.protocol_warnings: list[str] = []
        self.last_handle: str | None = None
        # Utterances programmées par le test : liste [(texte, chunks_restants)]
        self._utterances: list[tuple[str, int]] = []
        self._active: tuple[str, int] | None = None
        self.server._register(self)

    # -- réception côté Jarvis ------------------------------------------------

    def receive(self):
        """Fidélité au protocole réel (v1.7.4) : ``receive()`` se termine
        naturellement à la fin d'UN tour (``turn_complete``/``go_away``),
        PAS seulement à la fermeture de la session.

        Constat confirmé par Google — googleapis/python-genai#1224 (résolu) :
        « the receive() method throws you out of the loop if turn is
        complete. To keep receiving messages from the following turns you
        need to put this part of the code under the while loop. » Avant ce
        correctif, le faux serveur modélisait ``receive()`` comme un flux
        infini qui ne s'arrêtait qu'à ``close()`` — ce qui masquait
        complètement la classe de bug « reconnexion après chaque tour »
        (v1.7.3 et antérieures) : aucun test ne pouvait la détecter puisque
        le faux ``receive()`` ne rendait jamais la main entre deux tours.

        Un appel sur une session déjà fermée échoue immédiatement (comme un
        WebSocket réellement clos), plutôt que de bloquer indéfiniment sur
        une file vide.
        """

        async def gen():
            if self._closed and self._queue.empty():
                raise genai_errors.ClientError(
                    1000, {"error": {"message": "session déjà fermée"}}
                )
            while True:
                message = await self._queue.get()
                if message is None:
                    return
                yield message
                server_content = getattr(message, "server_content", None)
                turn_complete = bool(getattr(server_content, "turn_complete", False))
                go_away = getattr(message, "go_away", None)
                if turn_complete or go_away is not None:
                    # Fin naturelle d'UN tour : le générateur s'arrête ici,
                    # exactement comme le SDK réel. La session reste ouverte
                    # (``self._closed`` inchangé) — un appel ultérieur à
                    # ``receive()`` doit reprendre où celui-ci s'est arrêté
                    # pour le tour suivant, SANS reconnexion.
                    return

        return gen()

    async def _emit(self, message: _Message) -> None:
        if not self._closed:
            await self._queue.put(message)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._queue.put_nowait(None)

    # -- envois côté Jarvis ---------------------------------------------------

    async def send_client_content(self, turns: Any = None, turn_complete: bool = True) -> None:
        contents = _as_content_dicts(turns)
        self.server.wire.append(
            WireEvent(
                session_id=self.session_id,
                generation=self.generation,
                kind="client_content",
                turn_complete=turn_complete,
                contents=contents,
            )
        )
        if self._closed:
            raise RuntimeError("session fermée")
        semantics = self.server.semantics
        history_config = _history_config_enabled(self.server.last_config)

        if (
            semantics == "3.x"
            and self.initial_phase
            and not history_config
            and contents
        ):
            # Constat github googleapis/js-genai#1448 : sans historyConfig, un
            # clientContent 3.x en début de session est refusé.
            self.close()
            raise genai_errors.APIError(
                1008, {"error": {"message": "clientContent requires historyConfig on this model"}}
            )

        if contents:
            # Exigence 2.x documentée par pipecat : le seed doit finir par un
            # tour user. On ne rejette pas (le comportement réel exact est
            # incertain) mais on trace l'écart de protocole.
            if (
                semantics == "2.x"
                and self.initial_phase
                and contents[-1].get("role") == "model"
            ):
                self.protocol_warnings.append("seed_termine_par_tour_model")
            self.pending.extend(contents)

        if not turn_complete:
            return

        # turnComplete=true : le contenu devient historique committé.
        committed_now = self.pending
        self.pending = []
        self.initial_phase = False
        self.committed.extend(committed_now)

        trigger = False
        if semantics == "3.x":
            # Avec historyConfig, l'historique initial ne déclenche PAS d'appel
            # modèle (documentation officielle HistoryConfig).
            trigger = False
        else:
            # 2.x : un tour committé se terminant par un contenu user déclenche
            # une inférence (le « récapitulatif » observé par pipecat).
            trigger = bool(committed_now) and committed_now[-1].get("role") != "model"
        if trigger:
            await self._generate(recap=True)

    async def send_realtime_input(
        self,
        audio: Any = None,
        text: str | None = None,
        media: Any = None,
        video: Any = None,
        audio_stream_end: bool | None = None,
        activity_start: Any = None,
        activity_end: Any = None,
    ) -> None:
        if self._closed:
            raise RuntimeError("session fermée")
        if text is not None:
            self.server.wire.append(
                WireEvent(
                    session_id=self.session_id,
                    generation=self.generation,
                    kind="realtime_text",
                )
            )
            await self._emit(_Message(_ServerContent(user_text=text)))
            await self._finish_utterance(text)
            return
        if audio is not None:
            data = getattr(audio, "data", audio) or b""
            self.server.wire.append(
                WireEvent(
                    session_id=self.session_id,
                    generation=self.generation,
                    kind="realtime_audio",
                    audio_bytes=len(data),
                )
            )
            if self._active is None:
                if self._utterances:
                    self._active = self._utterances.pop(0)
                else:
                    return  # audio sans parole programmée : la VAD l'ignore.
            text, chunks_left = self._active
            chunks_left -= 1
            if chunks_left <= 0:
                self._active = None
                # Deux fragments : le pipeline doit fusionner (extend_user_message).
                # La coupure se fait à une frontière de MOT (comme une vraie
                # transcription partielle) pour que la fusion opérée par Jarvis
                # (« frag1 + " " + frag2 ») redonne l'énoncé exact.  À défaut
                # d'espace après la moitié, on coupe au milieu ET on nettoie
                # les bords : une coupure adjacente à une espace doit quand
                # même se reconstruire exactement (l'espace est celle de la
                # fusion).  Une coupure en plein mot reste en plein mot — c'est
                # le cas voulu pour exercer extend_user_message.
                half = max(1, len(text) // 2)
                space = text.find(" ", half)
                if space != -1:
                    first, second = text[:space], text[space + 1 :]
                else:
                    first, second = text[:half], text[half:]
                await self._emit(_Message(_ServerContent(user_text=first.rstrip())))
                await self._emit(_Message(_ServerContent(user_text=second.lstrip())))
                await self._finish_utterance(text)
            else:
                self._active = (text, chunks_left)
            return

    async def send_tool_response(self, function_responses: Any) -> None:
        responses = function_responses if isinstance(function_responses, list) else [function_responses]
        names: list[str] = []
        for response in responses:
            name = getattr(response, "name", None) or "outil"
            names.append(name)
            payload = getattr(response, "response", None) or {}
            self.committed.append(
                {
                    "role": "user",
                    "parts": [
                        {"functionResponse": {"name": name, "response": payload}}
                    ],
                }
            )
        self.server.wire.append(
            WireEvent(
                session_id=self.session_id,
                generation=self.generation,
                kind="tool_response",
                tool_names=names,
            )
        )
        # IMPORTANT : le tour doit TOUJOURS être continué après un tool call
        # (comportement Gemini réel : send_tool_response déclenche la suite du
        # tour).  ``tool_continuation`` n'est qu'un override optionnel du texte
        # produit, pas une condition de continuation.
        continuation, self.server.tool_continuation = self.server.tool_continuation, None
        await self._generate(answer_override=continuation)

    # -- pilotage depuis les tests ----------------------------------------------

    def queue_utterance(self, text: str, chunks: int = 3) -> None:
        """Programme ce que « dit » l'utilisateur pour les prochains chunks audio."""
        self._utterances.append((text, chunks))

    async def _finish_utterance(self, text: str) -> None:
        """Fin de parole (VAD) : commit du tour audio + génération."""
        self.initial_phase = False
        self.committed.append({"role": "user", "parts": [{"text": text}]})
        await self._generate()

    def visible(self) -> list[tuple[str, str]]:
        """Contexte visible pour la génération : paires (role, texte).

        Les ``pending`` (clientContent non clôturé) sont volontairement ABSENTS
        sur la sémantique 2.x : c'est le comportement réel rapporté (thread
        111617) que le harness doit reproduire pour détecter la régression.
        """
        pairs: list[tuple[str, str]] = []
        for content in self.committed:
            for part in content.get("parts", []):
                if part.get("text"):
                    pairs.append((content.get("role", "user"), part["text"]))
                elif "functionResponse" in part:
                    fr = part["functionResponse"]
                    pairs.append(
                        ("user", _render_function_response(fr.get("name"), fr.get("response")))
                    )
        return pairs

    async def _generate(
        self, recap: bool = False, answer_override: str | None = None
    ) -> None:
        visible = self.visible()
        if self.server.empty_generations_remaining > 0:
            # Bug serveur simulé (cf. FakeLiveServer.empty_generations_remaining) :
            # un model_turn arrive (TTS_START se déclenche côté client) mais ne
            # porte ni audio ni transcription exploitable, et rien n'est committé
            # côté serveur -- Jarvis doit alors relancer avec le même texte.
            self.server.empty_generations_remaining -= 1
            empty_content = _ServerContent()
            empty_content.model_turn = _ModelTurn([_Part(text="(generation vide simulee)")])
            await self._emit(_Message(empty_content))
            await self._emit(msg_turn_complete())
            return
        if recap:
            # Récapitulatif après un seed 2.x (inférence forcée) : le modèle
            # confirme qu'il a le contexte. Réponse déterministe.
            answer = "Parfait, j'ai bien notre conversation en tête."
        elif answer_override is not None:
            answer = answer_override
        else:
            question = visible[-1][1] if visible else ""
            script = self.server.next_tool_script
            if script is not None and re.search(script[0], question, re.IGNORECASE):
                self.server.next_tool_script = None
                name, args = script[1], script[2]
                await self._emit(
                    _Message(
                        tool_call=_ToolCall(
                            [_FunctionCall(name, args, uuid.uuid4().hex[:8])]
                        )
                    )
                )
                return  # la génération reprend à la réception du tool_response
            answer, _ok = self.server.oracle.answer(question, visible)
        self.model_outputs.append(answer)
        # Fragments de transcription de sortie + audio + fin de tour.
        first, second = _split_fragments(answer, 2)
        await self._emit(_Message(_ServerContent(model_text=first)))
        if self.server.interrupt_next_turn:
            # Interruption scriptable : le modèle est coupé après le premier
            # fragment (sequence réelle : interrupted puis turn_complete).
            self.server.interrupt_next_turn = False
            self.committed.append({"role": "model", "parts": [{"text": first}]})
            await self._emit(msg_interrupted())
            await self._emit(msg_turn_complete())
            return
        await self._emit(_Message(_ServerContent(model_text=second, audio=b"\x00\x01" * 32)))
        await self._emit(msg_turn_complete())
        self.committed.append({"role": "model", "parts": [{"text": answer}]})
        if not self.server.suppress_resumption_updates:
            handle = uuid.uuid4().hex[:12]
            self.server.handles[handle] = [dict(c) for c in self.committed]
            self.last_handle = handle
            await self._emit(_Message(session_resumption_update=_ResumptionUpdate(handle, True)))


class _FakeCtx:
    def __init__(self, session: FakeLiveSession) -> None:
        self.session = session
        self.exited = False

    async def __aenter__(self) -> FakeLiveSession:
        return self.session

    async def __aexit__(self, *args: Any) -> bool:
        self.exited = True
        self.session.close()
        return False


class _FakeLive:
    def __init__(self, server: "FakeLiveServer") -> None:
        self.server = server

    def connect(self, model: str, config: Any) -> _FakeCtx:
        self.server.last_model = model
        self.server.last_config = config
        self.server.configs[self.server.generation + 1] = config
        self.server.connect_count += 1
        handle = _resumption_handle(config)
        restored: list[dict[str, Any]] = []
        if handle:
            if handle in self.server.handles and handle not in self.server.invalidated:
                restored = self.server.handles[handle]
                self.server.resumed_count += 1
            else:
                # Constat googleapis/python-genai#2197 : handle invalide => erreur.
                raise genai_errors.APIError(
                    1007, {"error": {"message": "Invalid session handle"}}
                )
        self.server.generation += 1
        session = FakeLiveSession(self.server, uuid.uuid4().hex[:8], self.server.generation)
        session.committed = [dict(c) for c in restored]
        self.server.sessions.append(session)
        return _FakeCtx(session)


class _FakeAio:
    def __init__(self, live: _FakeLive) -> None:
        self.live = live


class FakeClient:
    """Remplace ``genai.Client`` : ``client.aio.live.connect(...)``."""

    def __init__(self, server: "FakeLiveServer") -> None:
        self.aio = _FakeAio(_FakeLive(server))


# ---------------------------------------------------------------------------
# Serveur : registre de handles, sémantiques configurables
# ---------------------------------------------------------------------------


class FakeLiveServer:
    """Serveur Live factice, pilotable et observable depuis les tests.

    Sémantiques :
    * ``"2.x"`` (défaut, modèles 2.0/2.5 audio) : un ``clientContent`` en
      attente n'est pas rappelé par l'audio ; un seed ``turnComplete=true``
      commite et déclenche une inférence s'il se termine par un tour user ;
    * ``"3.x"`` : ``historyConfig`` requis pour le seeding ; historique committé
      sans appel modèle.
    """

    def __init__(self, semantics: str = "2.x") -> None:
        self.semantics = semantics
        self.handles: dict[str, list[dict[str, Any]]] = {}
        self.invalidated: set[str] = set()
        self.sessions: list[FakeLiveSession] = []
        self.wire: list[WireEvent] = []
        self.generation = 0
        self.connect_count = 0
        self.resumed_count = 0
        self.last_model: str | None = None
        self.last_config: Any = None
        #: Config de setup reçue À CHAQUE connexion, indexée par génération
        #: (permet de vérifier le câble de chaque session séparément).
        self.configs: dict[int, Any] = {}
        self.oracle = SemanticOracle()
        #: Script d'outil : (regex sur la demande, nom, args). Consommé une fois.
        self.next_tool_script: tuple[str, str, dict[str, Any]] | None = None
        #: Réponse forcée après un tool_response (continuation du tour).
        self.tool_continuation: str | None = None
        #: Coupe le prochain tour du modèle après le premier fragment.
        self.interrupt_next_turn = False
        #: N'envoie jamais de session_resumption_update (simule une coupure
        #: avant le premier handle : Jarvis doit alors rejouer son contexte).
        self.suppress_resumption_updates = False
        #: Simule le bug serveur Gemini documenté (googleapis/python-genai#2117,
        #: cf. CHANGELOG/EMPTY_GENERATION_RETRY) : les N prochaines générations
        #: DÉCLENCHÉES PAR UN TOUR UTILISATEUR réel (jamais le tour de rejeu de
        #: contexte, ``recap=True``) renvoient un ``model_turn`` contenant
        #: seulement une part texte inexploitée (``has_text_part=True``,
        #: ``has_inline_audio=False``, aucun ``output_transcription``) puis
        #: ``turn_complete`` SANS rien committer côté serveur -- exactement la
        #: signature observée en validation réelle (g2-t8, docs/
        #: RAPPORT_RECONNEXION_PAR_TOUR_v1.7.4.md §13.1). Décrémenté à chaque
        #: génération consommée ; 0 par défaut (aucun effet sur les tests
        #: existants).
        self.empty_generations_remaining = 0
        self._live: list[FakeLiveSession] = []

    def _register(self, session: FakeLiveSession) -> None:
        self._live.append(session)

    # -- contrôle depuis les tests ---------------------------------------------

    @property
    def current(self) -> FakeLiveSession:
        assert self._live, "aucune session ouverte"
        return self._live[-1]

    def invalidate_all_handles(self) -> None:
        """Simule l'expiration des handles (au-delà de 2 h, par exemple)."""
        self.invalidated |= set(self.handles)

    def drop_connection(self) -> None:
        """Coupure réseau brutale : la session courante se ferme."""
        if self._live:
            self._live[-1].close()

    async def send_goaway(self) -> None:
        await self.current._emit(_Message(go_away=_GoAway("5s")))

    def events(self, kind: str | None = None, generation: int | None = None) -> list[WireEvent]:
        return [
            event
            for event in self.wire
            if (kind is None or event.kind == kind)
            and (generation is None or event.generation == generation)
        ]

    def client_content_events(self, generation: int | None = None) -> list[WireEvent]:
        return self.events("client_content", generation)

    def first_realtime_generation(self) -> int | None:
        for event in self.wire:
            if event.kind in ("realtime_audio", "realtime_text"):
                return event.generation
        return None
