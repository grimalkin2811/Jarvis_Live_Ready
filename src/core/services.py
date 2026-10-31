"""Explicit registry of resources owned by one runtime instance."""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger("jarvis.core.services")


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[str, Any] = {}
        self._cleanup: list[tuple[str, Callable[[], None]]] = []
        self._closed = False

    def register(self, name: str, service: Any, *, close: Callable[[], None] | None = None) -> Any:
        if self._closed:
            raise RuntimeError("Registry already closed")
        if name in self._services:
            raise ValueError(f"Service already registered: {name}")
        self._services[name] = service
        if close is not None:
            self._cleanup.append((name, close))
        return service

    def require(self, name: str) -> Any:
        try:
            return self._services[name]
        except KeyError as exc:
            raise LookupError(f"Missing service: {name}") from exc

    def close(self) -> list[str]:
        if self._closed:
            return []
        self._closed = True
        failures = []
        for name, cleanup in reversed(self._cleanup):
            try:
                cleanup()
            except Exception:
                failures.append(name)
                log.exception("Failed to close service %s", name)
        self._cleanup.clear()
        self._services.clear()
        return failures
