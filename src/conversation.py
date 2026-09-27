"""Contexte conversationnel multi-tour de Jarvis (v1.6.0).

Pourquoi ce module existe
-------------------------
Jusqu'à la v1.5.3, Jarvis ne possédait **aucune** représentation locale de la
conversation. Le pipeline vocal était :

``micro -> Gemini Live (session WebSocket) -> audio``

La continuité entre deux phrases reposait donc uniquement sur l'état implicite
de la session côté serveur : dès qu'une session était recréée (perte réseau,
changement de voix, expiration du handle de reprise, redémarrage), le modèle
repartait de zéro et « le deuxième », « mets-la en pause », « plus court »
n'avaient plus de référent. Aucun test ne pouvait détecter le problème
puisqu'il n'existait rien à tester.

Ce module fournit la pièce manquante : une **représentation structurée,
locale, bornée et testable** de la conversation en cours.

Trois contextes distincts (à ne jamais confondre)
-------------------------------------------------
1. **System prompt / identité** — construit dans ``src/gemini_live.py``
   (personnalité, règles, outils). Il n'est PAS stocké ici : il est fourni
   aux adaptateurs au moment de la conversion.
2. **Contexte conversationnel** — ce module. Durée de vie : la session Jarvis
   en cours. Jamais persisté sur disque, remis à zéro au démarrage.
3. **Mémoire persistante** — ``src/memory.py`` (SQLite). Modifiée uniquement
   par les outils ``remember``/``forget``/… et l'extraction conservatrice
   existante. Une réinitialisation de conversation n'y touche JAMAIS.

Limitation de taille
--------------------
Deux critères cumulés, volontairement simples (les modèles réellement utilisés
par Jarvis — Gemini Live natif audio — offrent une large fenêtre, et un
historique vocal est court) :

* ``max_turns`` : nombre de tours conservés (défaut 12, ``JARVIS_CONTEXT_MAX_TURNS``) ;
* ``max_tokens`` : budget estimé (défaut 3000, ``JARVIS_CONTEXT_MAX_TOKENS``).

Le rognage supprime les tours **les plus anciens et en entier** (message
utilisateur + appels d'outils + réponse), ce qui évite tout état incohérent
(résultat d'outil orphelin). Aucun mécanisme de résumé n'est implémenté :
une fenêtre glissante suffit largement pour un assistant vocal, et un résumé
imposerait un appel LLM supplémentaire à chaque tour.

Concurrence
-----------
Le pipeline est vocal et asynchrone (boucle asyncio + threads audio + outils
exécutés dans des threads). Toutes les mutations passent donc par un
``threading.RLock`` : l'ordre d'insertion est celui des appels, et deux
requêtes simultanées ne peuvent pas entrelacer un tour.
"""

from __future__ import annotations

import os
import re
import threading
import time
import unicodedata
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Sequence

from .logging_setup import get_logger

log = get_logger("conversation")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_TOOL = "tool"
ROLE_SYSTEM = "system"

KIND_TEXT = "text"
KIND_TOOL_CALL = "tool_call"
KIND_TOOL_RESULT = "tool_result"

#: Fournisseurs LLM connus du convertisseur (voir ``messages_for_provider``).
PROVIDER_GEMINI = "gemini"
PROVIDER_OLLAMA = "ollama"
DEFAULT_PROVIDER = PROVIDER_GEMINI

#: Nombre de tours conservés par défaut (un tour = une demande + sa réponse).
DEFAULT_MAX_TURNS = 12

#: Budget de contexte estimé, en tokens (heuristique 4 caractères = 1 token).
DEFAULT_MAX_TOKENS = 3000

#: Longueur maximale d'un message texte conservé (une réponse très longue est
#: tronquée : le contexte sert à référencer, pas à archiver).
MAX_TEXT_CHARS = 1200

#: Longueur maximale de la représentation compacte d'un résultat d'outil.
MAX_TOOL_RESULT_CHARS = 420

#: Nombre d'éléments conservés par liste dans un résultat d'outil (« le
#: deuxième résultat » doit rester résoluble sans injecter tout le JSON).
MAX_TOOL_RESULT_ITEMS = 5

#: Heuristique de conversion caractères -> tokens, suffisante pour borner.
CHARS_PER_TOKEN = 4

_LABEL_KEYS = ("label", "title", "name", "nom", "content", "text", "query")
_SECONDARY_KEYS = ("artist", "artiste", "owner", "album", "category", "description")


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, str(default)))))
    except (TypeError, ValueError):
        return default


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "non"}


