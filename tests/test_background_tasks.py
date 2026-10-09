from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.background_tasks.config import BackgroundModelConfig, DEFAULT_COMPLEX_MODEL, DEFAULT_MAIN_MODEL
from src.background_tasks.discovery import (
    LiveModelDiscoveryError,
    LiveModelResolution,
    LiveModelResolver,
    clear_discovery_cache_for_tests,
    is_gemini3_live_model,
)
from src.background_tasks.executor import ExecutionResult, TaskExecutor
from src.background_tasks.gateway import BackgroundModelGateway, BackgroundServiceError
from src.background_tasks.manager import TaskManager
from src.background_tasks.models import BackgroundTask, RoutingDecision, TaskComplexity, TaskPriority, TaskStatus
from src.background_tasks.quota import ComplexModelQuota, QuotaExceededError
from src.background_tasks.router import RoutingError, TaskRouter, parse_json_object

LIVE_MODEL = "discovered-gemini-3-live"


class FakeRouter:
    def __init__(self, complexity=TaskComplexity.SIMPLE, delay=0, fail=None):
        self.complexity, self.delay, self.fail = complexity, delay, fail

    async def route(self, title, description, priority):
        await asyncio.sleep(self.delay)
        if self.fail:
            raise self.fail
        model = DEFAULT_COMPLEX_MODEL if self.complexity is TaskComplexity.COMPLEX else LIVE_MODEL
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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = manager.get_task(task_id)
        if task and task.status in set(statuses):
            return task
        time.sleep(0.005)
    raise AssertionError(f"timeout: {manager.get_task(task_id)}")


