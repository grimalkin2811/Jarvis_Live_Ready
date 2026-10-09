"""Router background sur le modèle Gemini 3 Live découvert et validé."""
from __future__ import annotations

import asyncio
import json

from .config import BackgroundModelConfig
from .discovery import LiveModelResolver
from .gateway import BackgroundModelGateway
from .models import RoutingDecision, TaskComplexity, TaskPriority


class RoutingError(RuntimeError):
    pass


def parse_json_object(text: str) -> dict:
    """Extrait un objet JSON strict d'une réponse Live éventuellement streamée."""
    raw = str(text or "").strip()
    start = raw.find("{")
    if start < 0:
        raise RoutingError("Le Router Live n'a renvoyé aucun objet JSON.")
    try:
        value, _end = json.JSONDecoder().raw_decode(raw[start:])
    except json.JSONDecodeError as exc:
        raise RoutingError(f"JSON du Router Live invalide: {exc}") from exc
    if not isinstance(value, dict):
        raise RoutingError("La décision du Router Live n'est pas un objet JSON.")
    return value


class TaskRouter:
    def __init__(self, client, config: BackgroundModelConfig, resolver: LiveModelResolver, gateway=None):
        self.client, self.config, self.resolver = client, config, resolver
        self.gateway = gateway or BackgroundModelGateway(
            client,
            retry_attempts=config.retry_attempts,
            retry_base_delay=config.retry_base_delay,
        )

    async def route(self, title: str, description: str, priority: TaskPriority) -> RoutingDecision:
        try:
            resolution = await asyncio.wait_for(
                self.resolver.resolve(), timeout=self.config.timeout_seconds
            )
        except asyncio.TimeoutError as exc:
            raise RoutingError(
                f"Découverte/validation du modèle Live interrompue après {self.config.timeout_seconds:.0f}s."
            ) from exc
        except Exception as exc:
            raise RoutingError(f"Découverte/validation du modèle Live impossible: {exc}") from exc
        prompt = (
            "Analyse uniquement le routage de cette tâche. Réponds par UN objet JSON, sans markdown, avec exactement: "
            '"complexity" (simple|medium|complex), "task_type" (texte court), "reasoning" (texte), '
            f'"model" ("{resolution.model}" pour simple/medium, "{self.config.complex_model}" pour complex), '
            '"requires_tools" (booléen), "estimated_steps" (entier 1..12), "priority" (low|normal|high). '
            "Utilise complex seulement pour une recherche/production longue, multi-sources ou multi-étapes; "
            "medium pour une comparaison ou analyse modérée; simple pour une demande courte. "
            "Une météo actuelle nécessite un outil de recherche.\n"
            f"Titre: {title}\nPriorité demandée: {priority.value}\nDemande: {description}"
        )
        try:
            response = await asyncio.wait_for(
                self.gateway.generate_live(
                    resolution.model,
                    prompt,
                    system_instruction=(
                        "Tu es le routeur interne de Jarvis. Tu ne réponds jamais à l'utilisateur. "
                        "Émets uniquement la décision JSON demandée."
                    ),
                    temperature=0.1,
                    timeout_seconds=self.config.timeout_seconds,
                ),
                # Garde externe pour les doubles/injections; le gateway réel
                # produit son diagnostic détaillé une seconde plus tôt.
                timeout=(
                    self.config.timeout_seconds
                    + min(1.0, max(0.01, self.config.timeout_seconds * 0.05))
                ),
            )
            data = parse_json_object(response.text)
            required = {
                "complexity", "task_type", "reasoning", "model",
                "requires_tools", "estimated_steps", "priority",
            }
            missing = required - set(data)
            extra = set(data) - required
            if missing:
                raise RoutingError(f"Décision Router incomplète: {sorted(missing)}")
            if extra:
                raise RoutingError(f"Décision Router avec champs inattendus: {sorted(extra)}")
            for field in ("complexity", "task_type", "reasoning", "model", "priority"):
                if not isinstance(data[field], str) or not data[field].strip():
                    raise RoutingError(f"{field} doit être une chaîne non vide.")
            complexity = TaskComplexity(data["complexity"].lower())
            expected_model = self.config.model_for_complexity(
                complexity, live_model=resolution.model
            )
            returned_model = str(data["model"]).removeprefix("models/")
            if returned_model != expected_model:
                raise RoutingError(
                    f"Plan incohérent: {complexity.value} exige {expected_model}, pas {returned_model}."
                )
            if isinstance(data["estimated_steps"], bool) or not isinstance(data["estimated_steps"], int):
                raise RoutingError("estimated_steps doit être un entier.")
            steps = data["estimated_steps"]
            if not 1 <= steps <= 12:
                raise RoutingError("estimated_steps doit être compris entre 1 et 12.")
            if not isinstance(data["requires_tools"], bool):
                raise RoutingError("requires_tools doit être un booléen.")
            return RoutingDecision(
                complexity=complexity,
                model=expected_model,
                reason=str(data["reasoning"])[:1000],
                requires_tools=data["requires_tools"],
                estimated_steps=steps,
                priority=TaskPriority(str(data["priority"]).lower()),
                task_type=str(data["task_type"])[:100],
            )
        except asyncio.TimeoutError as exc:
            raise RoutingError(
                "Le cycle Live du Router n'a pas produit de signal de fin "
                f"sous {self.config.timeout_seconds:.0f}s."
            ) from exc
        except RoutingError:
            raise
        except Exception as exc:
            raise RoutingError(f"Routage Gemini Live impossible: {exc}") from exc