def estimate_tokens(text: str) -> int:
    """Estimation volontairement grossière mais stable du coût d'un texte."""
    length = len(str(text or ""))
    if length == 0:
        return 0
    return max(1, (length + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN)


def _clean_text(value: Any, limit: int = MAX_TEXT_CHARS) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


# ---------------------------------------------------------------------------
# Message
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Message:
    """Un élément du contexte conversationnel (immuable).

    ``turn`` identifie le tour : tous les messages produits entre deux
    demandes utilisateur portent le même numéro, ce qui permet de rogner par
    tours entiers.
    """

    role: str
    text: str
    kind: str = KIND_TEXT
    name: str | None = None
    turn: int = 0
    failed: bool = False
    tokens: int = 0
    data: dict[str, Any] | None = None
    created_at: float = field(default_factory=time.time, compare=False)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "role": self.role,
            "kind": self.kind,
            "text": self.text,
            "turn": self.turn,
        }
        if self.name:
            payload["name"] = self.name
        if self.failed:
            payload["failed"] = True
        return payload


def _make_message(role: str, text: str, *, kind: str = KIND_TEXT, name: str | None = None,
                  turn: int = 0, data: dict[str, Any] | None = None) -> Message:
    return Message(
        role=role,
        text=text,
        kind=kind,
        name=name,
        turn=turn,
        tokens=estimate_tokens(text) + 2,  # +2 : surcoût de balisage du rôle
        data=data,
    )


# ---------------------------------------------------------------------------
# Compactage des interactions d'outils
# ---------------------------------------------------------------------------


def compact_tool_args(args: Any, *, max_chars: int = 160) -> dict[str, Any]:
    """Réduit les arguments d'un appel d'outil à quelque chose de citable."""
    if not isinstance(args, dict):
        return {}
    compact: dict[str, Any] = {}
    for key, value in list(args.items())[:8]:
        if isinstance(value, (int, float, bool)) or value is None:
            compact[str(key)] = value
        else:
            compact[str(key)] = _clean_text(value, max_chars)
    return compact


def _item_label(item: Any) -> str:
    if isinstance(item, dict):
        main = ""
        for key in _LABEL_KEYS:
            value = item.get(key)
            if isinstance(value, (str, int, float)) and str(value).strip():
                main = str(value).strip()
                break
        if not main:
            main = ", ".join(
                f"{k}={v}" for k, v in list(item.items())[:2] if isinstance(v, (str, int, float))
            )
        for key in _SECONDARY_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value.strip() and value.strip() not in main:
                return f"{main} — {value.strip()}"
        return main
    return _clean_text(item, 80)


def _format_items(label: str, items: Sequence[Any], max_items: int) -> str:
    shown = [f"{index}. {_item_label(item)}" for index, item in enumerate(items[:max_items], start=1)]
    suffix = f" (+{len(items) - max_items})" if len(items) > max_items else ""
    return f"{label} : " + " ; ".join(shown) + suffix


