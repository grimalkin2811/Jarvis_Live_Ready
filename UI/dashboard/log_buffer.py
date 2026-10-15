"""Tampon circulaire du journal applicatif (logger ``jarvis``) — sans Qt.

Réutilise le système de journalisation EXISTANT (``src.logging_setup``) : un
simple ``logging.Handler`` supplémentaire, attaché au logger racine
``jarvis``, garde en mémoire les derniers enregistrements pour le widget
« Journal d'activité ». Aucun second journal n'est créé sur disque.

Tous les messages sont passés par ``redact_secrets`` avant stockage : un
message d'erreur réseau peut contenir une URL avec la clé API.
"""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from src.runtime_status import redact_secrets

ROOT_LOGGER = "jarvis"


@dataclass(frozen=True)
class LogEntry:
    id: int
    created: float
    level: str
    levelno: int
    source: str
    message: str
    #: "log" (journal applicatif) ou "event" (fait structuré du Dashboard).
    kind: str = "log"
    category: str = "general"


class LogRingHandler(logging.Handler):
    """Handler en mémoire, borné, thread-safe (``logging`` verrouille déjà)."""

    def __init__(self, capacity: int = 500, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._entries: deque[LogEntry] = deque(maxlen=max(50, int(capacity)))
        self._ids = itertools.count(1)
        self._listeners: list[Callable[[LogEntry], None]] = []
        self._listeners_lock = threading.Lock()
        self.paused = False

    def add_listener(self, listener: Callable[[LogEntry], None]) -> None:
        with self._listeners_lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: Callable[[LogEntry], None]) -> None:
        with self._listeners_lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def _push(self, entry: LogEntry) -> None:
        self._entries.append(entry)
        with self._listeners_lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(entry)
            except Exception:
                pass

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D401 - API logging
        if self.paused:
            return
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        source = record.name[len(ROOT_LOGGER) + 1:] if record.name.startswith(ROOT_LOGGER + ".") else record.name
        category = "tasks" if "task" in source or "background" in source else "general"
        self._push(
            LogEntry(next(self._ids), record.created, record.levelname, record.levelno, source or ROOT_LOGGER,
                     redact_secrets(message, limit=600), "log", category)
        )

    def record_event(self, message: str, *, level: int = logging.INFO, source: str = "dashboard",
                     category: str = "general") -> LogEntry:
        """Ajoute un fait structuré (statut de tâche, changement d'état moteur…)."""
        entry = LogEntry(next(self._ids), time.time(), logging.getLevelName(level), level, source,
                         redact_secrets(message, limit=600), "event", category)
        if not self.paused:
            self._push(entry)
        return entry

    def entries(self, *, min_level: int = logging.DEBUG, category: str | None = None,
                limit: int | None = None) -> list[LogEntry]:
        items = [e for e in list(self._entries) if e.levelno >= min_level
                 and (category is None or e.category == category)]
        if limit is not None:
            items = items[-limit:]
        return items

    def clear(self) -> None:
        self._entries.clear()


def attach(handler: LogRingHandler) -> bool:
    """Attache le tampon au logger ``jarvis`` (idempotent)."""
    logger = logging.getLogger(ROOT_LOGGER)
    if handler not in logger.handlers:
        logger.addHandler(handler)
    if logger.level == logging.NOTSET or logger.level > handler.level:
        # Sans setup_logging (tests, outils), le logger reste à WARNING par
        # défaut : on l'ouvre au niveau du tampon sans toucher aux autres
        # handlers, qui gardent leur propre filtre.
        logger.setLevel(min(handler.level, logger.level or handler.level))
    return True


def detach(handler: LogRingHandler) -> None:
    logger = logging.getLogger(ROOT_LOGGER)
    if handler in logger.handlers:
        logger.removeHandler(handler)
