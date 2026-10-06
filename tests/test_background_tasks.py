from __future__ import annotations

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.background_tasks.config import BackgroundModelConfig
from src.background_tasks.executor import ExecutionResult, TaskExecutor
from src.background_tasks.gateway import BackgroundModelGateway
from src.background_tasks.manager import TaskManager
from src.background_tasks.models import BackgroundTask, RoutingDecision, TaskComplexity, TaskPriority, TaskStatus
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
        model = BackgroundModelConfig().model_for_complexity(self.complexity)
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
    monkeypatch.setattr("src.background_tasks.manager.notifications.publish", lambda *a, **k: "test")

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


def test_simple_task_uses_flash_preview(make_manager):
    manager = make_manager(router=FakeRouter(TaskComplexity.SIMPLE))
    task = wait_for(manager, manager.create_task("T", "D").id, {TaskStatus.COMPLETED})
    assert task.model == "gemini-3-flash-preview"


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
        self.calls = []

    async def generate(self, model, prompt, **kwargs):
        self.calls.append({"model": model, "prompt": prompt, **kwargs})
        return SimpleNamespace(text=self.text, total_tokens=7)


def routing_json(complexity="medium", model="gemini-3-flash-preview", tools=False):
    import json
    return json.dumps({
        "complexity": complexity,
        "task_type": "research",
        "reasoning": "raison test",
        "model": model,
        "requires_tools": tools,
        "estimated_steps": 2,
        "priority": "normal",
    })


def test_router_uses_preview_with_classic_generate_content():
    gateway = FakeGateway(routing_json())
    route = asyncio.run(TaskRouter(object(), BackgroundModelConfig(), gateway=gateway).route("T", "D", TaskPriority.NORMAL))
    assert route.complexity is TaskComplexity.MEDIUM
    assert route.model == "gemini-3-flash-preview"
    assert route.task_type == "research"
    assert gateway.calls[0]["model"] == "gemini-3-flash-preview"
    assert gateway.calls[0]["response_json_schema"]


def test_router_maps_complex_without_consuming_complex_model_call():
    gateway = FakeGateway(routing_json("complex", "gemini-3.8-flash", True))
    route = asyncio.run(TaskRouter(object(), BackgroundModelConfig(), gateway=gateway).route("T", "D", TaskPriority.NORMAL))
    assert route.model == "gemini-3.8-flash" and route.requires_tools
    # Le Router lui-même reste toujours sur Flash Preview classique.
    assert [call["model"] for call in gateway.calls] == ["gemini-3-flash-preview"]


def test_router_rejects_model_inconsistent_with_complexity():
    gateway = FakeGateway(routing_json("simple", "gemini-3.8-flash"))
    with pytest.raises(RoutingError, match="incohérent"):
        asyncio.run(TaskRouter(object(), BackgroundModelConfig(), gateway=gateway).route("T", "D", TaskPriority.NORMAL))


def test_router_rejects_empty_response():
    with pytest.raises(RoutingError):
        asyncio.run(TaskRouter(object(), BackgroundModelConfig(), gateway=FakeGateway("")).route("T", "D", TaskPriority.NORMAL))


def test_gateway_uses_only_classic_generate_content():
    calls = []

    async def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text="bonjour", usage_metadata=SimpleNamespace(total_token_count=9))

    class ForbiddenLive:
        def __getattr__(self, name):
            raise AssertionError("Le background ne doit jamais accéder à client.aio.live")

    client = SimpleNamespace(
        aio=SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content),
            live=ForbiddenLive(),
        )
    )
    result = asyncio.run(BackgroundModelGateway(client).generate(
        "gemini-3-flash-preview",
        "demande",
        system_instruction="interne",
        tools=[{"google_search": {}}],
    ))
    assert result.text == "bonjour" and result.total_tokens == 9
    assert calls[0]["model"] == "gemini-3-flash-preview"
    assert calls[0]["config"]["tools"] == [{"google_search": {}}]
    assert calls[0]["config"]["system_instruction"] == "interne"


class RecordingQuota:
    def __init__(self):
        self.acquired = 0
        self.released = 0

    async def acquire(self, estimated_tokens=0):
        self.acquired += 1
        return estimated_tokens

    def release(self, reserved_tokens=0, actual_tokens=None):
        self.released += 1


