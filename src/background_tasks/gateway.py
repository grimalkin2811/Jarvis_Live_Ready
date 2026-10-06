"""Transports Gemini background: Live résolu et Generate Content complexe."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass

from google.genai import types


@dataclass
class TextModelResponse:
    text: str
    total_tokens: int = 0


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
    ) -> TextModelResponse:
        async def operation():
            config = {
                "response_modalities": ["TEXT"],
                "system_instruction": system_instruction,
                "temperature": temperature,
            }
            if tools:
                config["tools"] = tools
            chunks: list[str] = []
            tokens = 0
            turn_complete = False
            async with self.client.aio.live.connect(model=model, config=config) as session:
                await session.send_client_content(
                    turns=types.Content(role="user", parts=[types.Part(text=prompt)]),
                    turn_complete=True,
                )
                async for response in session.receive():
                    usage = getattr(response, "usage_metadata", None)
                    tokens = max(tokens, int(getattr(usage, "total_token_count", 0) or 0))
                    server = getattr(response, "server_content", None)
                    content = getattr(server, "model_turn", None)
                    for part in getattr(content, "parts", None) or []:
                        text = getattr(part, "text", None)
                        if text:
                            chunks.append(str(text))
                    output = getattr(server, "output_transcription", None)
                    if output and getattr(output, "text", None):
                        chunks.append(str(output.text))
                    if getattr(server, "turn_complete", False):
                        turn_complete = True
                        break
            text = "".join(chunks).strip()
            if not turn_complete:
                raise RuntimeError(
                    "La session Gemini Live s'est fermée sans turn_complete "
                    f"(texte partiel: {len(text)} caractère(s))."
                )
            return TextModelResponse(text, tokens)

        return await self._with_bounded_retry(operation)

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
