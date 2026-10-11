"""Modèle d'état immuable à l'extérieur du gestionnaire de tâches v1.8."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskStatus(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_FOR_TOOL = "WAITING_FOR_TOOL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class TaskComplexity(str, Enum):
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


class TaskPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


TERMINAL_STATUSES = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


@dataclass
class RoutingDecision:
    complexity: TaskComplexity
    model: str
    reason: str
    requires_tools: bool
    estimated_steps: int
    priority: TaskPriority = TaskPriority.NORMAL
    task_type: str = "general"


@dataclass
class BackgroundTask:
    id: str
    title: str
    description: str
    created_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    status: TaskStatus = TaskStatus.QUEUED
    priority: TaskPriority = TaskPriority.NORMAL
    complexity: TaskComplexity | None = None
    task_type: str | None = None
    model: str | None = None
    transport: str | None = None
    completion_signal: str | None = None
    sources: list[str] = field(default_factory=list)
    progress: int = 0
    current_step: str = "En attente de routage"
    current_step_number: int = 0
    total_steps: int = 0
    result: str | None = None
    summary: str | None = None
    files: list[str] = field(default_factory=list)
    error: str | None = None
    partial_errors: list[str] = field(default_factory=list)
    seen: bool = False
    routing_reason: str | None = None
    token_usage: int = 0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        value["priority"] = self.priority.value
        value["complexity"] = self.complexity.value if self.complexity else None
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "BackgroundTask":
        data = dict(value)
        data["status"] = TaskStatus(data.get("status", "QUEUED"))
        data["priority"] = TaskPriority(data.get("priority", "normal"))
        if data.get("complexity"):
            data["complexity"] = TaskComplexity(data["complexity"])
        known = cls.__dataclass_fields__
        return cls(**{key: item for key, item in data.items() if key in known})
