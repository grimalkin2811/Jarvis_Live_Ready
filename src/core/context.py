"""Bounded, provider-independent context assembly; no implicit collection.

Every item is untrusted data, not an instruction. This engine does not own
its sources, perform network I/O, or create a worker until build_async is used.
"""
from __future__ import annotations

import asyncio
import contextvars
import re
import threading
import unicodedata
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .services import ServiceRegistry

MAX_BUDGET = 16_000
MAX_ITEMS = 40
MAX_ITEM_CHARS = 8_000
MAX_PROJECT_ENTRIES = 40


@dataclass(frozen=True)
class ProjectEntry:
    """Caller-supplied project data, not a project store or an instruction."""
    reference: str
    kind: str  # e.g. goal, decision, task
    text: str


@dataclass(frozen=True)
class ContextRequest:
    query: str = ""
    budget_tokens: int = 2048
    conversation: bool = True
    memory: bool = False
    runtime: bool = True
    project_data: str | None = None
    environment_data: str | None = None
    allow_environment: bool = False
    intent: str | None = None
    project_entries: tuple[ProjectEntry, ...] = ()


@dataclass(frozen=True)
class ContextItem:
    source: str
    reference: str
    text: str
    estimated_tokens: int
    truncated: bool = False
    trust: str = "untrusted_data"
    priority: int = 0
    selection_reason: str = "source_order"


@dataclass(frozen=True)
class ContextExclusion:
    source: str
    reference: str
    reason: str
    priority: int | None = None


@dataclass(frozen=True)
class ContextSnapshot:
    items: tuple[ContextItem, ...]
    exclusions: tuple[ContextExclusion, ...]
    missing: tuple[str, ...]
    estimated_tokens: int
    budget_tokens: int
    instructions: tuple[str, ...] = ()
    # Metadata differs between builds but does not change content equivalence.
    snapshot_id: str = field(default_factory=lambda: uuid.uuid4().hex, compare=False)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc), compare=False)
    conversation_id: str | None = None
    intent: str | None = None
    sources: tuple[str, ...] = ()


def _cost(text: str) -> int:
    # Approximation only: does not include metadata or model-specific tokens.
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


def _terms(text: str) -> frozenset[str]:
    # Bounded, deterministic lexical heuristic, not embeddings or semantics.
    normalized = unicodedata.normalize("NFKD", text[:MAX_ITEM_CHARS].casefold())
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    return frozenset(word for word in re.findall(r"[^\W_]+", normalized) if len(word) >= 3)


