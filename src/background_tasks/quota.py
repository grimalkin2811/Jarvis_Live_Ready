"""Protection persistante et loop-safe du quota Gemini 3.8 Flash."""
from __future__ import annotations

import asyncio
import collections
import json
import threading
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path


class QuotaExceededError(RuntimeError):
    pass


@dataclass
class QuotaLease:
    estimated_tokens: int
    attempts: int = 0
    released: bool = False


@dataclass(frozen=True)
class QuotaSnapshot:
    calls_today: int
    calls_last_minute: int
    tokens_today: int
    tokens_last_minute: int
    daily_limit: int
    rpm_limit: int
    tpm_limit: int
    concurrent_limit: int


class ComplexModelQuota:
    def __init__(self, *, rpm: int = 5, rpd: int = 20, tpm: int = 250_000, concurrency: int = 1, storage_path=None):
        self.rpm, self.rpd, self.tpm = rpm, rpd, tpm
        self.concurrent_limit = concurrency
        self.storage_path = Path(storage_path) if storage_path else None
        self._lock = threading.RLock()
        self._minute: collections.deque[float] = collections.deque()
        self._minute_tokens: collections.deque[tuple[float, int]] = collections.deque()
        self._day = date.today()
        self._calls_today = 0
        self._tokens_today = 0
        # Créée paresseusement sur la boucle qui exécute réellement les tâches.
        self._semaphore: asyncio.Semaphore | None = None
        self._async_loop = None
        self._legacy_leases: collections.deque[QuotaLease] = collections.deque()
        self._load()

    def _ensure_async_primitives(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.concurrent_limit)
            self._async_loop = loop
        elif self._async_loop is not loop:
            raise RuntimeError("Le quota Gemini 3.8 est utilisé depuis une boucle asyncio différente.")
        return self._semaphore

    def _rollover(self) -> None:
        today = date.today()
        if today != self._day:
            self._day = today
            self._calls_today = self._tokens_today = 0
        cutoff = time.time() - 60.0
        while self._minute and self._minute[0] <= cutoff:
            self._minute.popleft()
        while self._minute_tokens and self._minute_tokens[0][0] <= cutoff:
            self._minute_tokens.popleft()

    def _save(self) -> None:
        if self.storage_path is None:
            return
        try:
            payload = {
                "day": self._day.isoformat(), "calls_today": self._calls_today,
                "tokens_today": self._tokens_today, "minute": list(self._minute),
                "minute_tokens": list(self._minute_tokens),
            }
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(self.storage_path)
        except Exception:
            pass

    def _load(self) -> None:
        if self.storage_path is None or not self.storage_path.exists():
            return
        try:
            data = json.loads(self.storage_path.read_text(encoding="utf-8"))
            self._day = date.fromisoformat(data["day"])
            self._calls_today = max(0, int(data.get("calls_today", 0)))
            self._tokens_today = max(0, int(data.get("tokens_today", 0)))
            self._minute.extend(float(item) for item in data.get("minute", []))
            self._minute_tokens.extend((float(item[0]), int(item[1])) for item in data.get("minute_tokens", []))
            self._rollover()
        except Exception:
            self._day = date.today()
            self._calls_today = self._tokens_today = 0
            self._minute.clear()
            self._minute_tokens.clear()

    def _check_available(self, estimate: int) -> None:
        self._rollover()
        if self._calls_today >= self.rpd:
            raise QuotaExceededError(f"Quota Gemini 3.8 épuisé ({self.rpd} appels/jour). Réessaie demain.")
        if len(self._minute) >= self.rpm:
            raise QuotaExceededError(f"Limite Gemini 3.8 atteinte ({self.rpm} appels/minute). Réessaie plus tard.")
        if estimate > self.tpm or sum(tokens for _, tokens in self._minute_tokens) + estimate > self.tpm:
            raise QuotaExceededError(f"Budget Gemini 3.8 de {self.tpm} tokens/minute indisponible. Réessaie plus tard.")

    async def reserve(self, estimated_tokens: int = 0) -> QuotaLease:
        estimate = max(0, int(estimated_tokens or 0))
        with self._lock:
            self._check_available(estimate)
        semaphore = self._ensure_async_primitives()
        await semaphore.acquire()
        try:
            with self._lock:
                self._check_available(estimate)
            return QuotaLease(estimate)
        except Exception:
            semaphore.release()
            raise

    def mark_attempt(self, lease: QuotaLease) -> None:
        """Compte exactement un appel juste avant son envoi au SDK."""
        if lease.released:
            raise RuntimeError("Réservation de quota déjà libérée.")
        with self._lock:
            self._check_available(lease.estimated_tokens)
            now = time.time()
            self._calls_today += 1
            self._minute.append(now)
            self._minute_tokens.append((now, lease.estimated_tokens))
            lease.attempts += 1
            self._save()

    def finish(self, lease: QuotaLease, actual_tokens: int = 0) -> None:
        if lease.released:
            return
        actual = max(0, int(actual_tokens or 0))
        with self._lock:
            if lease.attempts:
                # Seul le dernier appel a fourni une métrique réelle. Les
                # tentatives 503 restent estimées de façon conservatrice.
                delta = actual - lease.estimated_tokens
                if delta > 0:
                    self._minute_tokens.append((time.time(), delta))
                self._tokens_today += actual
            lease.released = True
            self._save()
        assert self._semaphore is not None
        self._semaphore.release()

    # Compatibilité API interne antérieure; les nouveaux appels utilisent
    # reserve/mark_attempt/finish afin de distinguer réservation et envoi réel.
    async def acquire(self, estimated_tokens: int = 0) -> int:
        lease = await self.reserve(estimated_tokens)
        self.mark_attempt(lease)
        with self._lock:
            self._legacy_leases.append(lease)
        return lease.estimated_tokens

    def release(self, reserved_tokens: int = 0, actual_tokens: int | None = None) -> None:
        with self._lock:
            if not self._legacy_leases:
                return
            lease = self._legacy_leases.popleft()
        actual = reserved_tokens if actual_tokens is None else actual_tokens
        self.finish(lease, actual)

    def snapshot(self) -> QuotaSnapshot:
        with self._lock:
            self._rollover()
            return QuotaSnapshot(
                self._calls_today, len(self._minute), self._tokens_today,
                sum(tokens for _, tokens in self._minute_tokens),
                self.rpd, self.rpm, self.tpm, self.concurrent_limit,
            )
