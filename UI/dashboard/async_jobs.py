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
from typing import Any

from PySide6.QtCore import QCoreApplication, QEventLoop, QObject, Qt, Signal, Slot


class JobRunner(QObject):
    _finished = Signal(int, bool, object)

    def __init__(self, max_workers: int = 3, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)), thread_name_prefix="jarvis-dashboard-job"
        )
        self._ids = itertools.count(1)
        self._callbacks: dict[int, tuple[Callable[[Any], None] | None, Callable[[BaseException], None] | None, Any]] = {}
        self._lock = threading.Lock()
        self._closed = False
        self.completed = 0
        self.failed = 0
        self._finished.connect(self._deliver, Qt.QueuedConnection)

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
        job_id = next(self._ids)
        owner_ref = weakref.ref(owner) if owner is not None else None
        with self._lock:
            if self._closed:
                return 0
            self._callbacks[job_id] = (on_done, on_error, owner_ref)

        def _run() -> None:
            try:
                result = fn(*args, **kwargs)
                ok = True
            except BaseException as exc:  # noqa: BLE001 - livré à on_error
                result = exc
                ok = False
            try:
                self._finished.emit(job_id, ok, result)
            except RuntimeError:  # pragma: no cover - objet Qt détruit à l'arrêt
                pass

        try:
            self._executor.submit(_run)
        except RuntimeError:
            with self._lock:
                self._callbacks.pop(job_id, None)
            return 0
        return job_id

    def pending(self) -> int:
        with self._lock:
            return len(self._callbacks)

    @Slot(int, bool, object)
    def _deliver(self, job_id: int, ok: bool, result: Any) -> None:
        with self._lock:
            entry = self._callbacks.pop(job_id, None)
        if entry is None:
            return
        on_done, on_error, owner_ref = entry
        if owner_ref is not None:
            owner = owner_ref()
            if owner is None:
                return
            try:
                from shiboken6 import isValid

                if not isValid(owner):
                    return
            except Exception:
                pass
        if ok:
            self.completed += 1
            if on_done is not None:
                on_done(result)
        else:
            self.failed += 1
            if on_error is not None:
                on_error(result)

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
        with self._lock:
            self._closed = True
            self._callbacks.clear()
        self._executor.shutdown(wait=False, cancel_futures=True)