class ContextEngine:
    def __init__(self, services: ServiceRegistry, *, activity_boundary=None) -> None:
        self._services = services
        self._activity_boundary = activity_boundary
        self._async_gate = threading.Lock()

    async def build_async(self, request: ContextRequest, *, timeout: float = 5.0) -> ContextSnapshot:
        """Keep synchronous local sources off the caller's event loop.

        A timed-out source cannot be killed safely in Python. Only one build
        per engine may remain in flight; it must return before another starts.
        Callers must not attach unbounded/network sources. Timeout/cancellation
        does not imply that the underlying source has stopped.
        """
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if not self._async_gate.acquire(blocking=False):
            raise RuntimeError("A context build is already running")

        def work():
            try:
                return self.build(request)
            finally:
                self._async_gate.release()

        # Submit synchronously through the launch gate: scheduling a to_thread
        # coroutine would defer executor submission until after STOPPING.
        # Preserve to_thread's contextvars propagation and shield the future.
        loop = asyncio.get_running_loop()
        context = contextvars.copy_context()
        submitted = []
        try:
            def submit():
                submitted.append(loop.run_in_executor(None, context.run, work))
            if self._activity_boundary is None:
                submit()
            elif not self._activity_boundary.launch(submit):
                raise RuntimeError("Runtime en cours d'arrêt : activité refusée.")
        except BaseException:
            self._async_gate.release()
            raise
        task = submitted[0]
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout)
        except BaseException:
            # Retrieve any eventual exception from a worker which outlives us.
            def consume(done):
                if not done.cancelled():
                    done.exception()
            task.add_done_callback(consume)
            raise

    def build(self, request: ContextRequest) -> ContextSnapshot:
        """Build synchronously; callers with an event loop should use build_async."""
        if self._activity_boundary is None:
            return self._build(request)
        with self._activity_boundary.activity("context.build"):
            return self._build(request)

    def _build(self, request: ContextRequest) -> ContextSnapshot:
        if not isinstance(request.budget_tokens, int) or isinstance(request.budget_tokens, bool):
            raise TypeError("budget_tokens must be an integer")
        if not 0 <= request.budget_tokens <= MAX_BUDGET:
            raise ValueError(f"budget_tokens must be between 0 and {MAX_BUDGET}")
        if not isinstance(request.query, str):
            raise TypeError("query must be a string")
        if not isinstance(request.project_entries, tuple) or len(request.project_entries) > MAX_PROJECT_ENTRIES:
            raise ValueError(f"project_entries must be a tuple of at most {MAX_PROJECT_ENTRIES} entries")
        candidates: list[tuple[str, str, str, int, str]] = []
        exclusions: list[ContextExclusion] = []
        missing: list[str] = []
        conversation_id = None
        query_terms = _terms(request.query)

        def add(source: str, reference: str, value: str, priority: int, reason: str) -> None:
            if not isinstance(value, str):
                exclusions.append(ContextExclusion(source, reference, "invalid_text", priority))
            elif value:
                candidates.append((source, reference, value, priority, reason))
            else:
                exclusions.append(ContextExclusion(source, reference, "empty", priority))

        add("request", "query", request.query, 1000, "explicit_request")
        if request.conversation:
            try:
                conversation = self._services.require("conversation")
                if callable(getattr(conversation, "context_messages", None)):
                    cid, messages = conversation.context_messages(limit=30)
                else:
                    messages = conversation.get_messages(limit=30)
                    cid = getattr(conversation, "conversation_id", None)
                messages = messages[-30:]
                conversation_id = str(cid)[:80] if cid is not None else None
                # Recency is primary; lexical matches are metadata, not a
                # claim that non-matching exchanges are irrelevant.
                for index, message in enumerate(reversed(messages)):
                    role = str(message.role)[:40]
                    turn = str(message.turn)[:40]
                    ref = f"recent:{index}:{role}:{turn}"
                    relevant = bool(query_terms & _terms(message.text)) if isinstance(message.text, str) else False
                    add("conversation", ref, message.text, 900 - index,
                        "recent_and_query_match" if relevant else "recent")
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
                    records = result.get("memories", [])[:10]
                    def importance(record):
                        try:
                            return max(1, min(5, int(record.get("importance", 1))))
                        except (TypeError, ValueError):
                            return 1
                    records = sorted(records, key=lambda r: (-importance(r), str(r.get("id", ""))))
                    for record in records:
                        rank = importance(record)
                        add("memory", str(record.get("id", "unknown"))[:80], record.get("content", ""),
                            700 + rank, f"search_match_importance_{rank}")
            except Exception:
                missing.append("memory")
        else:
            exclusions.append(ContextExclusion("memory", "*", "not_requested"))
        if request.project_data is not None:
            add("project", "caller", request.project_data, 600, "caller_supplied_unfiltered")
        if request.project_entries:
            for entry in request.project_entries:
                if not isinstance(entry, ProjectEntry) or not all(isinstance(x, str) for x in
                                                                  (entry.reference, entry.kind, entry.text)):
                    raise TypeError("project_entries must contain ProjectEntry strings")
                ref = f"{entry.kind[:40]}:{entry.reference[:80]}"
                if query_terms and not query_terms.intersection(_terms(entry.text)):
                    exclusions.append(ContextExclusion("project", ref, "no_lexical_match", 600))
                else:
                    add("project", ref, entry.text, 600, "query_match" if query_terms else "no_query_filter")
        if request.project_data is None and not request.project_entries:
            missing.append("project")
        if request.runtime:
            try:
                board = self._services.require("status")
                state = board.snapshot()
                for key in ("engine_state", "audio_state", "memory_state", "tasks_state", "wake_word"):
                    value = state.get(key)
                    if value is not None:
                        add("runtime", key, str(getattr(value, "value", value))[:100], 500, "status_allowlist")
            except Exception:
                missing.append("runtime")
        else:
            exclusions.append(ContextExclusion("runtime", "*", "not_requested"))
        if request.environment_data is not None:
            if request.allow_environment:
                add("environment", "caller", request.environment_data, 400, "caller_supplied")
            else:
                exclusions.append(ContextExclusion("environment", "caller", "not_authorized", 400))
        else:
            missing.append("environment")

        items: list[ContextItem] = []
        used = 0
        for index, (source, reference, text, priority, reason) in enumerate(candidates):
            if index >= MAX_ITEMS:
                exclusions.append(ContextExclusion(source, reference, "item_limit", priority))
                continue
            if used >= request.budget_tokens:
                exclusions.append(ContextExclusion(source, reference, "budget", priority))
                continue
            bounded = text[:MAX_ITEM_CHARS]
            sanitized = bounded.encode("utf-8", errors="replace").decode("utf-8")
            if sanitized != bounded:
                exclusions.append(ContextExclusion(source, reference, "invalid_unicode_replaced", priority))
            selected = _fit(sanitized, request.budget_tokens - used)
            if not selected:
                exclusions.append(ContextExclusion(source, reference, "budget", priority))
                continue
            cost = _cost(selected)
            truncated = len(selected) < len(text)
            items.append(ContextItem(source, reference, selected, cost, truncated, "untrusted_data", priority, reason))
            used += cost
            if truncated:
                exclusions.append(ContextExclusion(source, reference, "truncated", priority))
        sources = tuple(dict.fromkeys(item.source for item in items))
        intent = request.intent[:80] if isinstance(request.intent, str) else None
        return ContextSnapshot(tuple(items), tuple(exclusions), tuple(missing), used, request.budget_tokens,
                               conversation_id=conversation_id, intent=intent, sources=sources)
