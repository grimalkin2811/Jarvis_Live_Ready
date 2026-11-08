"""Lifecycle leases for work that may use a Core service.

A lease is acquired by the executing thread, not by a cancellable asyncio
Future. Cancellation of the awaiting coroutine therefore cannot release it.
"""
from __future__ import annotations

import itertools
import threading
from contextlib import contextmanager


class ActivityBoundary:
    def __init__(self):
        self._condition = threading.Condition()
        # Stop intent is published without waiting for an in-flight launch.
        # A launch already inside the gate is allowed to finish before the
        # STOPPING transition; no new admission can enter meanwhile.
        self._stop_intent = threading.Event()
        self._closing = False
        self._creations = 0
        self._creating_threads = {}
        self._ids = itertools.count(1)
        self._active = {}

    @contextmanager
    def activity(self, name):
        if self._stop_intent.is_set():
            raise RuntimeError("Runtime en cours d'arrêt : activité refusée.")
        with self._condition:
            if self._stop_intent.is_set() or self._closing:
                raise RuntimeError("Runtime en cours d'arrêt : activité refusée.")
            token = next(self._ids)
            self._active[token] = str(name)
        try:
            yield
        finally:
            with self._condition:
                self._active.pop(token)
                self._condition.notify_all()

    def launch(self, start):
        """Atomically perform a short worker-start operation or reject it.

        The shutdown transition and actual start are serialized; admission of
        a surrounding activity alone is not permission to start a new worker.
        ``start`` must only enqueue/start work and must never wait for it.
        """
        if self._stop_intent.is_set():
            return False
        with self._condition:
            if self._stop_intent.is_set() or self._closing:
                return False
            start()
            return True

    def create(self, factory):
        """Run an admitted factory before STOPPING, without holding the gate lock.

        A factory may block; STOP_REQUESTED remains visible until it returns.
        Only construction performed before the callable returns is covered;
        workers secretly started later by external code are not controlled.
        """
        if self._stop_intent.is_set():
            return False, None
        with self._condition:
            if self._stop_intent.is_set() or self._closing:
                return False, None
            self._creations += 1
            ident = threading.get_ident()
            self._creating_threads[ident] = self._creating_threads.get(ident, 0) + 1
        try:
            return True, factory()
        finally:
            with self._condition:
                self._creations -= 1
                self._creating_threads[ident] -= 1
                if not self._creating_threads[ident]:
                    del self._creating_threads[ident]
                self._condition.notify_all()

    def creating_on_current_thread(self):
        with self._condition:
            return threading.get_ident() in self._creating_threads

    def start_thread(self, target, *, name, daemon=True):
        """Reserve the service lifetime, then atomically start a Python thread."""
        lease = self.activity(name)
        try:
            lease.__enter__()
        except RuntimeError:
            return None

        def run():
            try:
                target()
            finally:
                lease.__exit__(None, None, None)

        try:
            thread = threading.Thread(target=run, name=name, daemon=daemon)
            if not self.launch(thread.start):
                lease.__exit__(None, None, None)
                return None
            return thread
        except BaseException:
            lease.__exit__(None, None, None)
            raise

    def begin_stop(self):
        """Publish stop intent immediately, even while a launch is in flight."""
        self._stop_intent.set()

    def phase(self):
        """OPEN, STOP_REQUESTED or STOPPING; no claim of final cleanup."""
        if not self._stop_intent.is_set():
            return "OPEN"
        return "STOPPING" if self._closing else "STOP_REQUESTED"

    def reject_new(self):
        self.begin_stop()
        with self._condition:
            if threading.get_ident() in self._creating_threads:
                raise RuntimeError("Cannot transition to STOPPING inside an admitted factory")
            self._condition.wait_for(lambda: not self._creations)
            self._closing = True
            self._condition.notify_all()

    def drain(self, timeout):
        # Refuse new creations before waiting. A creation already admitted
        # must finish before STOPPING; this wait is intentionally unbounded.
        self.reject_new()
        with self._condition:
            return self._condition.wait_for(lambda: not self._active, timeout=timeout)

    def snapshot(self):
        with self._condition:
            return {"closing": self._closing, "active": tuple(self._active.values())}