def summarize_tool_result(
    name: str,
    result: Any,
    *,
    max_chars: int = MAX_TOOL_RESULT_CHARS,
    max_items: int = MAX_TOOL_RESULT_ITEMS,
) -> str:
    """Représentation compacte et ordonnée d'un résultat d'outil.

    Objectif : qu'un tour suivant puisse dire « lance le deuxième » sans que
    l'intégralité du JSON (des centaines de lignes Deezer) ne soit réinjectée
    dans chaque requête LLM. Les listes conservent donc leur ORDRE et sont
    numérotées, mais bornées en nombre d'éléments et en caractères.
    """
    if not isinstance(result, dict):
        return _clean_text(result, max_chars)

    parts: list[str] = []
    success = result.get("success")
    if success is True:
        parts.append("ok")
    elif success is False:
        parts.append("échec")

    for key in ("message", "error", "raison", "reason"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(_clean_text(value, 180))
            break

    def _collect(container: dict[str, Any], depth: int) -> None:
        for key, value in container.items():
            if key in {"success", "message", "error", "raison", "reason", "provider"}:
                continue
            if isinstance(value, list) and value:
                parts.append(_format_items(str(key), value, max_items))
            elif isinstance(value, dict) and depth < 1:
                _collect(value, depth + 1)
            elif isinstance(value, (str, int, float, bool)) and str(value).strip():
                if key in {"query", "action", "track", "artist", "album", "playlist", "titre",
                           "state", "etat", "mode", "count", "volume"}:
                    parts.append(f"{key}={_clean_text(value, 80)}")

    _collect(result, 0)

    text = " | ".join(part for part in parts if part)
    if not text:
        text = "résultat sans contenu exploitable"
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return f"{name} -> {text}"


def format_tool_call(name: str, args: Any) -> str:
    compact = compact_tool_args(args)
    if not compact:
        return f"{name}()"
    rendered = ", ".join(f"{key}={value!r}" for key, value in compact.items())
    return _clean_text(f"{name}({rendered})", 240)


# ---------------------------------------------------------------------------
# Adaptateurs de fournisseurs
# ---------------------------------------------------------------------------


def to_gemini_contents(messages: Iterable[Message]) -> list[dict[str, Any]]:
    """Convertit le contexte au format natif Gemini (``Content`` / ``parts``).

    Gemini ne connaît que deux rôles conversationnels (``user`` et ``model``).
    Les interactions d'outils sont donc rattachées au rôle qui les produit :
    l'appel côté ``model``, le résultat côté ``user`` (c'est le canal par
    lequel l'environnement répond au modèle). Les messages consécutifs de même
    rôle sont fusionnés en un seul ``Content`` **à plusieurs parts** : on
    respecte l'alternance attendue par l'API sans jamais concaténer bêtement
    des chaînes.
    """
    contents: list[dict[str, Any]] = []
    for message in messages:
        if not message.text:
            continue
        if message.role == ROLE_ASSISTANT:
            role = "model"
            text = message.text
        elif message.role == ROLE_TOOL:
            if message.kind == KIND_TOOL_CALL:
                role = "model"
                text = f"[appel outil] {message.text}"
            else:
                role = "user"
                text = f"[résultat outil] {message.text}"
        else:
            role = "user"
            text = message.text
        if contents and contents[-1]["role"] == role:
            contents[-1]["parts"].append({"text": text})
        else:
            contents.append({"role": role, "parts": [{"text": text}]})
    return contents


def to_ollama_messages(
    messages: Iterable[Message],
    *,
    system_prompt: str | None = None,
) -> list[dict[str, Any]]:
    """Convertit le contexte au format ``messages`` d'Ollama / OpenAI.

    Rôles natifs : ``system``, ``user``, ``assistant`` (avec ``tool_calls``) et
    ``tool``. La conversation est donc représentée avec la même structure que
    côté Gemini — seule la mise en forme change, jamais la notion de
    conversation, qui reste la propriété de Jarvis.
    """
    payload: list[dict[str, Any]] = []
    if system_prompt:
        payload.append({"role": ROLE_SYSTEM, "content": str(system_prompt)})
    for message in messages:
        if not message.text and message.kind != KIND_TOOL_CALL:
            continue
        if message.role == ROLE_USER:
            payload.append({"role": ROLE_USER, "content": message.text})
        elif message.role == ROLE_ASSISTANT:
            payload.append({"role": ROLE_ASSISTANT, "content": message.text})
        elif message.kind == KIND_TOOL_CALL:
            payload.append(
                {
                    "role": ROLE_ASSISTANT,
                    "content": "",
                    "tool_calls": [
                        {
                            "type": "function",
                            "function": {
                                "name": message.name or "",
                                "arguments": dict((message.data or {}).get("args", {})),
                            },
                        }
                    ],
                }
            )
        else:
            payload.append(
                {"role": ROLE_TOOL, "name": message.name or "", "content": message.text}
            )
    return payload


#: Table des adaptateurs. Ajouter un fournisseur = ajouter une entrée ici.
PROVIDER_ADAPTERS: dict[str, Callable[..., list[dict[str, Any]]]] = {
    PROVIDER_GEMINI: lambda messages, system_prompt=None: to_gemini_contents(messages),
    PROVIDER_OLLAMA: lambda messages, system_prompt=None: to_ollama_messages(
        messages, system_prompt=system_prompt
    ),
}


def normalize_provider(value: Any, default: str = DEFAULT_PROVIDER) -> str:
    name = str(value or "").strip().lower()
    return name if name in PROVIDER_ADAPTERS else default


# ---------------------------------------------------------------------------
# Contexte conversationnel
# ---------------------------------------------------------------------------


class ConversationContext:
    """Mémoire de travail d'une conversation Jarvis (jamais persistée).

    API publique volontairement étroite : le reste de Jarvis n'accède jamais
    à la liste interne des messages.
    """

    def __init__(
        self,
        *,
        max_turns: int | None = None,
        max_tokens: int | None = None,
        provider: str = DEFAULT_PROVIDER,
        enabled: bool | None = None,
        conversation_id: str | None = None,
    ) -> None:
        self.max_turns = (
            int(max_turns)
            if max_turns is not None
            else _env_int("JARVIS_CONTEXT_MAX_TURNS", DEFAULT_MAX_TURNS, 1, 200)
        )
        self.max_tokens = (
            int(max_tokens)
            if max_tokens is not None
            else _env_int("JARVIS_CONTEXT_MAX_TOKENS", DEFAULT_MAX_TOKENS, 200, 200_000)
        )
        self.enabled = _env_bool("JARVIS_CONTEXT_ENABLED", True) if enabled is None else bool(enabled)

        self._lock = threading.RLock()
        self._messages: list[Message] = []
        self._provider = normalize_provider(provider)
        self._conversation_id = conversation_id or uuid.uuid4().hex[:8]
        self._started_at = time.time()
        self._turn = 0
        self._open_turn = False
        self._epoch = 0
        self._trimmed = 0
        self._session_handler: Callable[[dict[str, Any]], None] | None = None

        log.debug(
            "contexte créé id=%s provider=%s max_turns=%s max_tokens=%s enabled=%s",
            self._conversation_id, self._provider, self.max_turns, self.max_tokens, self.enabled,
        )

    # -- état ----------------------------------------------------------------

    @property
    def conversation_id(self) -> str:
        return self._conversation_id

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def epoch(self) -> int:
        """Numéro de génération : incrémenté à chaque nouvelle conversation."""
        return self._epoch

    @property
    def started_at(self) -> float:
        return self._started_at

    def size(self) -> int:
        """Nombre de messages conservés (utilisateur, assistant, outils)."""
        with self._lock:
            return len(self._messages)

    def turn_count(self) -> int:
        """Nombre de tours distincts conservés."""
        with self._lock:
            return len({message.turn for message in self._messages})

    def estimated_tokens(self) -> int:
        with self._lock:
            return sum(message.tokens for message in self._messages)

    def is_empty(self) -> bool:
        return self.size() == 0

    def has_open_turn(self) -> bool:
        """Vrai si une demande utilisateur attend encore sa réponse."""
        with self._lock:
            return self._open_turn

    # -- ajout de messages ---------------------------------------------------

    def add_user_message(self, text: str) -> Message | None:
        """Nouveau tour : la demande de l'utilisateur."""
        cleaned = _clean_text(text)
        if not cleaned or not self.enabled:
            return None
        with self._lock:
            self._turn += 1
            self._open_turn = True
            message = _make_message(ROLE_USER, cleaned, turn=self._turn)
            self._messages.append(message)
            self._trim_locked()
            self._log_state("user", message)
            return message

    def extend_user_message(self, text: str) -> Message | None:
        """Complète la demande en cours (transcription vocale incrémentale).

        La transcription d'un tour arrive par fragments ; le premier fragment
        crée le message, les suivants le complètent — sans jamais casser
        l'ordre déjà établi (un appel d'outil peut avoir été inséré entre
        temps).
        """
        cleaned = _clean_text(text)
        if not cleaned or not self.enabled:
            return None
        with self._lock:
            for index in range(len(self._messages) - 1, -1, -1):
                message = self._messages[index]
                if message.role != ROLE_USER or message.kind != KIND_TEXT:
                    continue
                if message.turn != self._turn:
                    break
                merged = _clean_text(f"{message.text} {cleaned}")
                updated = replace(message, text=merged, tokens=estimate_tokens(merged) + 2)
                self._messages[index] = updated
                self._trim_locked()
                return updated
            return self.add_user_message(cleaned)

    def add_assistant_message(self, text: str) -> Message | None:
        """Réponse de Jarvis : clôt le tour en cours."""
        cleaned = _clean_text(text)
        if not cleaned or not self.enabled:
            return None
        with self._lock:
            turn = self._turn or 1
            self._turn = turn
            message = _make_message(ROLE_ASSISTANT, cleaned, turn=turn)
            self._messages.append(message)
            self._open_turn = False
            self._trim_locked()
            self._log_state("assistant", message)
            return message

    def add_tool_call(self, name: str, args: Any = None) -> Message | None:
        """Appel d'outil décidé par le modèle, rattaché au tour courant."""
        tool = str(name or "").strip()
        if not tool or not self.enabled:
            return None
        with self._lock:
            turn = self._turn or 1
            self._turn = turn
            message = _make_message(
                ROLE_TOOL,
                format_tool_call(tool, args),
                kind=KIND_TOOL_CALL,
                name=tool,
                turn=turn,
                data={"args": compact_tool_args(args)},
            )
            self._messages.append(message)
            self._trim_locked()
            self._log_state("tool_call", message)
            return message

    def add_tool_result(self, name: str, result: Any) -> Message | None:
        """Résultat d'outil, compacté (jamais le payload JSON complet)."""
        tool = str(name or "").strip()
        if not tool or not self.enabled:
            return None
        with self._lock:
            turn = self._turn or 1
            self._turn = turn
            message = _make_message(
                ROLE_TOOL,
                summarize_tool_result(tool, result),
                kind=KIND_TOOL_RESULT,
                name=tool,
                turn=turn,
            )
            self._messages.append(message)
            self._trim_locked()
            self._log_state("tool_result", message)
            return message

    def add_tool_interaction(self, name: str, args: Any, result: Any) -> tuple[Message | None, Message | None]:
        """Raccourci : appel + résultat, dans l'ordre, sans état intermédiaire."""
        with self._lock:
            return self.add_tool_call(name, args), self.add_tool_result(name, result)

    def close_turn(self, reason: str = "") -> bool:
        """Clôt le tour courant sans réponse texte (tour outil ou audio seul).

        Un tour peut se terminer normalement sans que Jarvis ait produit du
        texte : il a par exemple seulement exécuté « monte le son ». Le tour
        n'est alors ni ouvert ni échoué — il est simplement terminé, et une
        coupure ultérieure ne doit pas le marquer en échec.
        """
        with self._lock:
            if not self._open_turn:
                return False
            self._open_turn = False
            log.debug(
                "tour clos id=%s turn=%s raison=%s",
                self._conversation_id, self._turn, reason or "fin de tour",
            )
            return True

    def fail_open_turn(self, reason: str = "") -> bool:
        """Marque le tour en cours comme échoué (appel LLM ou outil perdu).

        Comportement retenu après audit : le message utilisateur est
        **conservé** (« reprends », « et sa population ? » doivent continuer à
        fonctionner après une simple coupure réseau) mais il est marqué
        ``failed`` — l'état est donc explicite, déterministe et inspectable,
        jamais un demi-tour fantôme.
        """
        with self._lock:
            if not self._open_turn:
                return False
            marked = False
            for index in range(len(self._messages) - 1, -1, -1):
                message = self._messages[index]
                if message.turn != self._turn:
                    break
                if message.role == ROLE_USER and message.kind == KIND_TEXT:
                    self._messages[index] = replace(message, failed=True)
                    marked = True
                    break
            self._open_turn = False
            log.debug(
                "tour échoué id=%s turn=%s marque=%s raison=%s",
                self._conversation_id, self._turn, marked, reason or "inconnue",
            )
            return marked

    # -- lecture -------------------------------------------------------------

    def get_messages(self, limit: int | None = None) -> tuple[Message, ...]:
        """Messages conservés, du plus ancien au plus récent (lecture seule)."""
        with self._lock:
            messages = tuple(self._messages)
        if limit is not None and limit >= 0:
            return messages[-limit:] if limit else ()
        return messages

    def messages_for_provider(
        self,
        provider: str | None = None,
        *,
        system_prompt: str | None = None,
    ) -> list[dict[str, Any]]:
        """Contexte converti au format natif du fournisseur demandé."""
        name = normalize_provider(provider or self._provider)
        adapter = PROVIDER_ADAPTERS[name]
        messages = self.get_messages()
        converted = adapter(messages, system_prompt=system_prompt)
        log.debug(
            "conversion contexte id=%s provider=%s messages=%s sortie=%s tokens~%s",
            self._conversation_id, name, len(messages), len(converted), self.estimated_tokens(),
        )
        return converted

    def snapshot(self) -> dict[str, Any]:
        """Instantané non sensible : métriques, jamais le contenu des échanges."""
        with self._lock:
            roles: dict[str, int] = {}
            for message in self._messages:
                roles[message.role] = roles.get(message.role, 0) + 1
            return {
                "conversation_id": self._conversation_id,
                "provider": self._provider,
                "epoch": self._epoch,
                "messages": len(self._messages),
                "turns": len({message.turn for message in self._messages}),
                "estimated_tokens": sum(message.tokens for message in self._messages),
                "max_turns": self.max_turns,
                "max_tokens": self.max_tokens,
                "trimmed_messages": self._trimmed,
                "open_turn": self._open_turn,
                "enabled": self.enabled,
                "roles": roles,
                "started_at": self._started_at,
            }

    def describe(self) -> str:
        data = self.snapshot()
        return (
            f"conversation {data['conversation_id']} — {data['turns']} tour(s), "
            f"{data['messages']} message(s), ~{data['estimated_tokens']} tokens, "
            f"provider {data['provider']}"
        )

    # -- cycle de vie --------------------------------------------------------

    def start_new_conversation(self, reason: str = "") -> dict[str, Any]:
        """Vide le contexte conversationnel et ouvre une nouvelle conversation.

        Ne touche ni à la configuration, ni à la mémoire persistante, ni aux
        préférences, ni à l'authentification : seule la conversation en cours
        disparaît.
        """
        with self._lock:
            previous_id = self._conversation_id
            previous_turns = len({message.turn for message in self._messages})
            previous_messages = len(self._messages)
            self._messages.clear()
            self._turn = 0
            self._open_turn = False
            self._epoch += 1
            self._trimmed = 0
            self._conversation_id = uuid.uuid4().hex[:8]
            self._started_at = time.time()
            handler = self._session_handler
            info = {
                "conversation_id": self._conversation_id,
                "previous_conversation_id": previous_id,
                "previous_turns": previous_turns,
                "previous_messages": previous_messages,
                "epoch": self._epoch,
                "provider": self._provider,
                "reason": reason or "",
            }
        log.info(
            "nouvelle conversation id=%s (précédente %s : %s tour(s)) raison=%s",
            info["conversation_id"], previous_id, previous_turns, reason or "non précisée",
        )
        if handler is not None:
            # Hors verrou : le pont provider peut demander une reconnexion.
            try:
                handler(dict(info))
            except Exception as exc:  # pragma: no cover - défensif
                log.debug("handler de reset en échec : %s", exc)
        return info

    #: Alias explicites : les appelants utilisent le nom qui leur parle.
    def reset(self, reason: str = "") -> dict[str, Any]:
        return self.start_new_conversation(reason=reason)

    def clear(self, reason: str = "") -> dict[str, Any]:
        return self.start_new_conversation(reason=reason)

    def set_provider(self, provider: str) -> str:
        """Change de fournisseur LLM **sans perdre la conversation**.

        Le contexte appartient à Jarvis, pas au fournisseur : passer de Gemini
        à Ollama (et inversement) ne fait que changer l'adaptateur de sortie.
        """
        name = normalize_provider(provider, self._provider)
        with self._lock:
            previous, self._provider = self._provider, name
        if previous != name:
            log.info(
                "changement de fournisseur %s -> %s (conversation %s conservée : %s message(s))",
                previous, name, self._conversation_id, self.size(),
            )
        return previous

    def bind_session(self, handler: Callable[[dict[str, Any]], None] | None) -> None:
        """Associe la session LLM courante (au plus une) au contexte.

        Le handler est appelé après ``start_new_conversation`` pour que le
        fournisseur puisse repartir d'une session vierge côté serveur. Le
        « dernier lié gagne » : il n'y a jamais d'accumulation d'écouteurs.
        """
        with self._lock:
            self._session_handler = handler

    def unbind_session(self, handler: Callable[[dict[str, Any]], None] | None = None) -> None:
        with self._lock:
            # ``==`` et non ``is`` : une méthode liée (``self._handle_reset``)
            # crée un nouvel objet à chaque accès, mais deux méthodes liées de
            # la même instance sont égales.
            if handler is None or self._session_handler == handler:
                self._session_handler = None

    # -- interne -------------------------------------------------------------

    def _log_state(self, event: str, message: Message) -> None:
        # DEBUG utile au diagnostic (« pourquoi Jarvis a-t-il oublié ? ») sans
        # jamais recopier le contenu des échanges dans le journal.
        log.debug(
            "%s id=%s turn=%s role=%s kind=%s outil=%s chars=%s tokens~%s messages=%s",
            event, self._conversation_id, message.turn, message.role, message.kind,
            message.name or "-", len(message.text), self.estimated_tokens(), len(self._messages),
        )

    def _trim_locked(self) -> None:
        """Rogne par tours entiers. Appelé sous verrou uniquement."""
        removed = 0

        def _turns() -> list[int]:
            seen: list[int] = []
            for message in self._messages:
                if message.turn not in seen:
                    seen.append(message.turn)
            return seen

        turns = _turns()
        while len(turns) > self.max_turns:
            oldest = turns[0]
            before = len(self._messages)
            self._messages = [m for m in self._messages if m.turn != oldest]
            removed += before - len(self._messages)
            turns = _turns()

        total = sum(message.tokens for message in self._messages)
        while total > self.max_tokens and len(turns) > 1:
            oldest = turns[0]
            before = len(self._messages)
            self._messages = [m for m in self._messages if m.turn != oldest]
            removed += before - len(self._messages)
            turns = _turns()
            total = sum(message.tokens for message in self._messages)

        if removed:
            self._trimmed += removed
            log.debug(
                "rognage contexte id=%s messages_supprimes=%s tours_restants=%s tokens~%s",
                self._conversation_id, removed, len(turns), total,
            )


# ---------------------------------------------------------------------------
# Détection locale de « nouvelle conversation »
# ---------------------------------------------------------------------------


def _normalize(text: str) -> str:
    raw = unicodedata.normalize("NFD", str(text or "").lower())
    stripped = "".join(ch for ch in raw if unicodedata.category(ch) != "Mn")
    stripped = re.sub(r"[^a-z0-9]+", " ", stripped)
    return " ".join(stripped.split())


#: Formulations reconnues localement. Elles décrivent une INTENTION complète
#: (verbe + objet « conversation/contexte »), pas un simple mot-clé : « efface
#: la note » ou « nouvelle routine » ne déclenchent rien.
_RESET_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(nouvelle|nouvel|new)\s+(conversation|discussion|echange|dialogue|chat)\b"),
    re.compile(
        r"\b(commence|commencons|demarre|demarrons|ouvre|lance|start)\s+"
        r"(moi\s+)?(une\s+|un\s+|a\s+)?(nouvelle\s+|new\s+)?(conversation|discussion|chat)\b"
    ),
    # Verbes d'effacement uniquement : « reprenons la conversation » veut dire
    # l'inverse d'un reset et ne doit surtout pas vider le contexte.
    re.compile(
        r"\b(efface|effacer|oublie|oublier|vide|vider|reinitialise|reinitialiser|remet|remets|"
        r"remettre|reset|clear)\b[a-z0-9 ]{0,24}"
        r"\b(contexte|conversation|discussion|echange|historique|chat)\b"
    ),
    re.compile(r"\b(on\s+)?(repart|repartons|recommence|recommencons)\s+(de\s+)?zero\b"),
    re.compile(r"\btable\s+rase\b"),
)

