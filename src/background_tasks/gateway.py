"""Passerelle texte vers Gemini Live ou l'API Generate Content.

Chaque session Live créée ici appartient uniquement à une tâche background et
n'utilise jamais la session Native Audio principale.
"""
from __future__ import annotations

from dataclasses import dataclass

from google.genai import types


@dataclass
class TextModelResponse:
    text: str
    total_tokens: int = 0


class BackgroundModelGateway:
    def __init__(self, client):
        self.client = client

    async def generate_live(self, model: str, prompt: str, *, system_instruction: str, tools=None, temperature=0.2) -> TextModelResponse:
        config = {
            "response_modalities": ["TEXT"],
            "system_instruction": system_instruction,
            "temperature": temperature,
        }
        if tools:
            config["tools"] = tools
        chunks: list[str] = []
        tokens = 0
        async with self.client.aio.live.connect(model=model, config=config) as session:
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=prompt)]),
                turn_complete=True,
            )
            async for response in session.receive():
                usage = getattr(response, "usage_metadata", None)
                tokens = max(tokens, int(getattr(usage, "total_token_count", 0) or 0))
                content = getattr(getattr(response, "server_content", None), "model_turn", None)
                for part in getattr(content, "parts", None) or []:
                    text = getattr(part, "text", None)
                    if text:
                        chunks.append(str(text))
                if getattr(getattr(response, "server_content", None), "turn_complete", False):
                    break
        return TextModelResponse("".join(chunks).strip(), tokens)

    async def generate(self, model: str, prompt: str, *, tools=None, temperature=0.25) -> TextModelResponse:
        config = {"temperature": temperature}
        if tools:
            config["tools"] = tools
        response = await self.client.aio.models.generate_content(model=model, contents=prompt, config=config)
        usage = getattr(response, "usage_metadata", None)
        tokens = int(getattr(usage, "total_token_count", 0) or 0)
        return TextModelResponse(str(getattr(response, "text", "") or "").strip(), tokens)
