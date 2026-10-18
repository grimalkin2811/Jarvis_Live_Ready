"""Surveillance des ressources (CPU, RAM, disque, réseau) — sans Qt.

* Mesures réelles via ``psutil`` (dépendance existante de Jarvis).
* Échantillonnage dans un thread dédié : rien ne bloque l'interface.
* Démarrage/arrêt par **compteur d'abonnés** : sans widget visible ni
  surveillance en arrière-plan demandée, le thread n'existe pas (coût nul).
* Intervalle réglable, suspension explicite (``suspend``).
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

try:  # psutil est une dépendance déclarée ; on reste robuste s'il manque.
    import psutil  # type: ignore
except Exception:  # pragma: no cover - environnement incomplet
    psutil = None  # type: ignore[assignment]


@dataclass(frozen=True)
class ResourceSample:
    at: float
    cpu_percent: float
    ram_percent: float
    ram_used: int
    ram_total: int
    disk_percent: float
    disk_free: int
    disk_path: str
    net_sent_rate: float  # octets/s
    net_recv_rate: float
    process_rss: int
    process_cpu: float


def _disk_target() -> str:
    try:
        from src import paths

        target = str(paths.data_dir())
        while target and not os.path.exists(target):
            parent = os.path.dirname(target)
            if parent == target:
                break
            target = parent
        return target or os.path.abspath(os.sep)
    except Exception:
        return os.path.abspath(os.sep)


class ResourceMonitor:
    HISTORY = 120

    def __init__(self, *, interval: float = 2.0, sampler: Callable[[], ResourceSample] | None = None) -> None:
        self._lock = threading.RLock()
        self._interval = max(0.5, float(interval))
        self._subscribers: dict[str, Callable[[ResourceSample], None]] = {}
        self._history: deque[ResourceSample] = deque(maxlen=self.HISTORY)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._suspended = False
        self._sampler = sampler or self._sample
        self._last_net: tuple[float, int, int] | None = None
        self._process = None
        self._disk_path = _disk_target()
        self.samples_taken = 0
        self.last_error = ""

    @staticmethod
    def available() -> bool:
        return psutil is not None

    # -- abonnements -------------------------------------------------------------

    def subscribe(self, key: str, callback: Callable[[ResourceSample], None]) -> None:
        with self._lock:
            self._subscribers[str(key)] = callback
        self._ensure_thread()

    def unsubscribe(self, key: str) -> None:
        with self._lock:
            self._subscribers.pop(str(key), None)
            empty = not self._subscribers
        if empty:
            self.stop()

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    # -- réglages ----------------------------------------------------------------

    @property
    def interval(self) -> float:
        return self._interval

    def set_interval(self, seconds: float) -> None:
        self._interval = max(0.5, min(60.0, float(seconds)))
        self._wake.set()

    def suspend(self, value: bool = True) -> None:
        with self._lock:
            self._suspended = bool(value)
        if value:
            self.stop()
        else:
            self._ensure_thread()

    @property
    def suspended(self) -> bool:
        return self._suspended

    def history(self) -> list[ResourceSample]:
        with self._lock:
            return list(self._history)

    # -- cycle de vie ------------------------------------------------------------

    def _ensure_thread(self) -> None:
        with self._lock:
            if self._suspended or not self._subscribers or not self.available() and self._sampler == self._sample:
                return
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="jarvis-dashboard-resources", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        with self._lock:
            thread = self._thread
            self._thread = None
        self._stop.set()
        self._wake.set()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=timeout)

    def _run(self) -> None:
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)  # amorce la mesure non bloquante
            except Exception:
                pass
        while not self._stop.is_set():
            self._wake.clear()
            if self._wake.wait(self._interval) and self._stop.is_set():
                break
            if self._stop.is_set():
                break
            try:
                sample = self._sampler()
            except Exception as exc:
                self.last_error = str(exc)
                continue
            self.samples_taken += 1
            with self._lock:
                self._history.append(sample)
                callbacks = list(self._subscribers.values())
            for callback in callbacks:
                try:
                    callback(sample)
                except Exception:
                    pass

    # -- mesure ------------------------------------------------------------------

    def _sample(self) -> ResourceSample:
        if psutil is None:
            raise RuntimeError("psutil indisponible")
        now = time.monotonic()
        cpu = float(psutil.cpu_percent(interval=None))
        memory = psutil.virtual_memory()
        try:
            disk = psutil.disk_usage(self._disk_path)
            disk_percent, disk_free = float(disk.percent), int(disk.free)
        except Exception:
            disk_percent, disk_free = -1.0, 0
        sent_rate = recv_rate = 0.0
        try:
            counters = psutil.net_io_counters()
            if self._last_net is not None:
                elapsed = max(0.001, now - self._last_net[0])
                sent_rate = max(0.0, (counters.bytes_sent - self._last_net[1]) / elapsed)
                recv_rate = max(0.0, (counters.bytes_recv - self._last_net[2]) / elapsed)
            self._last_net = (now, int(counters.bytes_sent), int(counters.bytes_recv))
        except Exception:
            pass
        rss = 0
        proc_cpu = 0.0
        try:
            if self._process is None:
                self._process = psutil.Process()
                self._process.cpu_percent(interval=None)
            rss = int(self._process.memory_info().rss)
            proc_cpu = float(self._process.cpu_percent(interval=None))
        except Exception:
            pass
        return ResourceSample(
            time.time(), cpu, float(memory.percent), int(memory.used), int(memory.total),
            disk_percent, disk_free, self._disk_path, sent_rate, recv_rate, rss, proc_cpu,
        )


def threshold_breaches(sample: ResourceSample, cpu: int, ram: int, disk: int) -> list[tuple[str, str]]:
    """(métrique, message) des seuils franchis par un échantillon réel."""
    breaches: list[tuple[str, str]] = []
    if sample.cpu_percent >= cpu:
        breaches.append(("cpu", f"Processeur à {sample.cpu_percent:.0f} % (seuil {cpu} %)."))
    if sample.ram_percent >= ram:
        breaches.append(("ram", f"Mémoire vive à {sample.ram_percent:.0f} % (seuil {ram} %)."))
    if sample.disk_percent >= 0 and sample.disk_percent >= disk:
        breaches.append(("disk", f"Disque à {sample.disk_percent:.0f} % (seuil {disk} %)."))
    return breaches


def human_bytes(value: float) -> str:
    units = ["o", "Ko", "Mo", "Go", "To"]
    amount = float(max(0.0, value))
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.0f} {unit}" if unit == "o" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} To"


def describe_sample(sample: ResourceSample) -> dict[str, Any]:
    return {
        "cpu": f"{sample.cpu_percent:.0f} %",
        "ram": f"{sample.ram_percent:.0f} % ({human_bytes(sample.ram_used)} / {human_bytes(sample.ram_total)})",
        "disk": "indisponible" if sample.disk_percent < 0 else f"{sample.disk_percent:.0f} % (libre {human_bytes(sample.disk_free)})",
        "net": f"↑ {human_bytes(sample.net_sent_rate)}/s  ↓ {human_bytes(sample.net_recv_rate)}/s",
        "process": f"Jarvis : {human_bytes(sample.process_rss)}, {sample.process_cpu:.0f} % CPU",
    }