#: Une formulation négative ne doit jamais déclencher le reset.
_NEGATION = re.compile(r"\b(ne|n|pas|jamais|surtout\s+pas|sans)\b[a-z0-9 ]{0,24}\b(efface|oublie|reinitialise|reset|vide|nouvelle)\b")


def is_new_conversation_command(text: str) -> bool:
    """Vrai si la phrase demande explicitement une nouvelle conversation.

    Volontairement basée sur des motifs normalisés (accents, ponctuation et
    casse ignorés) plutôt que sur un ``if "nouvelle conversation" in text``.
    """
    normalized = _normalize(text)
    if not normalized:
        return False
    if _NEGATION.search(normalized):
        return False
    return any(pattern.search(normalized) for pattern in _RESET_PATTERNS)


# ---------------------------------------------------------------------------
# Instance par défaut (même schéma que memory / modes / music)
# ---------------------------------------------------------------------------

_DEFAULT: ConversationContext | None = None
_DEFAULT_LOCK = threading.RLock()


def get_default_conversation_context() -> ConversationContext:
    """Contexte partagé par le pipeline vocal, les outils et l'interface."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = ConversationContext()
        return _DEFAULT


def set_default_conversation_context(context: ConversationContext | None) -> None:
    global _DEFAULT
    with _DEFAULT_LOCK:
        _DEFAULT = context


def start_new_conversation(reason: str = "") -> dict[str, Any]:
    """Réinitialise la conversation partagée (mémoire persistante intacte)."""
    return get_default_conversation_context().start_new_conversation(reason=reason)