@pytest.fixture
def make_manager(tmp_path, monkeypatch):
    managers = []
    monkeypatch.setattr("src.background_tasks.manager.notifications.publish", lambda *a, **k: "test")

    def make(router=None, executor=None, config=None, client_factory=None):
        manager = TaskManager(
            "fake",
            router=router or FakeRouter(),
            executor=executor or FakeExecutor(),
            config=config or BackgroundModelConfig(),
            storage_path=tmp_path / f"tasks-{len(managers)}.json",
            output_dir=tmp_path / "out",
            client_factory=client_factory or (lambda: object()),
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


def test_full_lifecycle_and_result(make_manager):
    manager = make_manager()
    created = manager.create_task("T", "D")
    task = wait_for(manager, created.id, {TaskStatus.COMPLETED})
    assert task.progress == 100 and task.started_at and task.finished_at
    assert manager.list_unread_completed_tasks()[0].id == task.id
    result = manager.get_task_result(task.id)
    assert result["result"] == "Résultat détaillé" and result["seen"]
    assert manager.list_unread_completed_tasks() == []


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


def test_failure_does_not_block_other_task(make_manager):
    class SelectiveExecutor(FakeExecutor):
        async def execute(self, task, route, update):
            if task.title == "bad":
                await asyncio.sleep(0.03)
                raise RuntimeError("échec contrôlé")
            return await super().execute(task, route, update)

    manager = make_manager(executor=SelectiveExecutor(delay=0.05))
    bad = manager.create_task("bad", "D")
    good = manager.create_task("good", "D")
    assert wait_for(manager, bad.id, {TaskStatus.FAILED}).error == "échec contrôlé"
    assert wait_for(manager, good.id, {TaskStatus.COMPLETED}).result
    assert manager._thread.is_alive()


def test_global_task_timeout_releases_slot(make_manager):
    config = BackgroundModelConfig(task_timeout_seconds=0.05)
    manager = make_manager(executor=FakeExecutor(delay=1), config=config)
    timed = manager.create_task("slow", "D")
    failed = wait_for(manager, timed.id, {TaskStatus.FAILED})
    assert "Timeout global" in failed.error
    manager.executor = FakeExecutor()
    assert wait_for(manager, manager.create_task("next", "D").id, {TaskStatus.COMPLETED})


def test_cancel_running_task(make_manager):
    manager = make_manager(executor=FakeExecutor(delay=1))
    created = manager.create_task("T", "D")
    wait_for(manager, created.id, {TaskStatus.RUNNING})
    assert manager.cancel_task(created.id)
    assert wait_for(manager, created.id, {TaskStatus.CANCELLED})


def test_generated_file_is_referenced(make_manager, tmp_path):
    path = tmp_path / "rapport.md"
    path.write_text("ok")
    manager = make_manager(executor=FakeExecutor(files=[str(path)]))
    task = wait_for(manager, manager.create_task("T", "document").id, {TaskStatus.COMPLETED})
    assert manager.get_task_result(task.id)["files"] == [str(path)]


def test_manager_builds_loop_bound_resources_in_worker_thread(make_manager):
    creator = {}

    class LoopAwareClient:
        def __init__(self):
            creator["thread"] = threading.current_thread().name
            creator["loop"] = asyncio.get_running_loop()

    manager = make_manager(client_factory=LoopAwareClient)
    task = wait_for(manager, manager.create_task("T", "D").id, {TaskStatus.COMPLETED})
    assert task.result
    assert creator["thread"] == "jarvis-background-tasks"
    assert creator["loop"] is manager._loop
    assert manager.quota._async_loop is None  # aucune tâche complexe dans ce test


def test_manager_start_create_route_execute_result_and_clean_stop(make_manager):
    manager = make_manager()
    task = wait_for(manager, manager.create_task("cycle", "complet").id, {TaskStatus.COMPLETED})
    assert manager.get_task_result(task.id)["status"] == "COMPLETED"
    manager.close(wait=True)
    assert not manager._thread.is_alive()


class FakeResolver:
    def __init__(self, model=LIVE_MODEL):
        self.value = LiveModelResolution(model)
        self.resolution = self.value

    async def resolve(self, **kwargs):
        return self.value


class FakeGateway:
    def __init__(self, text="résultat"):
        self.text = text
        self.live_calls = []
        self.classic_calls = []

    async def generate_live(self, model, prompt, **kwargs):
        self.live_calls.append({"model": model, "prompt": prompt, **kwargs})
        return SimpleNamespace(text=self.text, total_tokens=7)

    async def generate_classic(self, model, prompt, **kwargs):
        self.classic_calls.append({"model": model, "prompt": prompt, **kwargs})
        callback = kwargs.get("before_attempt")
        if callback:
            callback()
        return SimpleNamespace(text=self.text, total_tokens=11)


def routing_json(complexity="medium", model=LIVE_MODEL, tools=False):
    return json.dumps({
        "complexity": complexity,
        "task_type": "research",
        "reasoning": "raison test",
        "model": model,
        "requires_tools": tools,
        "estimated_steps": 2,
        "priority": "normal",
    })


def test_router_uses_discovered_live_model_and_parses_streamed_json():
    gateway = FakeGateway("préfixe ```json\n" + routing_json() + "\n```")
    router = TaskRouter(object(), BackgroundModelConfig(), FakeResolver(), gateway=gateway)
    route = asyncio.run(router.route("T", "D", TaskPriority.NORMAL))
    assert route.complexity is TaskComplexity.MEDIUM
    assert route.model == LIVE_MODEL
    assert route.task_type == "research"
    assert [call["model"] for call in gateway.live_calls] == [LIVE_MODEL]
    assert gateway.classic_calls == []


def test_router_maps_complex_but_router_call_stays_live():
    gateway = FakeGateway(routing_json("complex", DEFAULT_COMPLEX_MODEL, True))
    router = TaskRouter(object(), BackgroundModelConfig(), FakeResolver(), gateway=gateway)
    route = asyncio.run(router.route("T", "D", TaskPriority.NORMAL))
    assert route.model == DEFAULT_COMPLEX_MODEL and route.requires_tools
    assert gateway.live_calls[0]["model"] == LIVE_MODEL


def test_router_live_timeout_is_explicit_and_bounded():
    class HangingGateway:
        async def generate_live(self, *args, **kwargs):
            await asyncio.Event().wait()

    router = TaskRouter(
        object(), BackgroundModelConfig(timeout_seconds=0.01),
        FakeResolver(), gateway=HangingGateway(),
    )
    with pytest.raises(RoutingError, match="turn_complete"):
        asyncio.run(router.route("T", "D", TaskPriority.NORMAL))


def test_router_rejects_invalid_or_inconsistent_json():
    with pytest.raises(RoutingError):
        parse_json_object("pas de json")
    gateway = FakeGateway(routing_json("simple", DEFAULT_COMPLEX_MODEL))
    router = TaskRouter(object(), BackgroundModelConfig(), FakeResolver(), gateway=gateway)
    with pytest.raises(RoutingError, match="incohérent"):
        asyncio.run(router.route("T", "D", TaskPriority.NORMAL))


def test_live_gateway_uses_audio_transcription_until_turn_complete():
    calls = []
    configs = []
    responses = [
        SimpleNamespace(
            server_content=SimpleNamespace(
                model_turn=SimpleNamespace(parts=[SimpleNamespace(text="IGNORÉ")]),
                output_transcription=SimpleNamespace(text="bon"), turn_complete=False,
            ),
            data=b"audio ignored", usage_metadata=None,
        ),
        SimpleNamespace(
            server_content=SimpleNamespace(
                model_turn=SimpleNamespace(parts=[]),
                output_transcription=SimpleNamespace(text="jour"), turn_complete=True,
            ),
            data=b"audio ignored", usage_metadata=SimpleNamespace(total_token_count=9),
        ),
    ]

    class Session:
        async def send_client_content(self, **kwargs): calls.append(kwargs)
        def receive(self):
            async def values():
                for item in responses:
                    yield item
            return values()

    class Context:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *args): calls.append("closed")

    def connect(**kwargs):
        configs.append(kwargs["config"])
        return Context()

    client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=connect)))
    result = asyncio.run(BackgroundModelGateway(client).generate_live(
        LIVE_MODEL, "demande", system_instruction="interne",
        tools=[{"google_search": {}}],
    ))
    assert result.text == "bonjour" and result.total_tokens == 9
    assert configs == [{
        "response_modalities": ["AUDIO"],
        "output_audio_transcription": {},
        "system_instruction": "interne",
        "temperature": 0.2,
        "tools": [{"google_search": {}}],
    }]
    assert calls[-1] == "closed"


