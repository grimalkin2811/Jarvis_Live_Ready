"""Transports Gemini background: Live résolu et Generate Content complexe."""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import dataclass

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


class BackgroundServiceError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, external_unavailable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.external_unavailable = external_unavailable


def api_status_code(exc: BaseException) -> int | None:
    for value in (getattr(exc, "code", None), getattr(exc, "status_code", None)):
        try:
            code = int(value)
            if code in (429, 503):
                return code
        except (TypeError, ValueError):
            pass
    text = str(exc).lower()
    if "429" in text or "resource_exhausted" in text:
        return 429
    if "503" in text or "unavailable" in text or "high demand" in text:
        return 503
    return None


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

    async def _with_bounded_retry(self, operation, *, before_attempt=None):
        last_exc = None
        for attempt in range(1, self.retry_attempts + 1):
            await self._before_attempt(before_attempt)
            try:
                return await operation()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                code = api_status_code(exc)
                if code not in (429, 503):
                    raise
                last_exc = exc
                if attempt >= self.retry_attempts:
                    label = "quota/rate limit" if code == 429 else "service temporairement indisponible"
                    raise BackgroundServiceError(
                        f"Gemini {label} après {attempt} tentative(s): {exc}",
                        status_code=code,
                        external_unavailable=True,
                    ) from exc
                await asyncio.sleep(self.retry_base_delay * (2 ** (attempt - 1)))
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

        cycle = self._with_bounded_retry(operation)
        if timeout_seconds is None:
            return await cycle
        try:
            return await asyncio.wait_for(cycle, timeout=max(0.001, float(timeout_seconds)))
        except asyncio.TimeoutError as exc:
            elapsed = time.monotonic() - trace.started_at
            raise BackgroundServiceError(
                "Timeout Gemini Live "
                f"après {elapsed:.1f}s; phase={trace.phase}; tentative={trace.attempt}; "
                f"messages={trace.messages}; audio={trace.audio_messages}; "
                f"fragments_transcription={trace.transcription_fragments}; "
                f"caractères_transcrits={trace.transcription_chars}; "
                f"turn_complete={trace.turn_complete}; session_fermée={trace.session_closed}; "
                f"dernier_événement={trace.last_event}."
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
