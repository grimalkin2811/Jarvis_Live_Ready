"""Transports Gemini background: Live résolu et Generate Content complexe."""
from __future__ import annotations

import asyncio
import inspect
import random
import re
import time
from dataclasses import dataclass, field

from google.genai import types


@dataclass
class TextModelResponse:
    text: str
    total_tokens: int = 0


@dataclass
class LiveCycleTrace:
    started_at: float
    attempt: int = 0
    phase: str = "initialisation"
    messages: int = 0
    transcription_fragments: int = 0
    transcription_chars: int = 0
    audio_messages: int = 0
    turn_complete: bool = False
    session_closed: bool = False
    last_event: str = "aucun"
    last_error: str = "aucune"
    error_history: list[str] = field(default_factory=list)
    last_external_category: str | None = None
    last_external_status: int | None = None
    last_external_live_code: int | None = None


@dataclass(frozen=True)
class BackgroundErrorInfo:
    category: str
    status_code: int | None = None
    live_close_code: int | None = None
    retryable: bool = False
    external: bool = False


class BackgroundServiceError(RuntimeError):
    def __init__(
        self, message: str, *, status_code: int | None = None,
        live_close_code: int | None = None, category: str | None = None,
        external_unavailable: bool = False,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.live_close_code = live_close_code
        self.category = category
        self.external_unavailable = external_unavailable


def _integer_code(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _structured_error_values(error) -> tuple[int | None, int | None, str]:
    if isinstance(error, BaseException):
        status = _integer_code(getattr(error, "status_code", None))
        direct_code = _integer_code(getattr(error, "code", None))
        received = getattr(error, "rcvd", None)
        live_code = _integer_code(getattr(received, "code", None))
        if live_code is None and direct_code not in (429, 503):
            live_code = direct_code
        if status is None and direct_code in (429, 503):
            status = direct_code
        reasons = [
            str(error),
            str(getattr(error, "reason", "") or ""),
            str(getattr(received, "reason", "") or ""),
        ]
        return status, live_code, " ".join(reasons).strip()
    return None, None, str(error or "")


def classify_background_error(error) -> BackgroundErrorInfo:
    """Classification unique pour runtime et harness, structurée si possible."""
    explicit = getattr(error, "category", None) if isinstance(error, BaseException) else None
    status, live_code, raw_text = _structured_error_values(error)
    text = raw_text.lower()
    if live_code is None and re.search(r"\b1011\b", text):
        live_code = 1011

    if explicit:
        category = str(explicit)
        return BackgroundErrorInfo(
            category, status, live_code,
            retryable=category in {"EXTERNAL_QUOTA", "EXTERNAL_SERVICE", "LIVE_RESOURCE_EXHAUSTED"},
            external=category.startswith("EXTERNAL_") or category == "LIVE_RESOURCE_EXHAUSTED",
        )
    for category in (
        "LIVE_RESOURCE_EXHAUSTED", "EXTERNAL_QUOTA", "EXTERNAL_SERVICE",
        "LOCAL_QUOTA_GUARD", "TIMEOUT", "LIVE_PROTOCOL",
    ):
        if category.lower() in text:
            return BackgroundErrorInfo(
                category, status, live_code,
                retryable=category in {"LIVE_RESOURCE_EXHAUSTED", "EXTERNAL_QUOTA", "EXTERNAL_SERVICE"},
                external=category.startswith("EXTERNAL_") or category == "LIVE_RESOURCE_EXHAUSTED",
            )
    # La fermeture Live 1011 est générique. Elle ne devient une saturation de
    # ressources que si le motif fournisseur le dit explicitement.
    resource_exhausted = bool(re.search(
        r"resource(?:\s+has\s+been|[_\s-]+)\s*exhausted", text
    ))
    if live_code == 1011 and resource_exhausted:
        return BackgroundErrorInfo("LIVE_RESOURCE_EXHAUSTED", status, 1011, True, True)
    if status == 429 or "429" in text or "resource_exhausted" in text:
        return BackgroundErrorInfo("EXTERNAL_QUOTA", 429, live_code, True, True)
    if status == 503 or "503" in text or "unavailable" in text or "high demand" in text:
        return BackgroundErrorInfo("EXTERNAL_SERVICE", 503, live_code, True, True)
    if "quota gemini 3.8 épuisé" in text or "appels/minute" in text or "tokens/minute" in text:
        return BackgroundErrorInfo("LOCAL_QUOTA_GUARD")
    if "timeout" in text or "délai" in text:
        return BackgroundErrorInfo("TIMEOUT")
    if "sans turn_complete" in text or "sans transcription audio" in text:
        return BackgroundErrorInfo("LIVE_PROTOCOL")
    return BackgroundErrorInfo("CODE_OR_PROTOCOL", status, live_code)


def api_status_code(exc: BaseException) -> int | None:
    return classify_background_error(exc).status_code


class BackgroundModelGateway:
    def __init__(self, client, *, retry_attempts: int = 3, retry_base_delay: float = 1.0):
        self.client = client
        self.retry_attempts = max(1, int(retry_attempts))
        self.retry_base_delay = max(0.0, float(retry_base_delay))

    async def _before_attempt(self, callback) -> None:
        if callback is None:
            return
        value = callback()
        if inspect.isawaitable(value):
            await value

    async def _with_bounded_retry(
        self, operation, *, before_attempt=None, retry_live_resource: bool = False,
    ):
        last_exc = None
        for attempt in range(1, self.retry_attempts + 1):
            await self._before_attempt(before_attempt)
            try:
                return await operation()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                info = classify_background_error(exc)
                live_resource = info.category == "LIVE_RESOURCE_EXHAUSTED"
                max_attempts = min(self.retry_attempts, 2) if live_resource else self.retry_attempts
                can_retry = info.category in {"EXTERNAL_QUOTA", "EXTERNAL_SERVICE"}
                can_retry = can_retry or (retry_live_resource and live_resource)
                if not can_retry:
                    raise
                last_exc = exc
                if attempt >= max_attempts:
                    raise BackgroundServiceError(
                        f"Gemini {info.category} après {attempt} tentative(s): {exc}",
                        status_code=info.status_code,
                        live_close_code=info.live_close_code,
                        category=info.category,
                        external_unavailable=True,
                    ) from exc
                delay = self.retry_base_delay * (2 ** (attempt - 1))
                # Une seule reprise 1011, espacée avec jitter, évite que deux
                # tâches concurrentes repartent exactement au même instant.
                if live_resource:
                    delay += random.uniform(0.0, max(0.05, delay * 0.25))
                await asyncio.sleep(delay)
        raise last_exc  # pragma: no cover

    async def generate_live(
        self,
        model: str,
        prompt: str,
        *,
        system_instruction: str,
        tools=None,
        temperature: float = 0.2,
        timeout_seconds: float | None = None,
    ) -> TextModelResponse:
        trace = LiveCycleTrace(started_at=time.monotonic())

        async def operation():
            trace.attempt += 1
            trace.phase = "configuration"
            trace.messages = 0
            trace.transcription_fragments = 0
            trace.transcription_chars = 0
            trace.audio_messages = 0
            trace.turn_complete = False
            trace.session_closed = False
            trace.last_event = "configuration"
            trace.last_error = "aucune"
            # Gemini 3.8 Live n'accepte que la modalité de sortie AUDIO.
            # Jarvis ne joue et ne conserve jamais ces octets : seule la
            # transcription officielle de la sortie audio est exploitée.
            config = {
                "response_modalities": ["AUDIO"],
                "output_audio_transcription": {},
                "system_instruction": system_instruction,
                "temperature": temperature,
            }
            if tools:
                config["tools"] = tools
            chunks: list[str] = []
            tokens = 0
            entered = False
            trace.phase = "connexion"
            trace.last_event = "ouverture demandée"
            try:
                async with self.client.aio.live.connect(model=model, config=config) as session:
                    entered = True
                    trace.phase = "envoi"
                    trace.last_event = "session ouverte"
                    await session.send_client_content(
                        turns=types.Content(role="user", parts=[types.Part(text=prompt)]),
                        turn_complete=True,
                    )
                    trace.phase = "réception"
                    trace.last_event = "prompt envoyé"
                    async for response in session.receive():
                        trace.messages += 1
                        trace.last_event = "message serveur"
                        usage = getattr(response, "usage_metadata", None)
                        tokens = max(tokens, int(getattr(usage, "total_token_count", 0) or 0))
                        if getattr(response, "data", None):
                            trace.audio_messages += 1
                            trace.last_event = "audio reçu"
                        server = getattr(response, "server_content", None)
                        # Une sortie AUDIO place le texte utilisable uniquement
                        # dans output_transcription. Les octets sont comptés
                        # pour le diagnostic mais jamais conservés ni joués.
                        output = getattr(server, "output_transcription", None)
                        transcription = getattr(output, "text", None) if output else None
                        if transcription:
                            fragment = str(transcription)
                            chunks.append(fragment)
                            trace.transcription_fragments += 1
                            trace.transcription_chars += len(fragment)
                            trace.last_event = "transcription reçue"
                        if getattr(server, "turn_complete", False):
                            trace.turn_complete = True
                            trace.phase = "fin de tour"
                            trace.last_event = "turn_complete"
                            break
            except asyncio.CancelledError:
                trace.last_error = "CancelledError"
                trace.error_history.append(trace.last_error)
                raise
            except Exception as exc:
                trace.last_error = f"{type(exc).__name__}: {exc}"
                trace.error_history.append(trace.last_error)
                info = classify_background_error(exc)
                if info.external:
                    trace.last_external_category = info.category
                    trace.last_external_status = info.status_code
                    trace.last_external_live_code = info.live_close_code
                raise
            finally:
                # Le finally externe à `async with` n'est atteint qu'après son
                # __aexit__ lorsque la session avait effectivement été ouverte.
                trace.session_closed = entered
            text = "".join(chunks).strip()
            if not trace.turn_complete:
                raise RuntimeError(
                    "La session Gemini Live s'est fermée sans turn_complete "
                    f"(transcription partielle: {len(text)} caractère(s))."
                )
            if not text:
                raise RuntimeError(
                    "Gemini Live a terminé son tour sans transcription audio exploitable."
                )
            trace.phase = "finalisation"
            return TextModelResponse(text, tokens)

        def trace_detail() -> str:
            elapsed = time.monotonic() - trace.started_at
            return (
                f"durée={elapsed:.1f}s; phase={trace.phase}; tentative={trace.attempt}; "
                f"messages={trace.messages}; audio={trace.audio_messages}; "
                f"fragments_transcription={trace.transcription_fragments}; "
                f"caractères_transcrits={trace.transcription_chars}; "
                f"turn_complete={trace.turn_complete}; session_fermée={trace.session_closed}; "
                f"dernier_événement={trace.last_event}; dernière_erreur={trace.last_error}; "
                f"historique_erreurs={trace.error_history[-3:]}"
            )

        async def run_cycle():
            try:
                return await self._with_bounded_retry(
                    operation, retry_live_resource=True
                )
            except BackgroundServiceError as exc:
                raise BackgroundServiceError(
                    f"{exc}; diagnostic Live: {trace_detail()}.",
                    status_code=exc.status_code,
                    live_close_code=exc.live_close_code,
                    category=exc.category,
                    external_unavailable=exc.external_unavailable,
                ) from exc

        cycle = run_cycle()
        if timeout_seconds is None:
            return await cycle
        try:
            return await asyncio.wait_for(cycle, timeout=max(0.001, float(timeout_seconds)))
        except asyncio.TimeoutError as exc:
            # Si une reprise déclenchée par un refus fournisseur finit par
            # consommer le budget restant, conserver la cause externe au lieu
            # de la masquer sous un TIMEOUT générique.
            category = trace.last_external_category or "TIMEOUT"
            raise BackgroundServiceError(
                f"Timeout Gemini Live; {trace_detail()}.",
                status_code=trace.last_external_status,
                live_close_code=trace.last_external_live_code,
                category=category,
                external_unavailable=trace.last_external_category is not None,
            ) from exc

    async def generate_classic(
        self,
        model: str,
        prompt: str,
        *,
        system_instruction: str | None = None,
        tools=None,
        temperature: float = 0.25,
        before_attempt=None,
    ) -> TextModelResponse:
        async def operation():
            config: dict = {"temperature": temperature}
            if system_instruction:
                config["system_instruction"] = system_instruction
            if tools:
                config["tools"] = tools
                # Les outils built-in restent exécutés côté serveur. Désactiver
                # l'AFC client évite le chemin déconseillé d'AsyncModels.
                config["automatic_function_calling"] = {"disable": True}
            response = await self.client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
            usage = getattr(response, "usage_metadata", None)
            tokens = int(getattr(usage, "total_token_count", 0) or 0)
            return TextModelResponse(str(getattr(response, "text", "") or "").strip(), tokens)

        return await self._with_bounded_retry(operation, before_attempt=before_attempt)