def test_live_gateway_requires_turn_complete_even_with_partial_text():
    message = SimpleNamespace(
        server_content=SimpleNamespace(
            model_turn=SimpleNamespace(parts=[]),
            output_transcription=SimpleNamespace(text="partiel"),
            turn_complete=False,
        ),
        usage_metadata=None,
    )

    class Session:
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values(): yield message
            return values()

    class Context:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *args): return None

    client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=lambda **kwargs: Context())))
    with pytest.raises(RuntimeError, match="sans turn_complete"):
        asyncio.run(BackgroundModelGateway(client).generate_live(LIVE_MODEL, "D", system_instruction="I"))


def test_live_gateway_cancellation_closes_session():
    closed = []

    class Session:
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values():
                await asyncio.Event().wait()
                yield  # pragma: no cover
            return values()

    class Context:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *args): closed.append(True)

    client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=lambda **kwargs: Context())))

    async def scenario():
        await asyncio.wait_for(
            BackgroundModelGateway(client).generate_live(
                LIVE_MODEL, "D", system_instruction="I"
            ),
            timeout=0.01,
        )

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(scenario())
    assert closed == [True]


def test_live_gateway_rejects_turn_without_output_transcription_and_closes():
    closed = []
    message = SimpleNamespace(
        server_content=SimpleNamespace(
            model_turn=SimpleNamespace(parts=[SimpleNamespace(text="ne pas utiliser")]),
            output_transcription=None,
            turn_complete=True,
        ),
        data=b"audio", usage_metadata=None,
    )

    class Session:
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values(): yield message
            return values()

    class Context:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *args): closed.append(True)

    client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=lambda **kwargs: Context())))
    with pytest.raises(RuntimeError, match="sans transcription audio"):
        asyncio.run(BackgroundModelGateway(client).generate_live(LIVE_MODEL, "D", system_instruction="I"))
    assert closed == [True]


