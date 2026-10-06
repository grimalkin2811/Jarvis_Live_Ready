from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.background_tasks.config import BackgroundModelConfig
from src.background_tasks.executor import ExecutionResult
from src.background_tasks.gateway import BackgroundModelGateway
from src.background_tasks.manager import TaskManager
from src.background_tasks.models import RoutingDecision, TaskComplexity, TaskPriority, TaskStatus
from src.background_tasks.quota import ComplexModelQuota, QuotaExceededError
from src.background_tasks.router import RoutingError, TaskRouter


class FakeRouter:
    def __init__(self, complexity=TaskComplexity.SIMPLE, delay=0, fail=None):
        self.complexity, self.delay, self.fail = complexity, delay, fail
        self.calls = []

    async def route(self, title, description, priority):
        self.calls.append((title, description, priority))
        await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        model = "gemini-3.8-flash" if self.complexity is TaskComplexity.COMPLEX else "gemini-3-flash-live"
        return RoutingDecision(self.complexity, model, "test", False, 3, priority)


class FakeExecutor:
    def __init__(self, delay=0.01, fail=None, files=None):
        self.delay, self.fail, self.files = delay, fail, files or []
        self.running = self.max_running = 0

    async def execute(self, task, route, update):
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            update(40, "Analyse", 2, 3)
            await asyncio.sleep(self.delay)
            if self.fail:
                raise self.fail
            return ExecutionResult("Résultat détaillé", "Résumé", self.files, 123)
        finally:
            self.running -= 1


def wait_for(manager, task_id, statuses, timeout=2):
    statuses = set(statuses)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = manager.get_task(task_id)
        if task and task.status in statuses:
            return task
        time.sleep(0.005)
    raise AssertionError(f"timeout: {manager.get_task(task_id)}")


@pytest.fixture
def make_manager(tmp_path, monkeypatch):
    managers = []
    monkeypatch.setattr("src.background_tasks.manager.notifications.publish", lambda *a: "test")

    def make(router=None, executor=None, config=None):
        manager = TaskManager(
            "fake", router=router or FakeRouter(), executor=executor or FakeExecutor(),
            config=config or BackgroundModelConfig(), storage_path=tmp_path / f"tasks-{len(managers)}.json",
            output_dir=tmp_path / "out", client=object(),
        )
        managers.append(manager)
        return manager

    yield make
    for manager in managers:
        manager.close(wait=True)


def test_create_task_is_immediately_queued(make_manager):
    manager = make_manager(router=FakeRouter(delay=0.1))
    task = manager.create_task("Titre", "Description")
    assert task.id and task.status is TaskStatus.QUEUED


def test_state_changes_to_completed(make_manager):
    manager = make_manager()
    task = wait_for(manager, manager.create_task("T", "D").id, {TaskStatus.COMPLETED})
    assert task.progress == 100 and task.started_at and task.finished_at


def test_simple_task_uses_flash_live(make_manager):
    manager = make_manager(router=FakeRouter(TaskComplexity.SIMPLE))
    task = wait_for(manager, manager.create_task("T", "D").id, {TaskStatus.COMPLETED})
    assert task.model == "gemini-3-flash-live"


def test_complex_task_uses_38(make_manager):
    manager = make_manager(router=FakeRouter(TaskComplexity.COMPLEX))
    task = wait_for(manager, manager.create_task("T", "D").id, {TaskStatus.COMPLETED})
    assert task.model == "gemini-3.8-flash"


def test_create_is_non_blocking(make_manager):
    manager = make_manager(executor=FakeExecutor(delay=0.25))
    start = time.monotonic()
    manager.create_task("T", "D")
    assert time.monotonic() - start < 0.1


def test_multiple_tasks_are_concurrent(make_manager):
    executor = FakeExecutor(delay=0.1)
    manager = make_manager(executor=executor)
    ids = [manager.create_task(str(i), "D").id for i in range(3)]
    for task_id in ids:
        wait_for(manager, task_id, {TaskStatus.COMPLETED})
    assert executor.max_running >= 2


def test_list_and_get_task(make_manager):
    manager = make_manager()
    created = manager.create_task("T", "D")
    assert manager.get_task(created.id).title == "T"
    assert manager.list_tasks()


def test_completed_task_is_unread(make_manager):
    manager = make_manager()
    task = wait_for(manager, manager.create_task("T", "D").id, {TaskStatus.COMPLETED})
    assert [item.id for item in manager.list_unread_completed_tasks()] == [task.id]


def test_mark_seen_removes_unread(make_manager):
    manager = make_manager()
    task = wait_for(manager, manager.create_task("T", "D").id, {TaskStatus.COMPLETED})
    assert manager.mark_task_as_seen(task.id)
    assert manager.list_unread_completed_tasks() == []


def test_failed_task_is_isolated(make_manager):
    manager = make_manager(executor=FakeExecutor(fail=RuntimeError("réseau coupé")))
    failed = wait_for(manager, manager.create_task("bad", "D").id, {TaskStatus.FAILED})
    healthy_id = manager.create_task("good", "D").id
    # Remplace l'exécuteur défectueux : le thread central est toujours vivant.
    manager.executor = FakeExecutor()
    healthy = wait_for(manager, healthy_id, {TaskStatus.COMPLETED})
    assert "réseau" in failed.error and healthy.status is TaskStatus.COMPLETED


def test_cancel_running_task(make_manager):
    manager = make_manager(executor=FakeExecutor(delay=1))
    created = manager.create_task("T", "D")
    wait_for(manager, created.id, {TaskStatus.RUNNING})
    assert manager.cancel_task(created.id)
    assert wait_for(manager, created.id, {TaskStatus.CANCELLED}).status is TaskStatus.CANCELLED


