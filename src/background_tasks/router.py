"""Router Gemini 3 Flash Preview via l'API classique Generate Content."""
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
        allowed_models = sorted({
            self.config.simple_model,
            self.config.medium_model,
            self.config.complex_model,
        })
        schema = {
            "type": "object",
            "properties": {
                "complexity": {"type": "string", "enum": ["simple", "medium", "complex"]},
                "task_type": {"type": "string"},
                "reasoning": {"type": "string"},
                "model": {"type": "string", "enum": allowed_models},
                "requires_tools": {"type": "boolean"},
                "estimated_steps": {"type": "integer", "minimum": 1, "maximum": 12},
                "priority": {"type": "string", "enum": ["low", "normal", "high"]},
            },
            "required": [
                "complexity", "task_type", "reasoning", "model",
                "requires_tools", "estimated_steps", "priority",
            ],
        }
        prompt = (
            "Analyse uniquement le routage de cette tâche. "
            "Utilise complex seulement pour une recherche/production réellement longue, "
            "multi-sources ou multi-étapes; medium pour une comparaison ou analyse modérée; "
            "simple pour une demande courte. Une météo actuelle nécessite Google Search.\n"
            f"Mapping obligatoire: simple={self.config.simple_model}; "
            f"medium={self.config.medium_model}; complex={self.config.complex_model}.\n"
            f"Titre: {title}\nPriorité demandée: {priority.value}\nDemande: {description}"
        )
        try:
            response = await self.gateway.generate(
                self.config.router_model,
                prompt,
                system_instruction=(
                    "Tu es le routeur interne de Jarvis. Tu ne réponds jamais à l'utilisateur. "
                    "Retourne exclusivement l'objet JSON conforme au schéma."
                ),
                temperature=0.1,
                response_json_schema=schema,
            )
            text = response.text.strip()
            if not text:
                raise RoutingError("Le router Gemini a renvoyé une réponse vide.")
            data = json.loads(text)
            complexity = TaskComplexity(data["complexity"])
            expected_model = self.config.model_for_complexity(complexity)
            returned_model = str(data["model"])
            if returned_model != expected_model:
                raise RoutingError(
                    f"Plan de routage incohérent: {complexity.value} doit utiliser {expected_model}, pas {returned_model}."
                )
            return RoutingDecision(
                complexity=complexity,
                model=expected_model,
                reason=str(data["reasoning"])[:1000],
                requires_tools=bool(data["requires_tools"]),
                estimated_steps=max(1, min(12, int(data["estimated_steps"]))),
                priority=TaskPriority(data.get("priority", priority.value)),
                task_type=str(data["task_type"])[:100],
            )
        except RoutingError:
            raise
        except Exception as exc:
            raise RoutingError(f"Routage Gemini impossible: {exc}") from exc
