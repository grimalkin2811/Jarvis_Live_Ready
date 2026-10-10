from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai import types as genai_types

from src.background_tasks.config import BackgroundModelConfig, DEFAULT_COMPLEX_MODEL, DEFAULT_MAIN_MODEL
from src.background_tasks.discovery import (
    LiveModelDiscoveryError,
    LiveModelResolution,
    LiveModelResolver,
    clear_discovery_cache_for_tests,
    is_gemini3_live_model,
    live_candidate_rejection,
)
from src.background_tasks.executor import ExecutionResult, TaskExecutor
from src.background_tasks.gateway import (
    BackgroundModelGateway,
    BackgroundServiceError,
    classify_background_error,
)
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
        manager_root = tmp_path / f"manager-{len(managers)}"
        manager = TaskManager(
            "fake",
            router=router or FakeRouter(),
            executor=executor or FakeExecutor(),
            config=config or BackgroundModelConfig(),
            storage_path=manager_root / "tasks.json",
            output_dir=manager_root / "out",
            client_factory=client_factory or (lambda: object()),
        )
        managers.append(manager)
        return manager

    yield make
    for manager in managers:
        manager.close(wait=True)


def test_manager_rejects_illegal_status_transition(make_manager):
    manager = make_manager(router=FakeRouter(delay=1))
    task = manager.create_task("transition", "D")
    # La future peut avoir commencé entre create_task et l'assertion; une tâche
    # locale dédiée garantit ici l'état QUEUED déterministe.
    queued = BackgroundTask("manual", "T", "D")
    with manager._lock:
        manager._tasks[queued.id] = queued
    with pytest.raises(RuntimeError, match="QUEUED → COMPLETED"):
        manager._mutate(queued.id, status=TaskStatus.COMPLETED)
    manager.cancel_task(task.id)


def test_create_task_is_immediately_queued(make_manager):
    manager = make_manager(router=FakeRouter(delay=0.1))
    task = manager.create_task("Titre", "Description")
    assert task.id and task.status is TaskStatus.QUEUED


def test_create_task_returns_queued_snapshot_if_worker_starts_immediately(
    make_manager, monkeypatch
):
    manager = make_manager()

    def start_immediately(coroutine, loop):
        del loop
        with manager._lock:
            task_id = next(reversed(manager._tasks))
            manager._tasks[task_id].status = TaskStatus.RUNNING
        coroutine.close()
        return SimpleNamespace(cancel=lambda: None)

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", start_immediately)
    submitted = manager.create_task("Titre", "Description")
    assert submitted.status is TaskStatus.QUEUED
    assert manager.get_task(submitted.id).status is TaskStatus.RUNNING


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


def test_cancelled_task_rejects_late_result_and_notification(make_manager, monkeypatch):
    published = []
    monkeypatch.setattr(
        "src.background_tasks.manager.notifications.publish",
        lambda *args, **kwargs: published.append(args),
    )

    class CancellationResistantExecutor(FakeExecutor):
        async def execute(self, task, route, update):
            update(40, "Analyse", 2, 3)
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                await asyncio.sleep(0)
            update(90, "Résultat tardif", 3, 3)
            return ExecutionResult("résultat tardif", "tardif")

    manager = make_manager(executor=CancellationResistantExecutor())
    task = manager.create_task("annulation", "D")
    wait_for(manager, task.id, {TaskStatus.RUNNING})
    assert manager.cancel_task(task.id)
    time.sleep(0.05)
    cancelled = manager.get_task(task.id)
    assert cancelled.status is TaskStatus.CANCELLED
    assert cancelled.result is None
    assert published == []


def test_manager_never_completes_partial_live_response(make_manager):
    message = genai_types.LiveServerMessage(
        server_content=genai_types.LiveServerContent(
            output_transcription=genai_types.Transcription(text="partiel")
        )
    )

    class Session:
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values(): yield message
            return values()

    class Context:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *args): return None

    gateway = BackgroundModelGateway(SimpleNamespace(aio=SimpleNamespace(
        live=SimpleNamespace(connect=lambda **kwargs: Context())
    )))

    class PartialExecutor:
        async def execute(self, task, route, update):
            await gateway.generate_live(LIVE_MODEL, "D", system_instruction="I")
            raise AssertionError("inaccessible")

    manager = make_manager(executor=PartialExecutor())
    created = manager.create_task("partielle", "D")
    failed = wait_for(manager, created.id, {TaskStatus.FAILED})
    assert "sans signal de fin" in failed.error
    assert failed.result is None


