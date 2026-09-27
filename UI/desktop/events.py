"""Pont thread-safe backend → Desktop Mode (v1.7.0) — sans Qt.

Le backend vocal (thread asyncio, callbacks ``sounddevice``) ne doit jamais
toucher à Qt. Il publie ici des **faits** (« un outil démarre », « la
transcription a avancé ») ; l'interface, si elle est attachée, les convertit
en signal Qt et les délivre dans le thread graphique.

Même contrat que les ponts existants (``INTERFACE_MODE``, ``VISIBILITY``) :

* module sans dépendance Qt, importable depuis n'importe quel thread ;
* au plus un consommateur attaché (« le dernier attaché gagne ») ;
* aucune exception ne remonte au backend — l'interface ne doit jamais faire
  tomber la boucle vocale.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .state import DesktopEvent


class DesktopEventBridge:
    """Point d'entrée unique des évènements Desktop."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handler: Callable[[str, dict[str, Any]], None] | None = None

    def set_handler(self, handler: Callable[[str, dict[str, Any]], None] | None) -> None:
        with self._lock:
            self._handler = handler

    def attached(self) -> bool:
        with self._lock:
            return self._handler is not None

    def emit(self, event: str, **payload: Any) -> None:
        """Publie un évènement. Ne lève jamais."""
        name = str(event or "").strip()
        if not name:
            return
        with self._lock:
            handler = self._handler
        if handler is None:
            return
        try:
            handler(name, dict(payload))
        except Exception:
            pass

    # -- raccourcis lisibles pour le backend --------------------------------

    def hotword(self) -> None:
        self.emit(DesktopEvent.HOTWORD)

    def transcript(self, text: str, final: bool = False) -> None:
        self.emit(DesktopEvent.TRANSCRIPT, text=str(text or ""), final=bool(final))

    def response(self, text: str) -> None:
        self.emit(DesktopEvent.RESPONSE, text=str(text or ""))

    def tool_start(self, name: str) -> None:
        self.emit(DesktopEvent.TOOL_START, name=str(name or ""))

    def tool_end(self, name: str, success: bool = True) -> None:
        self.emit(DesktopEvent.TOOL_END, name=str(name or ""), success=bool(success))

    def interrupted(self) -> None:
        self.emit(DesktopEvent.INTERRUPTED)

    def error(self, reason: str = "") -> None:
        self.emit(DesktopEvent.ERROR, reason=str(reason or ""))

    def level(self, value: float, source: str = "input") -> None:
        """Niveau audio (0..1). ``source`` : ``input`` (micro) ou ``output``."""
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return
        self.emit("level", value=max(0.0, min(1.0, amount)), source=str(source))


#: Singleton partagé par le backend et l'interface.
DESKTOP_EVENTS = DesktopEventBridge()
