"""Exécution hors du thread graphique, résultat livré dans le thread Qt.

Toute opération susceptible de bloquer (SQLite, disque, Task Manager,
routines, psutil sur un disque réseau…) passe par ``JobRunner.submit`` :
l'interface reste fluide et le résultat revient par signal Qt en file
d'attente. Un widget détruit entre-temps ne reçoit rien (``owner``).
"""

from __future__ import annotations

import concurrent.futures
import itertools
import threading
import time
import weakref
from collections.abc import Callable
from contextlib import contextmanager
from typing import Any

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, Qt, Signal, Slot


class JobRunner(QObject):
    _finished = Signal(int, bool, object)
    _future_done = Signal(int, object)

    def __init__(self, max_workers: int = 3, parent: QObject | None = None, *, activity_boundary=None) -> None:
        super().__init__(parent)
        self._activity_boundary = activity_boundary
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)), thread_name_prefix="jarvis-dashboard-job"
        )
        self._ids = itertools.count(1)
        self._callbacks: dict[int, tuple[Callable[[Any], None] | None, Callable[[BaseException], None] | None, Any]] = {}
        self._watched: dict[int, tuple[Callable[[Any], None], Any]] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._stop_intent = threading.Event()
        self._shutdown_requested = False
        self._deliveries = 0
        self.completed = 0
        self.failed = 0
        self._finished.connect(self._deliver, Qt.QueuedConnection)
        self._future_done.connect(self._deliver_future, Qt.QueuedConnection)

    @contextmanager
    def _guard(self):
        """Finish a deferred stop as the last in-flight lock holder leaves."""
        self._lock.acquire()
        try:
            yield
        finally:
            try:
                if self._stop_intent.is_set() and not self._shutdown_requested:
                    self._shutdown_locked()
            finally:
                self._lock.release()
            # Covers intent published between the check and releasing the lock.
            if self._stop_intent.is_set() and not self._shutdown_requested:
                self.request_shutdown()

    def _shutdown_locked(self):
        if self._shutdown_requested:
            return
        self._closed = True
        self._callbacks.clear()
        self._watched.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)
        self._shutdown_requested = True

    def request_shutdown(self):
        """Publish stop without waiting for an in-flight executor submission."""
        self._stop_intent.set()
        if not self._lock.acquire(blocking=False):
            return
        try:
            self._shutdown_locked()
        finally:
            self._lock.release()

    def submit(
        self,
        fn: Callable[..., Any],
        *args: Any,
        on_done: Callable[[Any], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        owner: QObject | None = None,
        **kwargs: Any,
    ) -> int:
        """Planifie ``fn`` ; ``on_done``/``on_error`` s'exécutent dans le thread Qt."""
        if self._stop_intent.is_set():
            return 0
        if self._activity_boundary is None:
            return self._submit(fn, args, kwargs, on_done, on_error, owner)
        # Atomically admit submission before an executor thread can be spawned.
        # The worker acquires a separate lease when it actually starts.
        lease = self._activity_boundary.activity("dashboard.submit")
        try:
            lease.__enter__()
        except RuntimeError:
            return 0
        try:
            return self._submit(fn, args, kwargs, on_done, on_error, owner)
        finally:
            lease.__exit__(None, None, None)

    def _submit(self, fn, args, kwargs, on_done, on_error, owner) -> int:
        job_id = next(self._ids)
        owner_ref = weakref.ref(owner) if owner is not None else None

        def _run() -> None:
            try:
                if self._activity_boundary is None:
                    result = fn(*args, **kwargs)
                else:
                    # Acquire on the actual executor thread: a queued job that
                    # starts after stop is rejected before it touches services.
                    with self._activity_boundary.activity("dashboard.job"):
                        result = fn(*args, **kwargs)
                ok = True
            except BaseException as exc:  # noqa: BLE001 - livré à on_error
                result = exc
                ok = False
            try:
                self._finished.emit(job_id, ok, result)
            except RuntimeError:  # pragma: no cover - objet Qt détruit à l'arrêt
                pass

        # Serialize executor submission with Hub shutdown. Otherwise shutdown
        # can set _closed after the check but before submit() creates a thread.
        with self._guard():
            if self._closed or self._stop_intent.is_set():
                return 0
            self._callbacks[job_id] = (on_done, on_error, owner_ref)
            try:
                if self._activity_boundary is None:
                    self._executor.submit(_run)
                elif not self._activity_boundary.launch(lambda: self._executor.submit(_run)):
                    self._callbacks.pop(job_id, None)
                    return 0
            except RuntimeError:
                self._callbacks.pop(job_id, None)
                return 0
            except BaseException:
                self._callbacks.pop(job_id, None)
                raise
        return job_id

    def watch_future(
        self,
        future: concurrent.futures.Future,
        *,
        on_done: Callable[[concurrent.futures.Future], None],
        owner: QObject | None = None,
    ) -> int:
        """Livre dans le thread Qt un ``Future`` résolu sur N'IMPORTE QUEL thread.

        Cas d'usage : la confirmation d'envoi d'un message texte, résolue sur
        la boucle asyncio du moteur vocal. Le signal est émis par ce
        ``JobRunner`` (qui vit avec le hub, donc tant que Jarvis tourne) et
        JAMAIS par le widget demandeur, qui peut avoir été détruit entre-temps
        (Dashboard fermé, fenêtre libérée, widget retiré) : ``on_done`` est
        alors simplement ignoré, sans erreur dans le thread du moteur.
        Les futurs surveillés ne comptent pas dans ``pending()``.
        """
        watch_id = next(self._ids)
        owner_ref = weakref.ref(owner) if owner is not None else None
        with self._guard():
            if self._closed or self._stop_intent.is_set():
                return 0
            self._watched[watch_id] = (on_done, owner_ref)

        def _relay(done: concurrent.futures.Future) -> None:
            try:
                self._future_done.emit(watch_id, done)
            except RuntimeError:  # pragma: no cover - runner détruit à l'arrêt de Jarvis
                pass

        future.add_done_callback(_relay)
        return watch_id

    @Slot(int, object)
    def _deliver_future(self, watch_id: int, future: Any) -> None:
        with self._guard():
            entry = self._watched.pop(watch_id, None)
            if entry is None or self._closed or self._stop_intent.is_set():
                return
            self._deliveries += 1
        try:
            on_done, owner_ref = entry
            if self._owner_valid(owner_ref):
                on_done(future)
        finally:
            with self._guard():
                self._deliveries -= 1

    @staticmethod
    def _owner_valid(owner_ref) -> bool:
        if owner_ref is None:
            return True
        owner = owner_ref()
        if owner is None:
            return False
        try:
            from shiboken6 import isValid
            return isValid(owner)
        except Exception:
            return True  # legacy fallback when shiboken cannot inspect the owner

    def pending(self) -> int:
        with self._guard():
            return len(self._callbacks)

    @Slot(int, bool, object)
    def _deliver(self, job_id: int, ok: bool, result: Any) -> None:
        with self._guard():
            entry = self._callbacks.pop(job_id, None)
            if entry is None or self._closed or self._stop_intent.is_set():
                return
            self._deliveries += 1
        try:
            on_done, on_error, owner_ref = entry
            if not self._owner_valid(owner_ref):
                return
            if ok:
                self.completed += 1
                if on_done is not None:
                    on_done(result)
            else:
                self.failed += 1
                if on_error is not None:
                    on_error(result)
        finally:
            with self._guard():
                self._deliveries -= 1

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Attend la fin des travaux en traitant les évènements Qt (tests)."""
        deadline = time.monotonic() + timeout
        app = QCoreApplication.instance()
        while time.monotonic() < deadline:
            if self.pending() == 0:
                return True
            if app is not None:
                app.processEvents(QEventLoop.AllEvents, 20)
            time.sleep(0.005)
        return self.pending() == 0

    def shutdown(self) -> None:
        """Compatibility: wait for any in-flight submit lock, then request stop."""
        self._stop_intent.set()
        with self._guard():
            self._shutdown_locked()

    def shutdown_requested(self) -> bool:
        """Whether executor shutdown has actually been issued."""
        return self._shutdown_requested

    def wait_shutdown(self, timeout: float = 2.0) -> bool:
        """Optionally join pool threads for a bounded time; never join oneself."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._guard():
            threads = tuple(self._executor._threads)
        for thread in threads:
            if thread is not threading.current_thread():
                thread.join(max(0.0, deadline - time.monotonic()))
        return self.shutdown_complete()

    def shutdown_complete(self) -> bool:
        """True only after executor threads and already-started callbacks exit."""
        if not self._shutdown_requested or not self._lock.acquire(blocking=False):
            return False
        try:
            if self._deliveries:
                return False
            threads = tuple(self._executor._threads)
        finally:
            self._lock.release()
        return not any(thread.is_alive() for thread in threads)
