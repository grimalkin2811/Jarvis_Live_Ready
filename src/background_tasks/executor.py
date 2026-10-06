"""Exécution isolée des tâches routées, sans dépendance à Gemini Live principal."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import BackgroundModelConfig
from .gateway import BackgroundModelGateway
from .models import BackgroundTask, RoutingDecision, TaskComplexity
from .quota import ComplexModelQuota


@dataclass
class ExecutionResult:
    text: str
    summary: str
    files: list[str] = field(default_factory=list)
    tokens: int = 0


class TaskExecutionError(RuntimeError):
    pass


class TaskExecutor:
    def __init__(self, client, config: BackgroundModelConfig, quota: ComplexModelQuota, output_dir: Path):
        self.client, self.config, self.quota = client, config, quota
        self.gateway = BackgroundModelGateway(client)
        self.output_dir = Path(output_dir)

    async def execute(self, task: BackgroundTask, route: RoutingDecision, update) -> ExecutionResult:
        complex_call = route.complexity is TaskComplexity.COMPLEX
        acquired = False
        reservation = 0
        response = None
        try:
            if complex_call:
                reservation = await self.quota.acquire(estimated_tokens=min(250_000, max(1, len(task.description) // 3)))
                acquired = True
            update(20, "Préparation de l'analyse", 1, route.estimated_steps)
            tools = [{"google_search": {}}] if route.requires_tools else None
            if tools:
                update(30, "Recherche et collecte des sources", min(2, route.estimated_steps), route.estimated_steps, waiting=True)
            prompt = (
                "Tu exécutes une tâche d'arrière-plan pour Jarvis. Produis un résultat autonome, exact, structuré en français. "
                "Pour une recherche web, cite les URL/sources fournies par le grounding et distingue les faits des incertitudes. "
                "Ne prétends jamais avoir créé une ressource externe. Commence par un résumé bref, puis le résultat détaillé.\n\n"
                f"TITRE: {task.title}\nDEMANDE: {task.description}"
            )
            update(55, "Analyse et synthèse", max(2, route.estimated_steps - 1), route.estimated_steps)
            operation = (
                self.gateway.generate(route.model, prompt, tools=tools, temperature=0.25)
                if complex_call else
                self.gateway.generate_live(
                    route.model, prompt,
                    system_instruction="Exécuteur interne de tâche. Fournis uniquement le livrable demandé en français.",
                    tools=tools, temperature=0.25,
                )
            )
            response = await asyncio.wait_for(operation, timeout=self.config.timeout_seconds)
            text = response.text.strip()
            if not text:
                raise TaskExecutionError("Le modèle d'exécution a renvoyé une réponse vide.")
            summary = text.split("\n\n", 1)[0].strip()[:1000]
            files: list[str] = []
            request = task.description.lower()
            if any(word in request for word in ("document", "fichier", "rapport", "markdown")):
                update(85, "Génération du document", route.estimated_steps, route.estimated_steps)
                self.output_dir.mkdir(parents=True, exist_ok=True)
                slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", task.title).strip("-")[:48] or "resultat"
                path = self.output_dir / f"{slug}-{task.id[:8]}.md"
                path.write_text(f"# {task.title}\n\n{text}\n", encoding="utf-8")
                files.append(str(path))
            return ExecutionResult(text=text, summary=summary, files=files, tokens=response.total_tokens)
        except asyncio.TimeoutError as exc:
            raise TaskExecutionError(f"Délai d'exécution dépassé ({self.config.timeout_seconds:.0f} s). Réessaie plus tard.") from exc
        finally:
            if acquired:
                self.quota.release(reservation, response.total_tokens if response is not None else 0)