def test_manager_rejects_empty_result_and_missing_artifact(make_manager, tmp_path):
    class InvalidExecutor(FakeExecutor):
        def __init__(self, result): self.result = result
        async def execute(self, task, route, update): return self.result

    empty_manager = make_manager(executor=InvalidExecutor(ExecutionResult("", "")))
    empty = empty_manager.create_task("vide", "D")
    failed_empty = wait_for(empty_manager, empty.id, {TaskStatus.FAILED})
    assert "résultat vide" in failed_empty.error

    missing = tmp_path / "absent.md"
    file_manager = make_manager(executor=InvalidExecutor(
        ExecutionResult("texte", "résumé", [str(missing)])
    ))
    artifact = file_manager.create_task("fichier", "Crée un rapport")
    failed_file = wait_for(file_manager, artifact.id, {TaskStatus.FAILED})
    assert "Livrable absent ou vide" in failed_file.error


def test_generated_file_is_referenced(make_manager, tmp_path):
    path = tmp_path / "rapport.md"
    path.write_text("ok")
    manager = make_manager(executor=FakeExecutor(files=[str(path)]))
    task = wait_for(manager, manager.create_task("T", "document").id, {TaskStatus.COMPLETED})
    assert manager.get_task_result(task.id)["files"] == [str(path)]


def test_restart_persists_interrupted_active_task_as_failed(tmp_path):
    storage = tmp_path / "tasks.json"
    active = BackgroundTask("active", "Titre", "D", status=TaskStatus.RUNNING)
    storage.write_text(json.dumps({"version": 1, "tasks": [active.to_dict()]}))
    manager = TaskManager(
        "fake", storage_path=storage, output_dir=tmp_path / "out",
        router=FakeRouter(), executor=FakeExecutor(),
        client_factory=lambda: object(), auto_start=False,
    )
    restored = manager.get_task("active")
    assert restored.status is TaskStatus.FAILED
    persisted = json.loads(storage.read_text(encoding="utf-8"))["tasks"][0]
    assert persisted["status"] == "FAILED"
    assert persisted["finished_at"]


def test_unread_result_persists_then_becomes_seen_after_restart(tmp_path, monkeypatch):
    monkeypatch.setattr("src.background_tasks.manager.notifications.publish", lambda *a, **k: None)
    storage = tmp_path / "persistent" / "tasks.json"
    kwargs = {
        "router": FakeRouter(),
        "executor": FakeExecutor(),
        "storage_path": storage,
        "output_dir": tmp_path / "out",
        "client_factory": lambda: object(),
    }
    first = TaskManager("fake", **kwargs)
    created = first.create_task("persistée", "D")
    completed = wait_for(first, created.id, {TaskStatus.COMPLETED})
    assert not completed.seen
    first.close(wait=True)

    second = TaskManager("fake", **kwargs)
    try:
        unread = second.list_unread_completed_tasks()
        assert [item.id for item in unread] == [created.id]
        result = second.get_task_result(created.id, mark_seen=True)
        assert result["seen"] is True
        assert second.list_unread_completed_tasks() == []
    finally:
        second.close(wait=True)

    persisted = json.loads(storage.read_text(encoding="utf-8"))
    restored = next(item for item in persisted["tasks"] if item["id"] == created.id)
    assert restored["seen"] is True


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
        return SimpleNamespace(
            text=self.text, total_tokens=7, completion_signal="generation_complete"
        )

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
    with pytest.raises(RoutingError, match="signal de fin"):
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


def test_live_gateway_requires_completion_signal_even_with_partial_text():
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
    with pytest.raises(RuntimeError, match="sans signal de fin"):
        asyncio.run(BackgroundModelGateway(client).generate_live(LIVE_MODEL, "D", system_instruction="I"))


