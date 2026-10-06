"""Protection locale et persistante du quota rare Gemini 3.8 Flash."""
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
    """Quota indisponible : l'appel peut être réessayé ultérieurement."""


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
        self._semaphore = asyncio.Semaphore(concurrency)
        self._load()

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
            pass  # La protection mémoire reste active; jamais casser une tâche pour le journal.

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

    async def acquire(self, estimated_tokens: int = 0) -> int:
        estimate = max(0, int(estimated_tokens or 0))
        # Refus explicite plutôt qu'une attente cachée d'une minute/journée.
        with self._lock:
            self._rollover()
            if self._calls_today >= self.rpd:
                raise QuotaExceededError(f"Quota Gemini 3.8 épuisé ({self.rpd} appels/jour). Réessaie demain.")
            if len(self._minute) >= self.rpm:
                raise QuotaExceededError(f"Limite Gemini 3.8 atteinte ({self.rpm} appels/minute). Réessaie plus tard.")
            if estimate > self.tpm or sum(tokens for _, tokens in self._minute_tokens) + estimate > self.tpm:
                raise QuotaExceededError(f"Budget Gemini 3.8 de {self.tpm} tokens/minute indisponible. Réessaie plus tard.")
        await self._semaphore.acquire()
        with self._lock:
            self._rollover()
            if self._calls_today >= self.rpd or len(self._minute) >= self.rpm:
                self._semaphore.release()
                raise QuotaExceededError("Quota Gemini 3.8 devenu indisponible. Réessaie plus tard.")
            now = time.time()
            self._calls_today += 1
            self._minute.append(now)
            self._minute_tokens.append((now, estimate))
            self._save()
        return estimate

    def release(self, reserved_tokens: int = 0, actual_tokens: int | None = None) -> None:
        reserved = max(0, int(reserved_tokens or 0))
        actual = reserved if actual_tokens is None else max(0, int(actual_tokens or 0))
        with self._lock:
            # La réservation compte déjà dans le TPM; n'ajouter que l'écart réel.
            if actual > reserved:
                self._minute_tokens.append((time.time(), actual - reserved))
            self._tokens_today += actual
            self._save()
        self._semaphore.release()

    def snapshot(self) -> QuotaSnapshot:
        with self._lock:
            self._rollover()
            return QuotaSnapshot(
                self._calls_today, len(self._minute), self._tokens_today,
                sum(tokens for _, tokens in self._minute_tokens),
                self.rpd, self.rpm, self.tpm, self.concurrent_limit,
            )
