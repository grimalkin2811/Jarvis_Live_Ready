"""Pont thread-safe pour les commandes vocales Blob Mode / Desktop Mode.

La persistance reste dans ``src.settings`` (``config.json``). Ce pont ne garde
aucun mode concurrent : il écrit la source de vérité puis notifie, si elle est
attachée, l'interface Qt afin d'appliquer la transition à chaud.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

from src import settings


class InterfaceModeBridge:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handler: Callable[[str], None] | None = None

    def set_handler(self, handler: Callable[[str], None] | None) -> None:
        """Branche/débranche le contrôleur runtime actif."""
        with self._lock:
            self._handler = handler

    def request(self, mode: str) -> str:
        """Persiste le mode et demande son application immédiate au runtime."""
        normalized = settings.set_interface_mode(mode)
        with self._lock:
            handler = self._handler
        if handler is not None:
            handler(normalized)
        return normalized

    def current(self) -> str:
        return settings.get_interface_mode()


INTERFACE_MODE = InterfaceModeBridge()