def execute_with_complexity(tmp_path, complexity, requires_tools=False):
    config = BackgroundModelConfig()
    gateway = FakeGateway("Résultat réel de test")
    quota = RecordingQuota()
    executor = TaskExecutor(object(), config, quota, tmp_path, gateway=gateway)
    route = RoutingDecision(
        complexity,
        config.model_for_complexity(complexity),
        "test",
        requires_tools,
        2,
    )
    updates = []
    result = asyncio.run(executor.execute(
        BackgroundTask("id", "Titre", "Description"),
        route,
        lambda *args, **kwargs: updates.append((args, kwargs)),
    ))
    return result, gateway, quota, updates


@pytest.mark.parametrize("complexity", [TaskComplexity.SIMPLE, TaskComplexity.MEDIUM])
def test_simple_and_medium_use_preview_classic_without_complex_quota(tmp_path, complexity):
    result, gateway, quota, _updates = execute_with_complexity(tmp_path, complexity)
    assert result.text
    assert [call["model"] for call in gateway.calls] == ["gemini-3-flash-preview"]
    assert quota.acquired == quota.released == 0


def test_complex_uses_38_classic_and_consumes_quota_once(tmp_path):
    _result, gateway, quota, _updates = execute_with_complexity(tmp_path, TaskComplexity.COMPLEX)
    assert [call["model"] for call in gateway.calls] == ["gemini-3.8-flash"]
    assert quota.acquired == quota.released == 1


def test_google_search_uses_classic_api_and_waiting_state(tmp_path):
    _result, gateway, quota, updates = execute_with_complexity(
        tmp_path, TaskComplexity.SIMPLE, requires_tools=True
    )
    assert gateway.calls[0]["tools"] == [{"google_search": {}}]
    assert any(kwargs.get("waiting") is True for _args, kwargs in updates)
    assert quota.acquired == 0


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


def test_central_model_configuration_has_expected_roles():
    from src.background_tasks.config import (
        DEFAULT_COMPLEX_MODEL,
        DEFAULT_MAIN_MODEL,
        DEFAULT_MEDIUM_MODEL,
        DEFAULT_ROUTER_MODEL,
        DEFAULT_SIMPLE_MODEL,
    )

    assert DEFAULT_MAIN_MODEL == "gemini-2.5-flash-native-audio-preview-12-2025"
    assert DEFAULT_ROUTER_MODEL == "gemini-3-flash-preview"
    assert DEFAULT_SIMPLE_MODEL == "gemini-3-flash-preview"
    assert DEFAULT_MEDIUM_MODEL == "gemini-3-flash-preview"
    assert DEFAULT_COMPLEX_MODEL == "gemini-3.8-flash"


def test_background_gateway_has_no_live_transport():
    import inspect

    source = inspect.getsource(BackgroundModelGateway)
    assert "aio.live" not in source
    assert "bidiGenerateContent" not in source
    assert not hasattr(BackgroundModelGateway, "generate_live")


def test_classic_api_error_becomes_failed_without_killing_manager(tmp_path, monkeypatch):
    monkeypatch.setattr("src.background_tasks.manager.notifications.publish", lambda *a, **k: "test")

    async def generate_content(**kwargs):
        raise RuntimeError("erreur generateContent contrôlée")

    client = SimpleNamespace(
        aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))
    )
    manager = TaskManager(
        "fake",
        router=FakeRouter(TaskComplexity.SIMPLE),
        config=BackgroundModelConfig(),
        storage_path=tmp_path / "tasks.json",
        output_dir=tmp_path / "out",
        client=client,
    )
    try:
        first = manager.create_task("Erreur", "Déclenche une erreur classique")
        failed = wait_for(manager, first.id, {TaskStatus.FAILED})
        assert "generateContent" in failed.error
        assert manager._thread.is_alive()
        assert manager.quota_status()["calls_today"] == 0

        second = manager.create_task("Encore", "Vérifie que le manager répond encore")
        second_failed = wait_for(manager, second.id, {TaskStatus.FAILED})
        assert second_failed.id != failed.id
        assert manager._thread.is_alive()
    finally:
        manager.close(wait=True)