def test_live_gateway_accepts_sdk_generation_complete_after_all_transcripts():
    """Utilise les classes du SDK installé, pas un schéma inventé."""
    closed = []
    messages = [
        genai_types.LiveServerMessage(
            usage_metadata=genai_types.UsageMetadata(total_token_count=1)
        ),
        genai_types.LiveServerMessage(server_content=genai_types.LiveServerContent(
            model_turn=genai_types.Content(role="model", parts=[
                genai_types.Part(inline_data=genai_types.Blob(
                    data=b"audio", mime_type="audio/pcm;rate=24000"
                ))
            ]),
            output_transcription=genai_types.Transcription(text="résultat "),
            grounding_metadata=genai_types.GroundingMetadata(grounding_chunks=[
                genai_types.GroundingChunk(web=genai_types.GroundingChunkWeb(
                    title="Source", uri="https://example.test/source"
                ))
            ]),
        )),
        genai_types.LiveServerMessage(server_content=genai_types.LiveServerContent(
            output_transcription=genai_types.Transcription(text="complet"),
            generation_complete=True,
        )),
    ]

    class Session:
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values():
                for message in messages:
                    yield message
            return values()

    class Context:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *args): closed.append(True)

    client = SimpleNamespace(aio=SimpleNamespace(
        live=SimpleNamespace(connect=lambda **kwargs: Context())
    ))
    result = asyncio.run(BackgroundModelGateway(client).generate_live(
        LIVE_MODEL, "D", system_instruction="I"
    ))
    assert result.text == "résultat complet"
    assert result.completion_signal == "generation_complete"
    assert result.sources == ["https://example.test/source"]
    assert closed == [True]


def test_live_gateway_accepts_sdk_interaction_idle_as_terminal():
    message = genai_types.LiveServerMessage(
        server_content=genai_types.LiveServerContent(
            output_transcription=genai_types.Transcription(text="terminé"),
            interaction_status=genai_types.InteractionStatus.IDLE,
        )
    )

    class Session:
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values(): yield message
            return values()

    class Context:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *args): return None

    gateway = BackgroundModelGateway(SimpleNamespace(aio=SimpleNamespace(
        live=SimpleNamespace(connect=lambda **kwargs: Context())
    )))
    result = asyncio.run(gateway.generate_live(
        LIVE_MODEL, "D", system_instruction="I"
    ))
    assert result.text == "terminé"
    assert result.completion_signal == "interaction_status=IDLE"


def test_live_gateway_rejects_interrupted_partial_sdk_response():
    message = genai_types.LiveServerMessage(
        server_content=genai_types.LiveServerContent(
            output_transcription=genai_types.Transcription(text="partiel"),
            interrupted=True,
            turn_complete=True,
        )
    )

    class Session:
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values(): yield message
            return values()

    class Context:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *args): return None

    gateway = BackgroundModelGateway(SimpleNamespace(aio=SimpleNamespace(
        live=SimpleNamespace(connect=lambda **kwargs: Context())
    )))
    with pytest.raises(RuntimeError, match="interrompue") as captured:
        asyncio.run(gateway.generate_live(LIVE_MODEL, "D", system_instruction="I"))
    assert classify_background_error(captured.value).category == "LIVE_PROTOCOL"


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


def test_live_gateway_internal_timeout_reports_phase_and_cleanup():
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

    client = SimpleNamespace(aio=SimpleNamespace(
        live=SimpleNamespace(connect=lambda **kwargs: Context())
    ))
    with pytest.raises(BackgroundServiceError) as captured:
        asyncio.run(BackgroundModelGateway(client).generate_live(
            LIVE_MODEL, "D", system_instruction="I", timeout_seconds=0.01
        ))
    detail = str(captured.value)
    assert "phase=réception" in detail
    assert "messages=0" in detail
    assert "turn_complete=False" in detail
    assert "session_fermée=True" in detail
    assert closed == [True]


