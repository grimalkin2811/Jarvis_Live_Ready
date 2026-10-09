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
from dataclasses import dataclass

from .gateway import BackgroundModelGateway


class LiveModelDiscoveryError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveModelResolution:
    model: str
    transport: str = "Live/BidiGenerateContent"
    validated: bool = True


_CACHE: dict[str, LiveModelResolution] = {}
_CACHE_LOCK = threading.RLock()


def _model_name(model) -> str:
    return str(getattr(model, "name", "") or "").removeprefix("models/")


def _supported_actions(model) -> set[str]:
    raw = getattr(model, "supported_actions", None) or []
    return {re.sub(r"[^a-z]", "", str(item).lower()) for item in raw}


def is_gemini3_live_model(model) -> bool:
    name = _model_name(model).lower()
    actions = _supported_actions(model)
    is_gemini3 = bool(re.search(r"(?:^|-)gemini-3(?:[.-]|$)", name))
    supports_bidi = any("bidigeneratecontent" in action for action in actions)
    return is_gemini3 and "live" in name and supports_bidi


def _candidate_score(model) -> tuple:
    name = _model_name(model).lower()
    versions = tuple(int(value) for value in re.findall(r"\d+", name)[:2])
    stable = not any(word in name for word in ("preview", "experimental", "legacy"))
    standard = not any(word in name for word in ("extended", "thinking"))
    return (stable, standard, versions, name)


class LiveModelResolver:
    def __init__(
        self, client, *, api_key_fingerprint: str = "", hint: str = "",
        list_timeout: float = 30.0, connect_timeout: float = 15.0,
        retry_attempts: int = 3, retry_base_delay: float = 1.0,
    ):
        self.client = client
        self.hint = str(hint or "").strip().removeprefix("models/")
        self.list_timeout = list_timeout
        self.connect_timeout = connect_timeout
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

    async def _list_models(self) -> list:
        async def list_once():
            pager = await asyncio.wait_for(self.client.aio.models.list(), timeout=self.list_timeout)
            models = []
            async for model in pager:
                models.append(model)
            return models

        return await self._retry_external(list_once)

    async def _validate(self, model: str) -> None:
        """Valide un tour complet AUDIO → transcription, pas le seul handshake."""
        gateway = BackgroundModelGateway(
            self.client,
            retry_attempts=self.retry_attempts,
            retry_base_delay=self.retry_base_delay,
        )
        response = await asyncio.wait_for(
            gateway.generate_live(
                model,
                "Réponds uniquement par le mot OK.",
                system_instruction=(
                    "Test technique de disponibilité Jarvis. Prononce uniquement "
                    "le mot OK, sans explication."
                ),
                temperature=0.0,
            ),
            timeout=self.connect_timeout,
        )
        if not response.text.strip():  # défense supplémentaire au contrat gateway
            raise RuntimeError("Échange Live terminé sans transcription textuelle.")

    async def resolve(self, *, force: bool = False) -> LiveModelResolution:
        if self._resolution is not None and not force:
            return self._resolution
        with _CACHE_LOCK:
            cached = _CACHE.get(self._cache_key)
        if cached is not None and not force:
            self._resolution = cached
            return cached

        try:
            available = await self._list_models()
        except Exception as exc:
            raise LiveModelDiscoveryError(f"Impossible de lister les modèles Gemini: {exc}") from exc

        candidates = [model for model in available if is_gemini3_live_model(model)]
        candidates.sort(
            key=lambda model: (
                bool(self.hint and _model_name(model) == self.hint),
                *_candidate_score(model),
            ),
            reverse=True,
        )
        if not candidates:
            raise LiveModelDiscoveryError(
                "Aucun modèle Gemini 3 compatible Live/BidiGenerateContent n'est disponible pour cette clé."
            )

        errors: list[str] = []
        for candidate in candidates:
            name = _model_name(candidate)
            try:
                await self._validate(name)
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
                continue
            resolution = LiveModelResolution(name)
            self._resolution = resolution
            with _CACHE_LOCK:
                _CACHE[self._cache_key] = resolution
            return resolution

        raise LiveModelDiscoveryError(
            "Aucun modèle Gemini 3 Live listé n'a accepté une connexion: " + " | ".join(errors)
        )


def clear_discovery_cache_for_tests() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()
