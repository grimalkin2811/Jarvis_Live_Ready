"""Passerelle asynchrone des tâches vers l'API Gemini classique.

Cette classe ne connaît volontairement aucune session WebSocket. Le Live reste
la responsabilité exclusive de ``src.gemini_live``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TextModelResponse:
    text: str
    total_tokens: int = 0


class BackgroundModelGateway:
    """Façade unique autour de ``aio.models.generate_content``."""

    def __init__(self, client):
        self.client = client

    async def generate(
        self,
        model: str,
        prompt: str,
        *,
        system_instruction: str | None = None,
        tools=None,
        temperature: float = 0.25,
        response_json_schema: dict | None = None,
    ) -> TextModelResponse:
        config: dict = {"temperature": temperature}
        if system_instruction:
            config["system_instruction"] = system_instruction
        if tools:
            config["tools"] = tools
        if response_json_schema is not None:
            config["response_mime_type"] = "application/json"
            config["response_json_schema"] = response_json_schema

        response = await self.client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config=config,
        )
        usage = getattr(response, "usage_metadata", None)
        tokens = int(getattr(usage, "total_token_count", 0) or 0)
        return TextModelResponse(
            str(getattr(response, "text", "") or "").strip(),
            tokens,
        )