def test_get_result_marks_seen(make_manager):
    manager = make_manager()
    task = wait_for(manager, manager.create_task("T", "D").id, {TaskStatus.COMPLETED})
    result = manager.get_task_result(task.id)
    assert result["result"] == "Résultat détaillé" and result["seen"]


def test_generated_file_is_referenced(make_manager, tmp_path):
    path = tmp_path / "rapport.md"
    path.write_text("ok")
    manager = make_manager(executor=FakeExecutor(files=[str(path)]))
    task = wait_for(manager, manager.create_task("T", "document").id, {TaskStatus.COMPLETED})
    assert task.files == [str(path)]


def test_completion_hook_fires(make_manager):
    manager = make_manager()
    events = []
    manager.add_hook(lambda task: events.append(task.status))
    task = manager.create_task("T", "D")
    wait_for(manager, task.id, {TaskStatus.COMPLETED})
    assert TaskStatus.COMPLETED in events


def test_state_is_persisted(make_manager, tmp_path):
    manager = make_manager()
    task = wait_for(manager, manager.create_task("T", "D").id, {TaskStatus.COMPLETED})
    assert manager.storage_path.exists() and task.id in manager.storage_path.read_text()


class FakeGateway:
    def __init__(self, text):
        self.text = text
        self.models = []

    async def generate_live(self, model, prompt, **kwargs):
        self.models.append(model)
        return SimpleNamespace(text=self.text)


def test_router_validates_and_maps_simple_model():
    gateway = FakeGateway('{"complexity":"medium","reason":"court","requires_tools":false,"estimated_steps":2,"priority":"normal"}')
    route = asyncio.run(TaskRouter(object(), BackgroundModelConfig(), gateway=gateway).route("T", "D", TaskPriority.NORMAL))
    assert route.complexity is TaskComplexity.MEDIUM and route.model == "gemini-3-flash-live"
    assert gateway.models == ["gemini-3-flash-live"]


def test_router_validates_and_maps_complex_model():
    gateway = FakeGateway('{"complexity":"complex","reason":"long","requires_tools":true,"estimated_steps":6,"priority":"high"}')
    route = asyncio.run(TaskRouter(object(), BackgroundModelConfig(), gateway=gateway).route("T", "D", TaskPriority.NORMAL))
    assert route.model == "gemini-3.8-flash" and route.requires_tools


def test_router_rejects_empty_response():
    with pytest.raises(RoutingError):
        asyncio.run(TaskRouter(object(), BackgroundModelConfig(), gateway=FakeGateway("")).route("T", "D", TaskPriority.NORMAL))


def test_gateway_uses_independent_live_text_session():
    sent = []
    responses = [
        SimpleNamespace(server_content=SimpleNamespace(model_turn=SimpleNamespace(parts=[SimpleNamespace(text="bon")]), turn_complete=False), usage_metadata=None),
        SimpleNamespace(server_content=SimpleNamespace(model_turn=SimpleNamespace(parts=[SimpleNamespace(text="jour")]), turn_complete=True), usage_metadata=SimpleNamespace(total_token_count=9)),
    ]

    class Session:
        async def send_client_content(self, **kwargs): sent.append(kwargs)
        def receive(self):
            async def values():
                for item in responses:
                    yield item
            return values()

    class Context:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *args): return None

    class Live:
        def connect(self, **kwargs):
            sent.append(kwargs)
            return Context()

    client = SimpleNamespace(aio=SimpleNamespace(live=Live()))
    result = asyncio.run(BackgroundModelGateway(client).generate_live(
        "gemini-3-flash-live", "demande", system_instruction="interne"
    ))
    assert result.text == "bonjour" and result.total_tokens == 9
    assert sent[0]["model"] == "gemini-3-flash-live"
    assert sent[0]["config"]["response_modalities"] == ["TEXT"]


def test_complex_daily_quota_is_explicit():
    async def scenario():
        quota = ComplexModelQuota(rpm=5, rpd=1, concurrency=1)
        await quota.acquire()
        quota.release()
        with pytest.raises(QuotaExceededError, match="jour"):
            await quota.acquire()
    asyncio.run(scenario())


def test_quota_counts_tokens():
    async def scenario():
        quota = ComplexModelQuota(rpm=5, rpd=2, concurrency=1)
        await quota.acquire()
        quota.release(456)
        return quota.snapshot()
    snapshot = asyncio.run(scenario())
    assert snapshot.calls_today == 1 and snapshot.tokens_today == 456


def test_quota_persists_across_manager_restart(tmp_path):
    path = tmp_path / "quota.json"
    async def consume():
        quota = ComplexModelQuota(rpm=5, rpd=1, concurrency=1, storage_path=path)
        await quota.acquire(10)
        quota.release(10, 12)
    asyncio.run(consume())
    restored = ComplexModelQuota(rpm=5, rpd=1, concurrency=1, storage_path=path)
    assert restored.snapshot().calls_today == 1
    with pytest.raises(QuotaExceededError):
        asyncio.run(restored.acquire())


def test_retry_failed_task(make_manager):
    manager = make_manager(executor=FakeExecutor(fail=RuntimeError("boom")))
    failed = wait_for(manager, manager.create_task("T", "D").id, {TaskStatus.FAILED})
    manager.executor = FakeExecutor()
    retried = manager.retry_task(failed.id)
    assert wait_for(manager, retried.id, {TaskStatus.COMPLETED}).status is TaskStatus.COMPLETED
