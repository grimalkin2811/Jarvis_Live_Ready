"""Router Gemini 3 Flash Live : uniquement une décision JSON validée."""
from __future__ import annotations

import json

from .config import BackgroundModelConfig
from .gateway import BackgroundModelGateway
from .models import RoutingDecision, TaskComplexity, TaskPriority


class RoutingError(RuntimeError):
    pass


class TaskRouter:
    def __init__(self, client, config: BackgroundModelConfig, gateway=None):
        self.client, self.config = client, config
        self.gateway = gateway or BackgroundModelGateway(client)

    async def route(self, title: str, description: str, priority: TaskPriority) -> RoutingDecision:
        schema = {
            "type": "object",
            "properties": {
                "complexity": {"type": "string", "enum": ["simple", "medium", "complex"]},
                "reason": {"type": "string"},
                "requires_tools": {"type": "boolean"},
                "estimated_steps": {"type": "integer", "minimum": 1, "maximum": 12},
                "priority": {"type": "string", "enum": ["low", "normal", "high"]},
            },
            "required": ["complexity", "reason", "requires_tools", "estimated_steps", "priority"],
        }
        prompt = (
            "Tu es le routeur interne de tâches de Jarvis. Tu ne parles jamais à l'utilisateur. "
            "Classe complex seulement les recherches/analyses réellement longues, multi-sources ou multi-étapes; "
            "sinon simple ou medium. Réponds exclusivement avec le JSON demandé.\n"
            f"Titre: {title}\nPriorité demandée: {priority.value}\nDemande: {description}"
        )
        try:
            response = await self.gateway.generate_live(
                self.config.router_model,
                prompt,
                system_instruction=(
                    "Routeur interne. Réponds avec un unique objet JSON, sans markdown. "
                    f"Le JSON doit respecter exactement ce schéma: {json.dumps(schema, ensure_ascii=False)}"
                ),
                temperature=0.1,
            )
            text = response.text.strip()
            if not text:
                raise RoutingError("Le router Gemini a renvoyé une réponse vide.")
            data = json.loads(text)
            complexity = TaskComplexity(data["complexity"])
            model = self.config.complex_model if complexity is TaskComplexity.COMPLEX else self.config.simple_model
            return RoutingDecision(
                complexity=complexity,
                model=model,
                reason=str(data["reason"])[:1000],
                requires_tools=bool(data["requires_tools"]),
                estimated_steps=max(1, min(12, int(data["estimated_steps"]))),
                priority=TaskPriority(data.get("priority", priority.value)),
            )
        except RoutingError:
            raise
        except Exception as exc:
            raise RoutingError(f"Routage Gemini impossible: {exc}") from exc
