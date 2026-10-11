"""Exécution asynchrone et bornée des tâches routées."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import BackgroundModelConfig
from .discovery import LiveModelResolver
from .gateway import BackgroundModelGateway
from .models import BackgroundTask, RoutingDecision, TaskComplexity
from .quota import ComplexModelQuota, QuotaLease


@dataclass
class ExecutionResult:
    text: str
    summary: str
    files: list[str] = field(default_factory=list)
    tokens: int = 0
    transport: str | None = None
    completion_signal: str | None = None
    sources: list[str] = field(default_factory=list)


class TaskExecutionError(RuntimeError):
    pass


class TaskExecutor:
    def __init__(self, client, config: BackgroundModelConfig, quota: ComplexModelQuota, output_dir: Path, resolver: LiveModelResolver, gateway=None):
        self.client, self.config, self.quota = client, config, quota
        self.resolver = resolver
        self.gateway = gateway or BackgroundModelGateway(
            client,
            retry_attempts=config.retry_attempts,
            retry_base_delay=config.retry_base_delay,
        )
        self.output_dir = Path(output_dir)

    async def execute(self, task: BackgroundTask, route: RoutingDecision, update) -> ExecutionResult:
        if route.complexity is TaskComplexity.COMPLEX:
            expected_model = self.config.complex_model
        else:
            try:
                resolution = await asyncio.wait_for(
                    self.resolver.resolve(), timeout=self.config.timeout_seconds
                )
            except asyncio.TimeoutError as exc:
                raise TaskExecutionError("Timeout pendant la résolution du modèle Gemini 3 Live.") from exc
            except Exception as exc:
                raise TaskExecutionError(f"Modèle Gemini 3 Live indisponible: {exc}") from exc
            expected_model = self.config.model_for_complexity(
                route.complexity, live_model=resolution.model
            )
        if route.model != expected_model:
            raise TaskExecutionError(
                f"Modèle de tâche incohérent: {route.complexity.value} exige {expected_model}."
            )

        complex_call = route.complexity is TaskComplexity.COMPLEX
        lease: QuotaLease | None = None
        response = None
        try:
            if complex_call:
                estimate = min(self.config.complex_tpm, max(1, len(task.description) // 3))
                lease = await self.quota.reserve(estimate)
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
            if complex_call:
                operation = self.gateway.generate_classic(
                    route.model,
                    prompt,
                    system_instruction="Exécuteur interne d'une tâche complexe Jarvis. Fournis le livrable demandé en français.",
                    tools=tools,
                    temperature=0.25,
                    before_attempt=lambda: self.quota.mark_attempt(lease),
                )
                response = await asyncio.wait_for(
                    operation, timeout=self.config.timeout_seconds
                )
            else:
                operation = self.gateway.generate_live(
                    route.model,
                    prompt,
                    system_instruction="Exécuteur interne d'une tâche Jarvis. Fournis uniquement le livrable demandé en français.",
                    tools=tools,
                    temperature=0.25,
                    timeout_seconds=self.config.timeout_seconds,
                )
                response = await asyncio.wait_for(
                    operation, timeout=self.config.timeout_seconds + 1.0
                )
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
                path = (self.output_dir / f"{slug}-{task.id[:8]}.md").resolve()
                path.write_text(f"# {task.title}\n\n{text}\n", encoding="utf-8")
                files.append(str(path))
            return ExecutionResult(
                text=text,
                summary=summary,
                files=files,
                tokens=response.total_tokens,
                transport="GenerateContent" if complex_call else "Live/BidiGenerateContent",
                completion_signal=getattr(response, "completion_signal", None),
                sources=list(getattr(response, "sources", None) or []),
            )
        except asyncio.TimeoutError as exc:
            raise TaskExecutionError(
                f"Délai d'exécution dépassé ({self.config.timeout_seconds:.0f} s); la session a été annulée et fermée."
            ) from exc
        finally:
            if lease is not None:
                self.quota.finish(lease, response.total_tokens if response is not None else 0)
