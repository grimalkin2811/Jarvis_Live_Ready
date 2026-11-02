"""Actual worker threads must outlive neither their services nor the owner."""
import asyncio
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication

from UI.dashboard.async_jobs import JobRunner
from src.background_tasks import manager as task_module
from src.background_tasks.manager import TaskManager
from src.core.activity_boundary import ActivityBoundary
from src.core.context import ContextRequest
from src.core.runtime import CoreRuntime
from src.runtime_status import EngineState, RuntimeStatusBoard
from tests.test_core_runtime import config, factory_set


def test_boundary_counts_exceptions_multiple_workers_and_rejects():
    gate = ActivityBoundary()
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()
    errors = []

    def work(index):
        try:
            with gate.activity(f"worker-{index}"):
                entered[index].set()
                assert release.wait(5)
                if index:
                    raise ValueError("expected")
        except ValueError as exc:
            errors.append(str(exc))

    workers = [threading.Thread(target=work, args=(i,)) for i in range(2)]
    for worker in workers:
        worker.start()
    try:
        assert all(event.wait(2) for event in entered)
        assert set(gate.snapshot()["active"]) == {"worker-0", "worker-1"}
        assert not gate.drain(0.01)
        with pytest.raises(RuntimeError, match="activité refusée"):
            with gate.activity("late"):
                pytest.fail("must not enter")
        release.set()
        for worker in workers:
            worker.join(2)
            assert not worker.is_alive()
        assert gate.drain(0) and gate.drain(0)
        assert errors == ["expected"]
    finally:
        release.set()
        for worker in workers:
            worker.join(3)


def test_context_timeout_and_dashboard_work_hold_runtime_until_real_exit():
    app = QCoreApplication.instance() or QCoreApplication([])
    context_entered, context_release = threading.Event(), threading.Event()
    job_entered, job_release = threading.Event(), threading.Event()
    late_called = threading.Event()
    closed = []

    class Conversation:
        def start_new_conversation(self, reason):
            pass
        def get_messages(self, limit):
            context_entered.set()
            assert context_release.wait(8)
            return []

    async def scenario():
        core = CoreRuntime(config(), factories=replace(factory_set(closed), conversation=Conversation),
                           status=RuntimeStatusBoard())
        runner = JobRunner(max_workers=1, activity_boundary=core.activity_boundary)
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            engine = core.services.require("context")
            assert engine is core.services.require("context")
            snapshot_task = asyncio.create_task(engine.build_async(ContextRequest(runtime=False), timeout=0.05))
            assert await asyncio.to_thread(context_entered.wait, 2)
            with pytest.raises(TimeoutError):
                await snapshot_task

            def blocking_job():
                job_entered.set()
                assert job_release.wait(8)
                assert core.services.require("conversation") is not None
            assert runner.submit(blocking_job)
            assert await asyncio.to_thread(job_entered.wait, 2)
            assert runner.submit(late_called.set)
            core.request_stop()
            core.request_stop()
            await asyncio.wait_for(task, 8)
            assert core.status.snapshot()["engine_state"] == EngineState.FAILED
            assert CoreRuntime._owner is core
            assert not closed
            assert sorted(core.activity_boundary.snapshot()["active"]) == ["context.build", "dashboard.job"]
            with pytest.raises(RuntimeError, match="activité refusée"):
                engine.build(ContextRequest(runtime=False))
            assert not core.complete_deferred_cleanup()
            job_release.set()
            context_release.set()
            assert await asyncio.to_thread(core.activity_boundary.drain, 3)
            for _ in range(300):
                app.processEvents()
                if CoreRuntime._owner is None and runner.pending() == 0:
                    break
                await asyncio.sleep(.01)
            assert CoreRuntime._owner is None
            assert closed == ["tasks", "scheduler"]
            assert not late_called.is_set()
            assert runner.failed == 1
            assert core.complete_deferred_cleanup()
        finally:
            context_release.set()
            job_release.set()
            runner.shutdown()
            core.request_stop()
            await asyncio.wait_for(task, 8)
    asyncio.run(scenario())


