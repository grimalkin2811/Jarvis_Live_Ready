"""Tableau d'état runtime de Jarvis (thread-safe, sans Qt).

Pourquoi ce module
------------------
Avant le Dashboard, l'état réel du moteur vocal (connexion au modèle,
reconnexion, clé refusée, pipeline audio…) n'existait que sous forme de
``print()`` dans la boucle vocale (``src/ui.py``). Aucune interface ne pouvait
donc afficher « Jarvis est opérationnel » sans le deviner.

Ce tableau est la **source unique** de cet état :

* la boucle vocale y **publie des faits** (« connexion établie », « reconnexion
  dans 5 s », « clé refusée ») au moment exact où ils se produisent ;
* les interfaces (Dashboard aujourd'hui) le **lisent** via ``snapshot()`` ou
  s'abonnent via ``add_hook`` — jamais l'inverse ;
* la boucle vocale y enregistre aussi, le temps de sa vie, le point d'entrée
  thread-safe d'envoi de texte (``submit_text``) du moteur Gemini Live : le
  Dashboard peut converser par écrit avec la MÊME session, sans second moteur.

Contrat identique aux ponts existants (``menu_state.LIVE``,
``UI.interface_mode_bridge``) : importable depuis n'importe quel thread, aucune
exception ne remonte vers la boucle vocale, les hooks sont appelés hors verrou.
"""

from __future__ import annotations

import concurrent.futures
import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

log = logging.getLogger("jarvis.runtime_status")

# ---------------------------------------------------------------------------
# États du moteur
# ---------------------------------------------------------------------------


class EngineState:
    """États publiés par la boucle vocale (chaînes : sérialisables, comparables)."""

    #: Aucun fait publié : la boucle vocale n'a pas (encore) démarré.
    NOT_STARTED = "not_started"
    #: Chargement des composants (mémoire, audio, wake word…).
    STARTING = "starting"
    #: Ouverture d'une session Live en cours.
    CONNECTING = "connecting"
    #: Session Live ouverte ET prête (rejeu de contexte terminé).
    READY = "ready"
    #: Connexion perdue : nouvelle tentative programmée.
    RECONNECTING = "reconnecting"
    #: Clé API refusée : la boucle s'est arrêtée, intervention requise.
    AUTH_ERROR = "auth_error"
    #: Échec d'initialisation (import, audio…) : backend vocal indisponible.
    FAILED = "failed"
    #: Boucle vocale terminée (arrêt normal de Jarvis).
    STOPPED = "stopped"


ENGINE_LABELS: dict[str, str] = {
    EngineState.NOT_STARTED: "Non démarré",
    EngineState.STARTING: "Démarrage",
    EngineState.CONNECTING: "Connexion au modèle",
    EngineState.READY: "Connecté",
    EngineState.RECONNECTING: "Reconnexion",
    EngineState.AUTH_ERROR: "Clé API refusée",
    EngineState.FAILED: "Indisponible",
    EngineState.STOPPED: "Arrêté",
}

#: États pour lesquels une conversation (voix ou texte) est impossible.
UNAVAILABLE_STATES = frozenset(
    {
        EngineState.RECONNECTING,
        EngineState.AUTH_ERROR,
        EngineState.FAILED,
        EngineState.STOPPED,
    }
)

#: Synthèse globale affichée par le Dashboard. « operational » n'est JAMAIS
#: déduit sans preuve : il exige une session prête ET un pipeline audio démarré.
HEALTH_OPERATIONAL = "operational"
HEALTH_DEGRADED = "degraded"
HEALTH_UNAVAILABLE = "unavailable"
HEALTH_UNKNOWN = "unknown"

HEALTH_LABELS: dict[str, str] = {
    HEALTH_OPERATIONAL: "Opérationnel",
    HEALTH_DEGRADED: "Dégradé",
    HEALTH_UNAVAILABLE: "Indisponible",
    HEALTH_UNKNOWN: "Inconnu",
}


# ---------------------------------------------------------------------------
# Masquage des secrets
# ---------------------------------------------------------------------------

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Clés Google / Gemini (format AIza…).
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "AIza…[masqué]"),
    # Jetons porteurs et en-têtes d'autorisation.
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-~+/=]{8,}"), r"\1[masqué]"),
    # Paires clé=valeur sensibles (api_key=…, token: …, password=…).
    (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|passwd|authorization)"
            r"(\s*[=:]\s*)(['\"]?)[^\s'\",;]{4,}"
        ),
        r"\1\2\3[masqué]",
    ),
    # Paramètres d'URL sensibles (?key=…&access_token=…).
    (re.compile(r"(?i)([?&](?:key|access_token|token|sig|signature)=)[^&\s]+"), r"\1[masqué]"),
)