def test_two_live_cycles_progress_independently_when_one_times_out():
    closed = []
    connection_number = 0

    class Session:
        def __init__(self, hangs): self.hangs = hangs
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values():
                if self.hangs:
                    await asyncio.Event().wait()
                    return
                yield SimpleNamespace(
                    server_content=SimpleNamespace(
                        output_transcription=SimpleNamespace(text="terminé"),
                        turn_complete=True,
                    ),
                    usage_metadata=None,
                )
            return values()

    class Context:
        def __init__(self, number): self.number = number
        async def __aenter__(self): return Session(self.number == 1)
        async def __aexit__(self, *args): closed.append(self.number)

    def connect(**kwargs):
        nonlocal connection_number
        connection_number += 1
        return Context(connection_number)

    client = SimpleNamespace(aio=SimpleNamespace(
        live=SimpleNamespace(connect=connect)
    ))
    gateway = BackgroundModelGateway(client)

    async def scenario():
        return await asyncio.gather(
            gateway.generate_live(
                LIVE_MODEL, "bloquée", system_instruction="I", timeout_seconds=0.03
            ),
            gateway.generate_live(
                LIVE_MODEL, "rapide", system_instruction="I", timeout_seconds=0.03
            ),
            return_exceptions=True,
        )

    blocked, completed = asyncio.run(scenario())
    assert isinstance(blocked, BackgroundServiceError)
    assert completed.text == "terminé"
    assert sorted(closed) == [1, 2]


def test_two_live_cycles_are_independent_when_one_has_resource_1011(monkeypatch):
    connection_number = 0
    closed = []

    class Session:
        def __init__(self, number): self.number = number
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values():
                if self.number in {1, 3}:
                    raise LiveClose1011(
                        "Resource has been exhausted (e.g. check quota)."
                    )
                yield SimpleNamespace(
                    server_content=SimpleNamespace(
                        output_transcription=SimpleNamespace(text="indépendante"),
                        turn_complete=True,
                    ),
                    usage_metadata=None,
                )
            return values()

    class Context:
        def __init__(self, number): self.number = number
        async def __aenter__(self): return Session(self.number)
        async def __aexit__(self, *args): closed.append(self.number)

    def connect(**kwargs):
        nonlocal connection_number
        connection_number += 1
        return Context(connection_number)

    original_sleep = asyncio.sleep

    async def no_wait(delay):
        await original_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", no_wait)
    monkeypatch.setattr("src.background_tasks.gateway.random.uniform", lambda low, high: 0.0)
    gateway = BackgroundModelGateway(SimpleNamespace(
        aio=SimpleNamespace(live=SimpleNamespace(connect=connect))
    ), retry_attempts=3, retry_base_delay=0.0)

    async def scenario():
        return await asyncio.gather(
            gateway.generate_live(LIVE_MODEL, "A", system_instruction="I"),
            gateway.generate_live(LIVE_MODEL, "B", system_instruction="I"),
            return_exceptions=True,
        )

    exhausted, completed = asyncio.run(scenario())
    assert isinstance(exhausted, BackgroundServiceError)
    assert exhausted.category == "LIVE_RESOURCE_EXHAUSTED"
    assert completed.text == "indépendante"
    assert sorted(closed) == [1, 2, 3]


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


class LiveClose1011(RuntimeError):
    code = 1011

    def __init__(self, reason):
        super().__init__(f"received 1011 (internal error) {reason}")
        self.reason = reason


def test_live_1011_explicit_quota_is_external_quota():
    reason = "You exceeded your current quota, please check your plan and billing details."
    info = classify_background_error(LiveClose1011(reason))
    assert info.category == "EXTERNAL_QUOTA"
    assert info.live_close_code == 1011
    assert info.status_code is None
    assert info.retryable and info.external


def test_explicit_quota_message_overrides_generic_live_resource_category():
    error = BackgroundServiceError(
        "You exceeded your current quota, please check your plan and billing details.",
        live_close_code=1011,
        category="LIVE_RESOURCE_EXHAUSTED",
        external_unavailable=True,
    )
    info = classify_background_error(error)
    assert info.category == "EXTERNAL_QUOTA"
    assert info.live_close_code == 1011
    assert info.status_code is None


def test_live_1011_resource_exhausted_is_classified_structurally():
    error = LiveClose1011("Resource has been exhausted (e.g. check quota).")
    info = classify_background_error(error)
    assert info.category == "LIVE_RESOURCE_EXHAUSTED"
    assert info.live_close_code == 1011
    assert info.status_code is None
    assert info.retryable and info.external


