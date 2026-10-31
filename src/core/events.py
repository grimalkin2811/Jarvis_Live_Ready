"""Small synchronous event dispatcher for runtime lifecycle notifications.

Callbacks run in the publisher's thread; GUI consumers must marshal to Qt.
Subscribe returns an idempotent unsubscribe function, so ownership is explicit.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger("jarvis.core.events")


@dataclass(frozen=True)
class RuntimeEvent:
    kind: str
    detail: str = ""


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[int, Callable[[RuntimeEvent], None]] = {}
        self._next_id = 0

    def subscribe(self, callback: Callable[[RuntimeEvent], None]) -> Callable[[], None]:
        if not callable(callback):
            raise TypeError("callback must be callable")
        with self._lock:
            token = self._next_id
            self._next_id += 1
            self._subscribers[token] = callback

        def unsubscribe() -> None:
            with self._lock:
                self._subscribers.pop(token, None)

        return unsubscribe

    def publish(self, event: RuntimeEvent) -> None:
        with self._lock:
            callbacks = tuple(self._subscribers.values())
        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                log.exception("Runtime event subscriber failed (%s)", event.kind)

    def clear(self) -> None:
        with self._lock:
            self._subscribers.clear()
