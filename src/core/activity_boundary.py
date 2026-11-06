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
        self._closing = False
        self._ids = itertools.count(1)
        self._active = {}

    @contextmanager
    def activity(self, name):
        with self._condition:
            if self._closing:
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
        with self._condition:
            if self._closing:
                return False
            start()
            return True

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

    def reject_new(self):
        with self._condition:
            self._closing = True
            self._condition.notify_all()

    def drain(self, timeout):
        with self._condition:
            self._closing = True
            return self._condition.wait_for(lambda: not self._active, timeout=timeout)

    def snapshot(self):
        with self._condition:
            return {"closing": self._closing, "active": tuple(self._active.values())}
