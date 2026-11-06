"""Explicit, thread-safe registry of resources owned by one runtime."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

log = logging.getLogger("jarvis.core.services")
T = TypeVar("T")


class ServiceRegistry:
    def __init__(self) -> None:
        self._services: dict[str, Any] = {}
        self._factories: dict[str, Callable[[], Any]] = {}
        self._resolving: set[str] = set()
        self._cleanup: list[tuple[str, Callable[[], None]]] = []
        self._closed = False
        self._close_failures: tuple[str, ...] | None = None
        self._lock = threading.RLock()

    def register(self, name: str, service: T, *, close: Callable[[], None] | None = None) -> T:
        with self._lock:
            if self._closed:
                raise RuntimeError("Registry already closed")
            if name in self._services or name in self._factories:
                raise ValueError(f"Service already registered: {name}")
            self._services[name] = service
            if close is not None:
                self._cleanup.append((name, close))
        return service

    def register_lazy(self, name: str, factory: Callable[[], T]) -> None:
        """Register a borrowed, lazily resolved service; its owner closes it."""
        with self._lock:
            if self._closed:
                raise RuntimeError("Registry already closed")
            if name in self._services or name in self._factories:
                raise ValueError(f"Service already registered: {name}")
            self._factories[name] = factory

    def require(self, name: str, expected_type: type[T] | None = None) -> T | Any:
        with self._lock:
            if self._closed:
                raise RuntimeError("Registry already closed")
            if name in self._factories:
                if name in self._resolving:
                    raise RuntimeError(f"Cyclic service dependency: {name}")
                self._resolving.add(name)
                try:
                    service = self._factories[name]()
                    self._services[name] = service
                    del self._factories[name]
                finally:
                    self._resolving.remove(name)
            try:
                service = self._services[name]
            except KeyError as exc:
                raise LookupError(f"Missing service: {name}") from exc
            if expected_type is not None and not isinstance(service, expected_type):
                raise TypeError(f"Service {name} is not {expected_type.__name__}")
            return service

    def close(self) -> list[str]:
        with self._lock:
            if self._closed:
                # A repeated close must not turn an in-flight or failed
                # cleanup into a false success, nor rerun unsafe callbacks.
                return list(self._close_failures) if self._close_failures is not None else ["cleanup_in_progress"]
            self._closed = True
            cleanup = tuple(reversed(self._cleanup))
            self._cleanup.clear()
            self._services.clear()
            self._factories.clear()
        failures = []
        for name, callback in cleanup:
            try:
                callback()
            except Exception:
                failures.append(name)
                log.exception("Failed to close service %s", name)
        with self._lock:
            self._close_failures = tuple(failures)
        return failures
