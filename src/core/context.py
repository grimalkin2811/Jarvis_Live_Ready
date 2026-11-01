"""Bounded, provider-independent context assembly; no implicit collection.

All returned content is untrusted data, never a system instruction. The engine
has no model, GUI, background worker, persistence or network dependency.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .services import ServiceRegistry

MAX_BUDGET = 16_000
MAX_ITEMS = 40
MAX_ITEM_CHARS = 8_000


@dataclass(frozen=True)
class ContextRequest:
    query: str = ""
    budget_tokens: int = 2048
    conversation: bool = True
    memory: bool = False
    runtime: bool = True
    project_data: str | None = None
    # Environment data is supplied by the caller, never fetched by the engine.
    environment_data: str | None = None
    allow_environment: bool = False


@dataclass(frozen=True)
class ContextItem:
    source: str
    reference: str
    text: str
    estimated_tokens: int
    truncated: bool = False
    trust: str = "untrusted_data"


@dataclass(frozen=True)
class ContextExclusion:
    source: str
    reference: str
    reason: str


@dataclass(frozen=True)
class ContextSnapshot:
    items: tuple[ContextItem, ...]
    exclusions: tuple[ContextExclusion, ...]
    missing: tuple[str, ...]
    estimated_tokens: int
    budget_tokens: int
    # Intentionally empty. Callers own trusted instructions separately.
    instructions: tuple[str, ...] = ()


def _cost(text: str) -> int:
    # UTF-8 bytes are stable across providers and conservative for many
    # non-Latin scripts; this is not a model tokenizer or a hard API limit.
    return (len(text.encode("utf-8")) + 3) // 4


def _fit(text: str, budget: int) -> str:
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if _cost(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


class ContextEngine:
    def __init__(self, services: ServiceRegistry) -> None:
        self._services = services

    def build(self, request: ContextRequest) -> ContextSnapshot:
        if not isinstance(request.budget_tokens, int) or isinstance(request.budget_tokens, bool):
            raise TypeError("budget_tokens must be an integer")
        if not 0 <= request.budget_tokens <= MAX_BUDGET:
            raise ValueError(f"budget_tokens must be between 0 and {MAX_BUDGET}")
        candidates: list[tuple[str, str, str]] = []
        exclusions: list[ContextExclusion] = []
        missing: list[str] = []

        def add(source: str, reference: str, value: str) -> None:
            if not isinstance(value, str):
                exclusions.append(ContextExclusion(source, reference, "invalid_text"))
            elif value:
                candidates.append((source, reference, value))

        add("request", "query", request.query)
        if request.conversation:
            try:
                conversation = self._services.require("conversation")
                messages = conversation.get_messages(limit=30)[-30:]
                # Most recent messages have priority, with stable references.
                for index, message in enumerate(reversed(messages)):
                    role = str(message.role)[:40]
                    turn = str(message.turn)[:40]
                    add("conversation", f"recent:{index}:{role}:{turn}", message.text)
            except Exception:
                missing.append("conversation")
        else:
            exclusions.append(ContextExclusion("conversation", "*", "not_requested"))
        if request.memory:
            try:
                memory = self._services.require("memory")
                result = memory.search_memories(request.query[:MAX_ITEM_CHARS], limit=10, mark_accessed=False)
                if not result.get("success"):
                    missing.append("memory")
                else:
                    for record in result.get("memories", [])[:10]:
                        add("memory", str(record.get("id", "unknown"))[:80], record.get("content", ""))
            except Exception:
                missing.append("memory")
        else:
            exclusions.append(ContextExclusion("memory", "*", "not_requested"))
        if request.project_data is not None:
            add("project", "caller", request.project_data)
        else:
            missing.append("project")
        if request.runtime:
            try:
                board = self._services.require("status")
                state = board.snapshot()
                # Allowlist: never serialize arbitrary diagnostics, keys or paths.
                for key in ("engine_state", "audio_state", "memory_state", "tasks_state", "wake_word"):
                    value = state.get(key)
                    if value is not None:
                        add("runtime", key, str(getattr(value, "value", value))[:100])
            except Exception:
                missing.append("runtime")
        else:
            exclusions.append(ContextExclusion("runtime", "*", "not_requested"))
        if request.environment_data is not None:
            if request.allow_environment:
                add("environment", "caller", request.environment_data)
            else:
                exclusions.append(ContextExclusion("environment", "caller", "not_authorized"))
        else:
            missing.append("environment")

        items: list[ContextItem] = []
        used = 0
        for index, (source, reference, text) in enumerate(candidates):
            if index >= MAX_ITEMS:
                exclusions.append(ContextExclusion(source, reference, "item_limit"))
                continue
            if used >= request.budget_tokens:
                exclusions.append(ContextExclusion(source, reference, "budget"))
                continue
            bounded = text[:MAX_ITEM_CHARS]
            selected = _fit(bounded, request.budget_tokens - used)
            if not selected:
                exclusions.append(ContextExclusion(source, reference, "budget"))
                continue
            cost = _cost(selected)
            truncated = len(selected) < len(text)
            items.append(ContextItem(source, reference, selected, cost, truncated))
            used += cost
            if truncated:
                exclusions.append(ContextExclusion(source, reference, "truncated"))
        return ContextSnapshot(tuple(items), tuple(exclusions), tuple(missing), used, request.budget_tokens)