def redact_secrets(text: Any, *, limit: int = 2000) -> str:
    """Retire clés API, jetons et mots de passe d'un texte destiné à l'affichage.

    Utilisé pour TOUT texte d'erreur ou de journal montré par le Dashboard :
    une trace réseau peut contenir l'URL complète d'une requête, donc la clé.
    """
    value = str(text if text is not None else "")
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    if limit and len(value) > limit:
        value = value[: limit - 1].rstrip() + "…"
    return value


# ---------------------------------------------------------------------------
# Erreurs d'envoi de texte
# ---------------------------------------------------------------------------


class EngineUnavailableError(RuntimeError):
    """Aucun moteur capable de recevoir du texte n'est enregistré."""

    def __init__(self, message: str, *, code: str = "unavailable") -> None:
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------------------
# Tableau d'état
# ---------------------------------------------------------------------------

Hook = Callable[[dict[str, Any]], None]
TextHandler = Callable[[str], "concurrent.futures.Future[Any]"]


class RuntimeStatusBoard:
    """État runtime partagé entre la boucle vocale et les interfaces."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._hooks: list[Hook] = []
        self._text_handler: TextHandler | None = None
        self._revision = 0
        self._reset_locked()

    # -- état interne -------------------------------------------------------

    def _reset_locked(self) -> None:
        now = time.time()
        self._data: dict[str, Any] = {
            "engine_state": EngineState.NOT_STARTED,
            "engine_detail": "",
            "engine_since": now,
            "model": "",
            "session_generation": 0,
            "connected_since": None,
            "reconnect_attempts": 0,
            "last_error": "",
            "last_error_at": None,
            "audio_state": "unknown",  # unknown | ok | failed
            "audio_detail": "",
            "wake_word": "unknown",  # unknown | loaded | unavailable
            "wake_word_detail": "",
            "memory_state": "unknown",  # unknown | ok | disabled | failed
            "tasks_state": "unknown",  # unknown | ok | failed
            "tasks_detail": "",
            "text_input": False,
            "updated_at": now,
        }

    def reset(self) -> None:
        """Remet le tableau à l'état « non démarré » (tests, redémarrage)."""
        with self._lock:
            self._reset_locked()
            self._text_handler = None
            self._revision += 1
            snapshot = self._snapshot_locked()
        self._emit(snapshot)

    def _snapshot_locked(self) -> dict[str, Any]:
        data = dict(self._data)
        data["revision"] = self._revision
        data["text_input"] = self._text_handler is not None
        data["health"] = self._health_locked(data)
        data["engine_label"] = ENGINE_LABELS.get(data["engine_state"], data["engine_state"])
        data["health_label"] = HEALTH_LABELS[data["health"]]
        return data

    @staticmethod
    def _health_locked(data: dict[str, Any]) -> str:
        state = data.get("engine_state")
        if state in UNAVAILABLE_STATES:
            return HEALTH_UNAVAILABLE
        if state != EngineState.READY:
            # Démarrage, connexion, ou rien publié : aucune preuve suffisante.
            return HEALTH_UNKNOWN
        if data.get("audio_state") != "ok":
            # Session prête mais pipeline audio non confirmé : on ne conclut
            # pas « opérationnel ».
            return HEALTH_DEGRADED if data.get("audio_state") == "failed" else HEALTH_UNKNOWN
        degraded = (
            data.get("wake_word") == "unavailable"
            or data.get("memory_state") == "failed"
            or data.get("tasks_state") == "failed"
        )
        return HEALTH_DEGRADED if degraded else HEALTH_OPERATIONAL

    # -- abonnements ----------------------------------------------------------

    def add_hook(self, hook: Hook) -> None:
        """``hook(snapshot)`` est appelé hors verrou, depuis le thread publiant."""
        with self._lock:
            if hook not in self._hooks:
                self._hooks.append(hook)

    def remove_hook(self, hook: Hook) -> None:
        with self._lock:
            if hook in self._hooks:
                self._hooks.remove(hook)

    def hook_count(self) -> int:
        with self._lock:
            return len(self._hooks)

    def _emit(self, snapshot: dict[str, Any]) -> None:
        with self._lock:
            hooks = list(self._hooks)
        for hook in hooks:
            try:
                hook(dict(snapshot))
            except Exception:
                # Isoler les autres abonnés tout en rendant le défaut visible.
                log.exception("Runtime status subscriber failed")

    # -- lecture ---------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_locked()

    # -- publication -----------------------------------------------------------

    def update(self, **fields: Any) -> dict[str, Any]:
        """Met à jour des champs connus ; notifie seulement en cas de changement."""
        with self._lock:
            changed = False
            for key, value in fields.items():
                if key not in self._data or key in {"updated_at"}:
                    continue
                if key in {"engine_detail", "last_error", "audio_detail", "wake_word_detail", "tasks_detail"}:
                    value = redact_secrets(value, limit=300)
                if self._data.get(key) != value:
                    self._data[key] = value
                    changed = True
            if not changed:
                return self._snapshot_locked()
            self._data["updated_at"] = time.time()
            self._revision += 1
            snapshot = self._snapshot_locked()
        self._emit(snapshot)
        return snapshot

    def set_engine_state(self, state: str, detail: str = "", **extra: Any) -> dict[str, Any]:
        """Publie un nouvel état moteur (horodaté), avec champs optionnels."""
        fields: dict[str, Any] = dict(extra)
        with self._lock:
            previous = self._data.get("engine_state")
        fields["engine_state"] = str(state)
        fields["engine_detail"] = detail or ""
        if previous != state:
            fields["engine_since"] = time.time()
        if state == EngineState.READY:
            fields.setdefault("connected_since", time.time())
            fields.setdefault("reconnect_attempts", 0)
        elif state in UNAVAILABLE_STATES or state == EngineState.CONNECTING:
            if state != EngineState.CONNECTING:
                fields.setdefault("connected_since", None)
        return self.update(**fields)

    def record_error(self, message: str) -> dict[str, Any]:
        return self.update(last_error=message, last_error_at=time.time())

    def note_reconnect_attempt(self) -> dict[str, Any]:
        with self._lock:
            attempts = int(self._data.get("reconnect_attempts") or 0) + 1
        return self.update(reconnect_attempts=attempts)

    # -- envoi de texte vers le moteur -----------------------------------------

    def register_text_handler(self, handler: TextHandler | None) -> None:
        """Enregistre (ou retire avec ``None``) le point d'entrée texte du moteur."""
        with self._lock:
            if self._text_handler is handler:
                return
            self._text_handler = handler
            self._revision += 1
            snapshot = self._snapshot_locked()
        self._emit(snapshot)

    def unregister_text_handler(self, handler: TextHandler | None = None) -> None:
        """Retire le handler (seulement s'il s'agit bien de celui fourni)."""
        with self._lock:
            if handler is not None and self._text_handler is not handler:
                return
        self.register_text_handler(None)

    def unavailable_reason(self) -> str:
        """Explication honnête de l'indisponibilité de la conversation."""
        snap = self.snapshot()
        state = snap["engine_state"]
        detail = snap.get("engine_detail") or ""
        if state == EngineState.NOT_STARTED:
            return "Le moteur vocal n'est pas démarré (interface lancée sans backend vocal)."
        if state == EngineState.STARTING:
            return "Jarvis démarre : la conversation sera disponible dans un instant."
        if state == EngineState.CONNECTING:
            return "Connexion au modèle en cours : réessaie dans un instant."
        if state == EngineState.RECONNECTING:
            return "Hors ligne : connexion au modèle perdue, nouvelle tentative en cours." + (
                f" ({detail})" if detail else ""
            )
        if state == EngineState.AUTH_ERROR:
            return "Clé Gemini API refusée : vérifie GEMINI_API_KEY (fichier .env)."
        if state == EngineState.FAILED:
            return "Backend vocal indisponible." + (f" ({detail})" if detail else "")
        if state == EngineState.STOPPED:
            return "Le moteur vocal est arrêté."
        if not snap.get("text_input"):
            return "Le moteur actif n'accepte pas encore de texte."
        return ""

    def submit_text(self, text: str) -> "concurrent.futures.Future[Any]":
        """Transmet un message texte au moteur ; lève si aucun moteur ne l'accepte.

        Retourne un ``concurrent.futures.Future`` résolu par le moteur
        (accepté / refusé avec raison). Ne bloque jamais l'appelant.
        """
        with self._lock:
            handler = self._text_handler
            state = self._data.get("engine_state")
        if handler is None or state in UNAVAILABLE_STATES:
            raise EngineUnavailableError(self.unavailable_reason() or "Moteur indisponible.")
        return handler(text)


#: Singleton partagé par la boucle vocale et les interfaces.
RUNTIME = RuntimeStatusBoard()
