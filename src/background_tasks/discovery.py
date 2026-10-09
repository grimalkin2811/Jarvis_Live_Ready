"""Découverte et validation du modèle Gemini 3 Live disponible.

Aucun identifiant Live n'est supposé. La liste exposée à la clé est filtrée
par capacité BidiGenerateContent, classée, puis chaque candidat doit réussir
un tour AUDIO transcrit complet avant d'être mémorisé pour le processus courant.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import threading
import time
from dataclasses import dataclass

from .gateway import BackgroundModelGateway, classify_background_error


@dataclass(frozen=True)
class LiveValidationFailure:
    model: str
    category: str
    detail: str


class LiveModelDiscoveryError(RuntimeError):
    def __init__(self, message: str, *, failures: tuple[LiveValidationFailure, ...] = ()):
        super().__init__(message)
        self.failures = failures


@dataclass(frozen=True)
class LiveModelResolution:
    model: str
    transport: str = "Live/BidiGenerateContent"
    validated: bool = True
    diagnostics: tuple[LiveValidationFailure, ...] = ()


_CACHE: dict[str, LiveModelResolution] = {}
_CACHE_LOCK = threading.RLock()


def _model_name(model) -> str:
    return str(getattr(model, "name", "") or "").removeprefix("models/")


def _supported_actions(model) -> set[str]:
    raw = getattr(model, "supported_actions", None) or []
    return {re.sub(r"[^a-z]", "", str(item).lower()) for item in raw}


def live_candidate_rejection(model) -> str | None:
    """Écarte les variantes Bidi spécialisées incompatibles avec notre rôle."""
    name = _model_name(model).lower()
    actions = _supported_actions(model)
    if not re.search(r"(?:^|-)gemini-3(?:[.-]|$)", name):
        return "pas un modèle Gemini 3"
    if "live" not in name:
        return "pas un modèle Live"
    if not any("bidigeneratecontent" in action for action in actions):
        return "BidiGenerateContent non annoncé"
    if any(marker in name for marker in ("transcribe", "translate")):
        return "modèle spécialisé transcription/traduction"
    if "thinking" in name:
        return "configuration de réflexion spécifique requise"
    return None


def is_gemini3_live_model(model) -> bool:
    return live_candidate_rejection(model) is None


def _candidate_score(model) -> tuple:
    name = _model_name(model).lower()
    versions = tuple(int(value) for value in re.findall(r"\d+", name)[:2])
    stable = not any(word in name for word in ("preview", "experimental", "legacy"))
    standard = not any(word in name for word in ("extended", "thinking"))
    return (stable, standard, versions, name)


class LiveModelResolver:
    def __init__(
        self, client, *, api_key_fingerprint: str = "", hint: str = "",
        list_timeout: float = 30.0, connect_timeout: float = 25.0,
        total_timeout: float = 60.0, max_candidates: int = 3,
        retry_attempts: int = 3, retry_base_delay: float = 1.0,
    ):
        self.client = client
        self.hint = str(hint or "").strip().removeprefix("models/")
        self.list_timeout = max(0.01, float(list_timeout))
        self.connect_timeout = max(0.01, float(connect_timeout))
        self.total_timeout = max(0.01, float(total_timeout))
        self.max_candidates = max(1, int(max_candidates))
        self.retry_attempts = max(1, int(retry_attempts))
        self.retry_base_delay = max(0.0, float(retry_base_delay))
        self._cache_key = api_key_fingerprint or f"client-{id(client)}"
        self._resolution: LiveModelResolution | None = None

    @staticmethod
    def fingerprint(api_key: str) -> str:
        return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:16] if api_key else "anonymous"

    @property
    def resolution(self) -> LiveModelResolution | None:
        return self._resolution

    @staticmethod
    def _external_status(exc: BaseException) -> int | None:
        text = str(exc).lower()
        if "429" in text or "resource_exhausted" in text:
            return 429
        if "503" in text or "unavailable" in text or "high demand" in text:
            return 503
        for value in (getattr(exc, "code", None), getattr(exc, "status_code", None)):
            try:
                code = int(value)
                if code in (429, 503):
                    return code
            except (TypeError, ValueError):
                continue
        return None

    async def _retry_external(self, operation):
        for attempt in range(1, self.retry_attempts + 1):
            try:
                return await operation()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if self._external_status(exc) not in (429, 503) or attempt >= self.retry_attempts:
                    raise
                await asyncio.sleep(self.retry_base_delay * (2 ** (attempt - 1)))
        raise AssertionError("retry loop unreachable")  # pragma: no cover

    async def _list_models(self, timeout: float | None = None) -> list:
        effective_timeout = min(self.list_timeout, timeout or self.list_timeout)

        async def list_once():
            pager = await asyncio.wait_for(
                self.client.aio.models.list(), timeout=effective_timeout
            )
            models = []
            async for model in pager:
                models.append(model)
            return models

        return await self._retry_external(list_once)

    async def _validate(self, model: str, *, timeout: float) -> None:
        """Valide un tour complet AUDIO → transcription, pas le seul handshake."""
        gateway = BackgroundModelGateway(
            self.client,
            retry_attempts=self.retry_attempts,
            retry_base_delay=self.retry_base_delay,
        )
        response = await gateway.generate_live(
            model,
            "Réponds uniquement par le mot OK.",
            system_instruction=(
                "Test technique de disponibilité Jarvis. Prononce uniquement "
                "le mot OK, sans explication."
            ),
            temperature=0.0,
            timeout_seconds=timeout,
        )
        if not response.text.strip():  # défense supplémentaire au contrat gateway
            raise RuntimeError("Échange Live terminé sans transcription textuelle.")

    @staticmethod
    def _validation_failure(model: str, exc: BaseException) -> LiveValidationFailure:
        text = str(exc)
        lowered = text.lower()
        info = classify_background_error(exc)
        if (
            "not supported by the model" in lowered
            or "unsupported model" in lowered
            or ("modality" in lowered and "not supported" in lowered)
        ):
            category = "UNSUPPORTED_MODEL_OR_MODALITY"
        elif "thinking" in lowered and any(
            marker in lowered for marker in ("required", "must", "missing")
        ):
            category = "CONFIGURATION_ERROR"
        elif info.category == "TIMEOUT":
            category = "LIVE_TIMEOUT"
        elif info.category in {
            "EXTERNAL_QUOTA", "EXTERNAL_SERVICE", "LIVE_RESOURCE_EXHAUSTED",
        }:
            category = info.category
        elif info.category in {"LIVE_PROTOCOL", "CODE_OR_PROTOCOL"}:
            category = "LIVE_PROTOCOL"
        else:
            category = (
                "CONFIGURATION_ERROR" if "invalid argument" in lowered
                else "LIVE_PROTOCOL"
            )
        return LiveValidationFailure(model, category, text)

    async def resolve(self, *, force: bool = False) -> LiveModelResolution:
        if self._resolution is not None and not force:
            return self._resolution
        with _CACHE_LOCK:
            cached = _CACHE.get(self._cache_key)
        if cached is not None and not force:
            self._resolution = cached
            return cached

        started = time.monotonic()
        try:
            available = await asyncio.wait_for(
                self._list_models(timeout=self.total_timeout),
                timeout=self.total_timeout,
            )
        except Exception as exc:
            raise LiveModelDiscoveryError(
                f"Impossible de lister les modèles Gemini: {exc}"
            ) from exc

        failures: list[LiveValidationFailure] = []
        candidates = []
        for model in available:
            reason = live_candidate_rejection(model)
            if reason is None:
                candidates.append(model)
                continue
            name = _model_name(model)
            # N'encombre pas le rapport avec tous les modèles non-Live. Les
            # variantes Gemini 3 Live spécialisées restent, elles, explicites.
            if "gemini-3" in name.lower() and "live" in name.lower():
                failures.append(LiveValidationFailure(
                    name, "FILTERED_SPECIALIZED_MODEL", reason
                ))

        candidates.sort(
            key=lambda model: (
                bool(self.hint and _model_name(model) == self.hint),
                *_candidate_score(model),
            ),
            reverse=True,
        )
        if not candidates:
            detail = " | ".join(
                f"{item.model} [{item.category}]: {item.detail}"
                for item in failures
            )
            raise LiveModelDiscoveryError(
                "Aucun modèle Gemini 3 généraliste compatible "
                f"Live/BidiGenerateContent n'est disponible. {detail}".strip(),
                failures=tuple(failures),
            )

        for skipped in candidates[self.max_candidates:]:
            failures.append(LiveValidationFailure(
                _model_name(skipped), "DISCOVERY_CANDIDATE_LIMIT",
                f"limite de {self.max_candidates} candidat(s) atteinte",
            ))

        for candidate in candidates[:self.max_candidates]:
            name = _model_name(candidate)
            remaining = self.total_timeout - (time.monotonic() - started)
            if remaining <= 0:
                failures.append(LiveValidationFailure(
                    name, "LIVE_TIMEOUT", "budget total de découverte épuisé"
                ))
                break
            timeout = min(self.connect_timeout, remaining)
            try:
                await self._validate(name, timeout=timeout)
            except Exception as exc:
                failures.append(self._validation_failure(name, exc))
                continue
            resolution = LiveModelResolution(name, diagnostics=tuple(failures))
            self._resolution = resolution
            with _CACHE_LOCK:
                _CACHE[self._cache_key] = resolution
            return resolution

        detail = " | ".join(
            f"{item.model} [{item.category}]: {item.detail}"
            for item in failures
        )
        raise LiveModelDiscoveryError(
            "Aucun modèle Gemini 3 Live listé n'a terminé l'échange de "
            f"validation. {detail}",
            failures=tuple(failures),
        )


def clear_discovery_cache_for_tests() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