def test_live_1011_without_resource_message_is_not_misclassified():
    info = classify_background_error(LiveClose1011("unexpected internal condition"))
    assert info.category == "CODE_OR_PROTOCOL"
    assert info.live_close_code == 1011
    assert not info.retryable


def test_live_explicit_quota_keeps_reason_code_and_live_retry_limit(monkeypatch):
    attempts = 0
    closed = []
    reason = "You exceeded your current quota, please check your plan and billing details."

    class Session:
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values():
                nonlocal attempts
                attempts += 1
                raise LiveClose1011(reason)
                yield  # pragma: no cover
            return values()

    class Context:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *args): closed.append(True)

    async def no_wait(delay): return None
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    monkeypatch.setattr("src.background_tasks.gateway.random.uniform", lambda low, high: 0.0)
    gateway = BackgroundModelGateway(SimpleNamespace(
        aio=SimpleNamespace(live=SimpleNamespace(connect=lambda **kwargs: Context()))
    ), retry_attempts=5, retry_base_delay=0.0)

    with pytest.raises(BackgroundServiceError) as captured:
        asyncio.run(gateway.generate_live(LIVE_MODEL, "D", system_instruction="I"))
    assert attempts == 2
    assert closed == [True, True]
    assert captured.value.category == "EXTERNAL_QUOTA"
    assert captured.value.live_close_code == 1011
    assert captured.value.status_code is None
    assert reason in str(captured.value)


def test_live_resource_retry_is_limited_jittered_and_closes(monkeypatch):
    attempts = 0
    closed = []
    sleeps = []

    class Session:
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values():
                nonlocal attempts
                attempts += 1
                raise LiveClose1011("Resource has been exhausted (e.g. check quota).")
                yield  # pragma: no cover
            return values()

    class Context:
        async def __aenter__(self): return Session()
        async def __aexit__(self, *args): closed.append(True)

    async def fake_sleep(delay): sleeps.append(delay)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr("src.background_tasks.gateway.random.uniform", lambda low, high: 0.02)
    client = SimpleNamespace(aio=SimpleNamespace(
        live=SimpleNamespace(connect=lambda **kwargs: Context())
    ))
    gateway = BackgroundModelGateway(client, retry_attempts=5, retry_base_delay=0.1)
    with pytest.raises(BackgroundServiceError) as captured:
        asyncio.run(gateway.generate_live(LIVE_MODEL, "D", system_instruction="I"))
    assert attempts == 2  # politique Live plus stricte que les retries HTTP
    assert sleeps == [pytest.approx(0.12)]
    assert closed == [True, True]
    assert captured.value.category == "LIVE_RESOURCE_EXHAUSTED"
    assert captured.value.live_close_code == 1011
    detail = str(captured.value)
    assert "diagnostic Live" in detail
    assert "dernière_erreur=LiveClose1011" in detail


def test_live_retry_timeout_preserves_initial_external_1011(monkeypatch):
    connection_number = 0
    closed = []

    class Session:
        def __init__(self, number): self.number = number
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values():
                if self.number == 1:
                    raise LiveClose1011(
                        "Resource has been exhausted (e.g. check quota)."
                    )
                await asyncio.Event().wait()
                yield  # pragma: no cover
            return values()

    class Context:
        def __init__(self, number): self.number = number
        async def __aenter__(self): return Session(self.number)
        async def __aexit__(self, *args): closed.append(self.number)

    def connect(**kwargs):
        nonlocal connection_number
        connection_number += 1
        return Context(connection_number)

    monkeypatch.setattr("src.background_tasks.gateway.random.uniform", lambda low, high: 0.0)
    gateway = BackgroundModelGateway(SimpleNamespace(
        aio=SimpleNamespace(live=SimpleNamespace(connect=connect))
    ), retry_attempts=3, retry_base_delay=0.0)
    with pytest.raises(BackgroundServiceError) as captured:
        asyncio.run(gateway.generate_live(
            LIVE_MODEL, "D", system_instruction="I", timeout_seconds=0.03
        ))
    assert captured.value.category == "LIVE_RESOURCE_EXHAUSTED"
    assert captured.value.live_close_code == 1011
    assert captured.value.external_unavailable
    assert "Resource has been exhausted" in str(captured.value)
    assert "historique_erreurs=" in str(captured.value)
    assert sorted(closed) == [1, 2]