def test_task_manager_real_initialization_worker_defers_core_cleanup(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    manager = TaskManager("fake", storage_path=tmp_path / "tasks.json", auto_start=False)
    monkeypatch.setattr(task_module, "_DEFAULT", manager)
    errors = []

    async def initialize():
        entered.set()
        assert release.wait(8)

    manager._initialize_loop_resources = initialize
    def start():
        try:
            manager.start()
        except RuntimeError as exc:
            errors.append(str(exc))
    starter = threading.Thread(target=start)
    starter.start()
    assert entered.wait(2)
    worker = manager._thread
    closed = []

    async def scenario():
        core = CoreRuntime(config(), factories=replace(factory_set(closed), tasks=lambda key: manager),
                           status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            assert core.services.require("tasks") is manager is task_module.get_default_task_manager()
            core.request_stop()
            await asyncio.wait_for(task, 8)
            assert worker.is_alive() and CoreRuntime._owner is core and not closed
            assert core.services.require("tasks") is manager
            with pytest.raises(RuntimeError, match="arrêt"):
                manager.start()
            with pytest.raises(RuntimeError, match="arrêt"):
                task_module.configure_default_task_manager("fake")
            assert not core.complete_deferred_cleanup()
            release.set()
            await asyncio.to_thread(worker.join, 3)
            await asyncio.to_thread(starter.join, 3)
            for _ in range(300):
                if CoreRuntime._owner is None:
                    break
                await asyncio.sleep(.01)
            assert not worker.is_alive() and not starter.is_alive()
            assert errors and "arrêté pendant son démarrage" in errors[0]
            assert CoreRuntime._owner is None
            assert closed == ["scheduler"]
            assert core.complete_deferred_cleanup()
        finally:
            release.set()
            core.request_stop()
            await asyncio.wait_for(task, 8)
            await asyncio.to_thread(starter.join, 3)
            await asyncio.to_thread(worker.join, 3)
    asyncio.run(scenario())


def test_task_client_close_error_is_visible(tmp_path):
    manager = TaskManager("fake", storage_path=tmp_path / "tasks.json", auto_start=False)
    async def close():
        raise ValueError("client close failed")
    async def initialize():
        manager.client = SimpleNamespace(aio=SimpleNamespace(aclose=close))
    manager._initialize_loop_resources = initialize
    manager.start()
    with pytest.raises(RuntimeError, match="Fermeture du client"):
        manager.close(wait=True)
    assert not manager._thread.is_alive()


def test_real_tasks_tool_dashboard_events_and_restoration(tmp_path, monkeypatch):
    from UI.dashboard.tasks_service import TasksService
    from src.background_tasks.models import TaskStatus
    from src.core import runtime as runtime_module
    from tests.test_background_tasks import FakeExecutor, FakeRouter
    from tests.test_core_runtime_tool_bindings import Activity, Mode

    monkeypatch.setattr(runtime_module, "_default_modes", Mode)
    monkeypatch.setattr(runtime_module, "_default_activity", Activity)
    monkeypatch.setattr(task_module.notifications, "publish", lambda *a, **kw: None)
    path = tmp_path / "tasks.json"
    manager = TaskManager("fake", router=FakeRouter(), executor=FakeExecutor(delay=.02),
                          client_factory=lambda: object(), storage_path=path)
    events = []
    completed = threading.Event()
    def on_task(task):
        events.append((task.id, task.status))
        if task.status is TaskStatus.COMPLETED:
            completed.set()
    manager.add_hook(on_task)

    async def scenario():
        core = CoreRuntime(config(), factories=replace(factory_set([]), tasks=lambda key: manager),
                           status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            dashboard = TasksService(lambda: core.services.require("tasks"))
            assert dashboard.manager() is core.services.require("tasks") is manager
            tools = core.services.require("tools").functions
            created = await asyncio.to_thread(tools["start_background_task"], description="D", title="Partagée")
            assert created["success"]
            assert await asyncio.to_thread(completed.wait, 3)
            assert manager.get_task(created["id"]).status is TaskStatus.COMPLETED
            assert any(t.id == created["id"] for t in dashboard.manager().list_tasks())
            assert tools["list_background_tasks"]()["tasks"][0]["id"] == created["id"]
            assert {status for task_id, status in events if task_id == created["id"]} >= {
                TaskStatus.QUEUED, TaskStatus.RUNNING, TaskStatus.COMPLETED}
            core.request_stop()
            await asyncio.wait_for(task, 8)
            assert not manager._thread.is_alive()
            assert not tools["start_background_task"](description="après arrêt")["success"]
            restored = TaskManager("fake", storage_path=path, auto_start=False)
            assert restored.get_task(created["id"]).status is TaskStatus.COMPLETED
            assert restored.get_task(created["id"]).result == "Résultat détaillé"
        finally:
            core.request_stop()
            await asyncio.wait_for(task, 8)
            manager.close(wait=True)
    asyncio.run(scenario())


def test_audio_worker_is_joined_before_service_cleanup():
    from tests.test_core_runtime import Audio
    entered, release = threading.Event(), threading.Event()
    closed = []
    class BlockingAudio(Audio):
        def start(self):
            def work():
                entered.set()
                release.wait(8)
            self.wake_thread = threading.Thread(target=work, name="test-audio-worker")
            self.wake_thread.start()
        def request_stop(self):
            self.requested = True
        def stop(self):
            assert not self.wake_thread.is_alive()
            super().stop()
    async def scenario():
        core = CoreRuntime(config(), factories=factory_set(closed, audio=BlockingAudio),
                           status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            assert await asyncio.to_thread(entered.wait, 2)
            core.request_stop()
            await asyncio.wait_for(task, 8)
            assert core.audio.requested and not core.audio.stopped
            assert core.audio.wake_thread.is_alive() and not closed
            assert not core.complete_deferred_cleanup()
            release.set()
            await asyncio.to_thread(core.audio.wake_thread.join, 3)
            for _ in range(300):
                if CoreRuntime._owner is None:
                    break
                await asyncio.sleep(.01)
            assert CoreRuntime._owner is None
            assert core.audio.stopped and closed == ["tasks", "scheduler"]
        finally:
            release.set()
            core.request_stop()
            await asyncio.wait_for(task, 8)
    asyncio.run(scenario())


def test_real_task_stop_cancels_and_restores_terminal_state(tmp_path):
    from src.background_tasks.models import TaskStatus
    from tests.test_background_tasks import FakeExecutor, FakeRouter
    path = tmp_path / "tasks.json"
    manager = TaskManager("fake", router=FakeRouter(), executor=FakeExecutor(delay=30),
                          client_factory=lambda: object(), storage_path=path)
    manager.bind_runtime()
    events = []
    running = threading.Event()
    def on_task(task):
        events.append(task.status)
        if task.status is TaskStatus.RUNNING:
            running.set()
    manager.add_hook(on_task)
    queued = manager.create_task("interrompue", "description")
    assert running.wait(3)
    manager.request_stop()
    manager.close(wait=True)
    assert not manager._thread.is_alive()
    assert manager.get_task(queued.id).status is TaskStatus.CANCELLED
    assert TaskStatus.CANCELLED in events
    manager.release_runtime()
    with pytest.raises(RuntimeError, match="arrêt"):
        manager.start()
    restored = TaskManager("fake", storage_path=path, auto_start=False)
    assert restored.get_task(queued.id).status is TaskStatus.CANCELLED


def test_core_timer_is_cancelled_and_joined_without_touching_standalone_timer(monkeypatch):
    from src import tools
    from src.core import runtime as runtime_module
    from tests.test_core_runtime_tool_bindings import Activity, Mode
    monkeypatch.setattr(runtime_module, "_default_modes", Mode)
    monkeypatch.setattr(runtime_module, "_default_activity", Activity)
    standalone = tools.set_timer(seconds=30, label="autonome")
    assert standalone["success"]

    async def scenario():
        core = CoreRuntime(config(), factories=factory_set([]), status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            timer = core.services.require("tools").functions["set_timer"](seconds=30, label="core")
            assert timer["success"]
            worker = tools._TIMERS[timer["id"]]["timer"]
            assert worker.is_alive()
            core.request_stop()
            await asyncio.wait_for(task, 8)
            assert not worker.is_alive()
            assert tools._TIMERS[timer["id"]]["done"]
            assert not tools._TIMERS[standalone["id"]]["done"]
        finally:
            core.request_stop()
            await asyncio.wait_for(task, 8)
    try:
        asyncio.run(scenario())
    finally:
        tools.cancel_timer(standalone["id"])
        tools._TIMERS[standalone["id"]]["timer"].join(3)


def test_running_timer_callback_delays_cleanup(monkeypatch):
    from src import tools
    from src.core import runtime as runtime_module
    from tests.test_core_runtime_tool_bindings import Activity, Mode
    monkeypatch.setattr(runtime_module, "_default_modes", Mode)
    monkeypatch.setattr(runtime_module, "_default_activity", Activity)
    entered, release = threading.Event(), threading.Event()
    def callback(timer_id, label):
        entered.set()
        assert release.wait(8)
    monkeypatch.setattr(tools, "_timer_finished", callback)
    closed = []
    async def scenario():
        core = CoreRuntime(config(), factories=factory_set(closed), status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            timer = core.services.require("tools").functions["set_timer"](seconds=1)
            assert timer["success"]
            worker = tools._TIMERS[timer["id"]]["timer"]
            assert await asyncio.to_thread(entered.wait, 3)
            core.request_stop()
            await asyncio.wait_for(task, 8)
            assert worker.is_alive() and not closed and CoreRuntime._owner is core
            assert not core.complete_deferred_cleanup()
            release.set()
            await asyncio.to_thread(worker.join, 3)
            for _ in range(300):
                if CoreRuntime._owner is None:
                    break
                await asyncio.sleep(.01)
            assert not worker.is_alive() and CoreRuntime._owner is None
            assert closed == ["tasks", "scheduler"]
        finally:
            release.set()
            core.request_stop()
            await asyncio.wait_for(task, 8)
    asyncio.run(scenario())


def test_default_task_factory_binds_manager_before_start(tmp_path, monkeypatch):
    from src import background_tasks
    from src.core import runtime as runtime_module
    manager = TaskManager("fake", storage_path=tmp_path / "tasks.json", auto_start=False,
                          client_factory=lambda: object())
    def configure(key, *, auto_start):
        assert key == "test" and auto_start is False
        return manager
    monkeypatch.setattr(background_tasks, "configure_default_task_manager", configure)
    original_start = manager.start
    def start():
        owner = CoreRuntime._owner
        assert owner is not None and owner.services.require("tasks") is manager
        assert manager._runtime_bound
        return original_start()
    monkeypatch.setattr(manager, "start", start)

    async def scenario():
        core = CoreRuntime(config(), factories=replace(factory_set([]), tasks=runtime_module._default_tasks),
                           status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            assert manager._thread.is_alive()
        finally:
            core.request_stop()
            await asyncio.wait_for(task, 8)
        assert not manager._thread.is_alive()
    asyncio.run(scenario())


def test_task_model_discovery_is_cancelled_when_owner_stops(tmp_path):
    entered = threading.Event()
    manager = TaskManager("fake", storage_path=tmp_path / "tasks.json", auto_start=False,
                          client_factory=lambda: object())
    manager.bind_runtime()
    manager.start()
    class Resolver:
        async def resolve(self):
            entered.set()
            await asyncio.Event().wait()
    manager.resolver = Resolver()
    errors = []
    def resolve():
        try:
            manager.resolve_live_model(timeout=3)
        except BaseException as exc:
            errors.append(exc)
    caller = threading.Thread(target=resolve)
    caller.start()
    try:
        assert entered.wait(2)
        manager.close(wait=True)
        caller.join(3)
        assert not caller.is_alive() and not manager._thread.is_alive()
        assert errors
    finally:
        manager.close(wait=True)
        caller.join(3)