@pytest.mark.parametrize("code", [429, 503])
def test_gateway_retries_external_errors_with_bounded_attempts(monkeypatch, code):
    attempts = 0
    sleeps = []

    class Context:
        async def __aenter__(self):
            nonlocal attempts
            attempts += 1
            raise RuntimeError(f"{code} RESOURCE_EXHAUSTED" if code == 429 else "503 UNAVAILABLE high demand")
        async def __aexit__(self, *args): return None

    async def fake_sleep(delay): sleeps.append(delay)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=lambda **kwargs: Context())))
    gateway = BackgroundModelGateway(client, retry_attempts=3, retry_base_delay=0.1)
    with pytest.raises(BackgroundServiceError) as captured:
        asyncio.run(gateway.generate_live(LIVE_MODEL, "D", system_instruction="I"))
    assert captured.value.status_code == code
    assert attempts == 3 and sleeps == [0.1, 0.2]


def test_classic_gateway_disables_afc_for_server_tool():
    calls = []
    async def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text="ok", usage_metadata=SimpleNamespace(total_token_count=3))
    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    result = asyncio.run(BackgroundModelGateway(client).generate_classic(
        DEFAULT_COMPLEX_MODEL, "D", tools=[{"google_search": {}}]
    ))
    assert result.text == "ok"
    assert calls[0]["config"]["automatic_function_calling"] == {"disable": True}


class RecordingQuota:
    def __init__(self):
        self.reserved = self.attempted = self.finished = 0
    async def reserve(self, estimate):
        self.reserved += 1
        return SimpleNamespace(estimated_tokens=estimate, attempts=0, released=False)
    def mark_attempt(self, lease):
        self.attempted += 1
        lease.attempts += 1
    def finish(self, lease, tokens):
        self.finished += 1


def execute_with_complexity(tmp_path, complexity, requires_tools=False):
    config = BackgroundModelConfig()
    gateway = FakeGateway("Résultat\n\nDétail")
    quota = RecordingQuota()
    executor = TaskExecutor(object(), config, quota, tmp_path, FakeResolver(), gateway=gateway)
    route = RoutingDecision(
        complexity,
        config.model_for_complexity(complexity, live_model=LIVE_MODEL),
        "test", requires_tools, 2,
    )
    updates = []
    result = asyncio.run(executor.execute(
        BackgroundTask("id", "Titre", "Crée un document"), route,
        lambda *args, **kwargs: updates.append((args, kwargs)),
    ))
    return result, gateway, quota, updates


@pytest.mark.parametrize("complexity", [TaskComplexity.SIMPLE, TaskComplexity.MEDIUM])
def test_simple_medium_use_live_without_complex_quota(tmp_path, complexity):
    result, gateway, quota, _ = execute_with_complexity(tmp_path, complexity)
    assert result.files and Path(result.files[0]).exists()
    assert gateway.live_calls[0]["model"] == LIVE_MODEL
    assert not gateway.classic_calls
    assert quota.reserved == quota.attempted == quota.finished == 0


def test_complex_uses_classic_and_counts_attempt(tmp_path):
    result, gateway, quota, _ = execute_with_complexity(tmp_path, TaskComplexity.COMPLEX)
    assert result.files
    assert gateway.classic_calls[0]["model"] == DEFAULT_COMPLEX_MODEL
    assert quota.reserved == quota.attempted == quota.finished == 1