def test_classic_gateway_disables_afc_for_server_tool():
    calls = []
    async def generate_content(**kwargs):
        calls.append(kwargs)
        metadata = genai_types.GroundingMetadata(grounding_chunks=[
            genai_types.GroundingChunk(web=genai_types.GroundingChunkWeb(
                title="Source", uri="https://example.test/classic"
            ))
        ])
        return SimpleNamespace(
            text="ok", usage_metadata=SimpleNamespace(total_token_count=3),
            candidates=[SimpleNamespace(grounding_metadata=metadata)],
        )
    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    result = asyncio.run(BackgroundModelGateway(client).generate_classic(
        DEFAULT_COMPLEX_MODEL, "D", tools=[{"google_search": {}}]
    ))
    assert result.text == "ok"
    assert result.sources == ["https://example.test/classic"]
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
    assert result.transport == "Live/BidiGenerateContent"
    assert result.completion_signal == "generation_complete"
    assert quota.reserved == quota.attempted == quota.finished == 0


def test_complex_uses_classic_and_counts_attempt(tmp_path):
    result, gateway, quota, _ = execute_with_complexity(tmp_path, TaskComplexity.COMPLEX)
    assert result.files
    assert gateway.classic_calls[0]["model"] == DEFAULT_COMPLEX_MODEL
    assert result.transport == "GenerateContent"
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
    assert resolution.completion_signal == "turn_complete"
    assert [item[0] for item in calls] == ["connect", "send", "close"]
    assert calls[0][2]["response_modalities"] == ["AUDIO"]
    assert calls[0][2]["output_audio_transcription"] == {}


def test_discovery_filters_specialized_live_variants_before_connection():
    clear_discovery_cache_for_tests()
    models = [
        model("gemini-3.5-transcribe-live", ["bidiGenerateContent"]),
        model("gemini-3.5-live-translate-preview", ["bidiGenerateContent"]),
        model("gemini-3.8-live-extended-thinking", ["bidiGenerateContent"]),
        model("gemini-3.1-flash-live-preview", ["bidiGenerateContent"]),
    ]
    client, calls = discovery_client(models, {"gemini-3.1-flash-live-preview"})
    result = asyncio.run(LiveModelResolver(
        client, api_key_fingerprint="specialized-filter"
    ).resolve())
    assert result.model == "gemini-3.1-flash-live-preview"
    connected = [item[1] for item in calls if item[0] == "connect"]
    assert connected == ["gemini-3.1-flash-live-preview"]
    assert "transcription/traduction" in live_candidate_rejection(models[0])
    assert "transcription/traduction" in live_candidate_rejection(models[1])
    assert "réflexion" in live_candidate_rejection(models[2])


def test_validation_failure_categories_are_context_specific():
    unsupported = LiveModelResolver._validation_failure(
        "transcribe", RuntimeError(
            "The requested combination of response modalities (AUDIO) is not supported by the model"
        )
    )
    configuration = LiveModelResolver._validation_failure(
        "thinking", RuntimeError("thinking level is required and missing")
    )
    assert unsupported.category == "UNSUPPORTED_MODEL_OR_MODALITY"
    assert configuration.category == "CONFIGURATION_ERROR"


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
    with pytest.raises(LiveModelDiscoveryError, match="sans transcription") as captured:
        asyncio.run(resolver.resolve())
    assert resolver.resolution is None
    assert captured.value.failures[0].category == "LIVE_PROTOCOL"

    # Le même fingerprint doit relancer un échange : aucun cache positif ne
    # peut provenir d'un handshake ou d'un tour incomplet.
    successful, calls = discovery_client(
        [model("gemini-3.8-live", ["bidiGenerateContent"])],
        {"gemini-3.8-live"},
    )
    recovered = LiveModelResolver(
        successful, api_key_fingerprint="empty-exchange"
    )
    assert asyncio.run(recovered.resolve()).validated
    assert any(item[0] == "connect" for item in calls)