def test_google_search_on_simple_uses_live_and_waiting_state(tmp_path):
    _, gateway, quota, updates = execute_with_complexity(tmp_path, TaskComplexity.SIMPLE, True)
    assert gateway.live_calls[0]["tools"] == [{"google_search": {}}]
    assert any(kwargs.get("waiting") for _, kwargs in updates)
    assert quota.reserved == 0


def test_quota_reservation_without_attempt_does_not_count(tmp_path):
    async def scenario():
        quota = ComplexModelQuota(storage_path=tmp_path / "quota.json")
        lease = await quota.reserve(100)
        assert quota.snapshot().calls_today == 0
        quota.finish(lease, 0)
        return quota.snapshot()
    assert asyncio.run(scenario()).calls_today == 0


def test_quota_counts_each_real_retry_and_persists(tmp_path):
    path = tmp_path / "quota.json"
    async def scenario():
        quota = ComplexModelQuota(storage_path=path)
        lease = await quota.reserve(10)
        quota.mark_attempt(lease)
        quota.mark_attempt(lease)
        quota.finish(lease, 12)
        return quota.snapshot()
    snapshot = asyncio.run(scenario())
    assert snapshot.calls_today == 2 and snapshot.tokens_today == 12
    assert ComplexModelQuota(storage_path=path).snapshot().calls_today == 2


def test_quota_limits_and_loop_ownership():
    quota = ComplexModelQuota(rpd=1)
    async def first():
        lease = await quota.reserve()
        quota.mark_attempt(lease)
        quota.finish(lease)
    asyncio.run(first())
    with pytest.raises((QuotaExceededError, RuntimeError)):
        asyncio.run(quota.reserve())


class AsyncPager:
    def __init__(self, values): self.values = values
    def __aiter__(self):
        async def iterate():
            for value in self.values:
                yield value
        return iterate()


class DiscoverySession:
    def __init__(self, name, calls):
        self.name, self.calls = name, calls

    async def send_client_content(self, **kwargs):
        self.calls.append(("send", self.name, kwargs))

    def receive(self):
        async def values():
            yield SimpleNamespace(
                server_content=SimpleNamespace(
                    output_transcription=SimpleNamespace(text="OK"),
                    turn_complete=True,
                ),
                usage_metadata=None,
            )
        return values()


class LiveContext:
    def __init__(self, name, config, accepted, calls):
        self.name, self.config, self.accepted, self.calls = name, config, accepted, calls

    async def __aenter__(self):
        self.calls.append(("connect", self.name, self.config))
        if self.name not in self.accepted:
            raise RuntimeError("unsupported")
        return DiscoverySession(self.name, self.calls)

    async def __aexit__(self, *args):
        self.calls.append(("close", self.name))


def discovery_client(models, accepted):
    calls = []
    async def list_models(): return AsyncPager(models)
    live = SimpleNamespace(
        connect=lambda model, config: LiveContext(model, config, accepted, calls)
    )
    return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(list=list_models), live=live)), calls


def model(name, actions):
    return SimpleNamespace(name=f"models/{name}", supported_actions=actions)


def test_discovery_filters_bidi_prefers_current_stable_and_validates_connection():
    clear_discovery_cache_for_tests()
    models = [
        model("gemini-3.1-flash-live-preview", ["bidiGenerateContent"]),
        model("gemini-3.8-live", ["bidiGenerateContent"]),
        model("gemini-3.8-flash", ["generateContent"]),
        model("gemini-2.5-live", ["bidiGenerateContent"]),
    ]
    client, calls = discovery_client(models, {"gemini-3.8-live"})
    resolver = LiveModelResolver(client, api_key_fingerprint="key-test")
    resolution = asyncio.run(resolver.resolve())
    assert resolution.model == "gemini-3.8-live"
    assert resolution.transport == "Live/BidiGenerateContent"
    assert [item[0] for item in calls] == ["connect", "send", "close"]
    assert calls[0][2]["response_modalities"] == ["AUDIO"]
    assert calls[0][2]["output_audio_transcription"] == {}


def test_discovery_does_not_validate_handshake_without_transcription():
    clear_discovery_cache_for_tests()

    class EmptySession:
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values():
                yield SimpleNamespace(
                    server_content=SimpleNamespace(
                        output_transcription=None, turn_complete=True
                    ),
                    usage_metadata=None,
                )
            return values()

    class EmptyContext:
        async def __aenter__(self): return EmptySession()
        async def __aexit__(self, *args): return None

    async def list_models():
        return AsyncPager([model("gemini-3.8-live", ["bidiGenerateContent"])])

    client = SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(list=list_models),
        live=SimpleNamespace(connect=lambda **kwargs: EmptyContext()),
    ))
    resolver = LiveModelResolver(client, api_key_fingerprint="empty-exchange")
    with pytest.raises(LiveModelDiscoveryError, match="sans transcription"):
        asyncio.run(resolver.resolve())
    assert resolver.resolution is None


def test_discovery_tries_next_candidate_and_never_invents_name():
    clear_discovery_cache_for_tests()
    models = [
        model("gemini-3.9-live", ["bidiGenerateContent"]),
        model("gemini-3.8-live", ["bidiGenerateContent"]),
    ]
    client, calls = discovery_client(models, {"gemini-3.8-live"})
    result = asyncio.run(LiveModelResolver(client, api_key_fingerprint="key-next").resolve())
    assert result.model == "gemini-3.8-live"
    assert [item[:2] for item in calls if item[0] == "connect"] == [
        ("connect", "gemini-3.9-live"),
        ("connect", "gemini-3.8-live"),
    ]
    assert ("send", "gemini-3.8-live") == calls[-2][:2]
    assert calls[-1] == ("close", "gemini-3.8-live")

    empty, _ = discovery_client([model("gemini-3.8-flash", ["generateContent"])], set())
    with pytest.raises(LiveModelDiscoveryError, match="Aucun modèle"):
        asyncio.run(LiveModelResolver(empty, api_key_fingerprint="key-empty").resolve())


def test_discovery_retries_503_with_bounded_backoff(monkeypatch):
    clear_discovery_cache_for_tests()
    attempts = 0
    sleeps = []

    async def list_models():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise RuntimeError("503 UNAVAILABLE high demand")
        return AsyncPager([model("gemini-3.8-live", ["bidiGenerateContent"])])

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    calls = []
    live = SimpleNamespace(
        connect=lambda model, config: LiveContext(
            model, config, {"gemini-3.8-live"}, calls
        )
    )
    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(list=list_models), live=live))
    resolver = LiveModelResolver(
        client, api_key_fingerprint="retry-discovery",
        retry_attempts=3, retry_base_delay=0.1,
    )
    assert asyncio.run(resolver.resolve()).model == "gemini-3.8-live"
    assert attempts == 3 and sleeps == [0.1, 0.2]


def test_model_filter_requires_gemini3_live_and_bidi():
    assert is_gemini3_live_model(model("gemini-3.8-live", ["bidiGenerateContent"]))
    assert not is_gemini3_live_model(model("gemini-3.8-flash", ["generateContent"]))
    assert not is_gemini3_live_model(model("gemini-2.5-live", ["bidiGenerateContent"]))


def test_main_model_unchanged_and_no_assumed_background_identifier():
    assert DEFAULT_MAIN_MODEL == "gemini-2.5-flash-native-audio-preview-12-2025"
    config = BackgroundModelConfig()
    assert config.live_model_hint == ""
    with pytest.raises(RuntimeError, match="découvert"):
        config.model_for_complexity(TaskComplexity.SIMPLE)