def test_discovery_timeout_is_categorized_and_not_cached():
    clear_discovery_cache_for_tests()
    closed = []

    class HangingSession:
        async def send_client_content(self, **kwargs): return None
        def receive(self):
            async def values():
                # Une transcription partielle ne valide jamais le candidat sans
                # marqueur de fin de tour.
                yield SimpleNamespace(
                    server_content=SimpleNamespace(
                        output_transcription=SimpleNamespace(text="OK"),
                        turn_complete=False,
                    ),
                    usage_metadata=None,
                )
                await asyncio.Event().wait()
                yield  # pragma: no cover
            return values()

    class HangingContext:
        async def __aenter__(self): return HangingSession()
        async def __aexit__(self, *args): closed.append(True)

    async def list_models():
        return AsyncPager([model("gemini-3.8-live", ["bidiGenerateContent"])])

    client = SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(list=list_models),
        live=SimpleNamespace(connect=lambda **kwargs: HangingContext()),
    ))
    resolver = LiveModelResolver(
        client, api_key_fingerprint="timeout-no-cache", connect_timeout=0.01,
        total_timeout=0.1,
    )
    with pytest.raises(LiveModelDiscoveryError) as captured:
        asyncio.run(resolver.resolve())
    assert resolver.resolution is None
    assert closed == [True]
    failures = captured.value.failures
    assert len(failures) == 1
    assert failures[0].category == "LIVE_TIMEOUT"
    assert "CancelledError" in failures[0].detail


def test_discovery_timeout_candidate_then_success_closes_and_continues():
    clear_discovery_cache_for_tests()
    calls = []

    class Session:
        def __init__(self, name): self.name = name
        async def send_client_content(self, **kwargs): calls.append(("send", self.name))
        def receive(self):
            async def values():
                if self.name == "gemini-3.9-live":
                    await asyncio.Event().wait()
                    yield  # pragma: no cover
                else:
                    yield SimpleNamespace(
                        server_content=SimpleNamespace(
                            output_transcription=SimpleNamespace(text="OK"),
                            turn_complete=True,
                        ),
                        usage_metadata=None,
                    )
            return values()

    class Context:
        def __init__(self, name): self.name = name
        async def __aenter__(self):
            calls.append(("connect", self.name))
            return Session(self.name)
        async def __aexit__(self, *args): calls.append(("close", self.name))

    async def list_models():
        return AsyncPager([
            model("gemini-3.9-live", ["bidiGenerateContent"]),
            model("gemini-3.8-live", ["bidiGenerateContent"]),
        ])

    client = SimpleNamespace(aio=SimpleNamespace(
        models=SimpleNamespace(list=list_models),
        live=SimpleNamespace(connect=lambda model, config: Context(model)),
    ))
    resolver = LiveModelResolver(
        client, api_key_fingerprint="timeout-next", connect_timeout=0.01,
        total_timeout=1.0, max_candidates=2,
    )
    result = asyncio.run(resolver.resolve())
    assert result.model == "gemini-3.8-live"
    assert [(item.model, item.category) for item in result.diagnostics] == [
        ("gemini-3.9-live", "LIVE_TIMEOUT")
    ]
    assert ("close", "gemini-3.9-live") in calls
    assert ("close", "gemini-3.8-live") in calls


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


def test_discovery_candidate_count_is_bounded():
    clear_discovery_cache_for_tests()
    models = [
        model("gemini-3.9-live", ["bidiGenerateContent"]),
        model("gemini-3.8-live", ["bidiGenerateContent"]),
        model("gemini-3.7-live", ["bidiGenerateContent"]),
    ]
    client, calls = discovery_client(models, set())
    resolver = LiveModelResolver(
        client, api_key_fingerprint="candidate-limit", max_candidates=2
    )
    with pytest.raises(LiveModelDiscoveryError) as captured:
        asyncio.run(resolver.resolve())
    connected = [item for item in calls if item[0] == "connect"]
    assert len(connected) == 2
    assert any(
        failure.category == "DISCOVERY_CANDIDATE_LIMIT"
        for failure in captured.value.failures
    )


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
