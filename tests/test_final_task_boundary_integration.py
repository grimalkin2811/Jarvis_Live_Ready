"""Real TaskManager/store shared by injected routine and protocol paths."""
import asyncio
import threading
from dataclasses import replace

import pytest

from UI.dashboard.tasks_service import TasksService
from src.background_tasks.manager import TaskManager
from src.background_tasks.models import TaskStatus
from src.core import runtime as runtime_module
from src.core.runtime import CoreRuntime
from src.protocols import Beat, Protocol, start_protocol
from src.routines import RoutineManager
from src.runtime_status import RuntimeStatusBoard
from src.scheduler import Scheduler
from tests.test_background_tasks import FakeExecutor, FakeRouter
from tests.test_core_runtime import config, factory_set
from tests.test_core_runtime_tool_bindings import Activity, Mode


def test_task_manager_through_scheduled_routine_and_protocol(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime_module, "_default_modes", Mode)
    monkeypatch.setattr(runtime_module, "_default_activity", Activity)
    monkeypatch.setattr("src.background_tasks.manager.notifications.publish", lambda *a, **kw: None)
    monkeypatch.setattr("src.background_tasks.get_default_task_manager",
                        lambda: pytest.fail("unexpected default TaskManager"))
    routine = RoutineManager(tmp_path / "routines.json", include_presets=False)
    assert routine.create_routine("shared", "start_background_task(description=routine,title=routine)")["success"]
    scheduler = Scheduler(tmp_path / "scheduler.db")
    manager = TaskManager("fake", router=FakeRouter(), executor=FakeExecutor(delay=.02),
                          client_factory=lambda: object(), storage_path=tmp_path / "tasks.json")
    monkeypatch.setattr(runtime_module, "_default_routines", lambda: routine)
    completed = threading.Event()
    done_ids = set()
    def on_task(task):
        if task.status is TaskStatus.COMPLETED:
            done_ids.add(task.id)
            if len(done_ids) == 2:
                completed.set()
    manager.add_hook(on_task)
    protocol = Protocol("task-proof", "task", "task", "task", "done", (
        Beat("create", 0, tool="start_background_task", args={"description": "protocol", "title": "protocol"}),
    ))

    async def scenario():
        core = CoreRuntime(config(), factories=replace(factory_set([]), scheduler=lambda: scheduler,
                                                       tasks=lambda key: manager), status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            assert core.services.require("tasks") is manager
            assert TasksService(lambda: core.services.require("tasks")).manager() is manager
            assert scheduler._routines() is routine
            result = await asyncio.to_thread(scheduler._run_routine, "shared")
            assert result["success"], result
            run = start_protocol(protocol, lambda event: None, fast=True, services=core.services)
            await asyncio.to_thread(run.join, 3)
            assert not run.running and run.result["success"]
            assert await asyncio.to_thread(completed.wait, 3)
            assert {t.title for t in manager.list_tasks()} == {"routine", "protocol"}
            core.request_stop()
            await asyncio.wait_for(task, 8)
            assert not manager._thread.is_alive()
            assert all(t.status is TaskStatus.COMPLETED for t in manager.list_tasks())
            restored = TaskManager("fake", storage_path=tmp_path / "tasks.json", auto_start=False)
            assert {t.title for t in restored.list_tasks()} == {"routine", "protocol"}
            persisted = manager.storage_path.read_bytes()
            assert manager.mark_task_as_seen(next(iter(done_ids))) is False
            assert manager.storage_path.read_bytes() == persisted
            with pytest.raises(RuntimeError, match="arrêt"):
                manager.create_task("late", "must fail")
        finally:
            core.request_stop()
            await asyncio.wait_for(task, 8)
            manager.close(wait=True)
    asyncio.run(scenario())


def test_repeated_stop_during_busy_loop_does_not_interrupt_cancellation(tmp_path):
    entered, release, running = threading.Event(), threading.Event(), threading.Event()
    manager = TaskManager("fake", router=FakeRouter(), executor=FakeExecutor(delay=30),
                          client_factory=lambda: object(), storage_path=tmp_path / "repeated.json")
    manager.bind_runtime()
    manager.add_hook(lambda task: running.set() if task.status is TaskStatus.RUNNING else None)
    task = manager.create_task("stop", "active")
    assert running.wait(3)
    def block_loop():
        entered.set()
        assert release.wait(5)
    manager._loop.call_soon_threadsafe(block_loop)
    try:
        assert entered.wait(2)
        for _ in range(5):
            manager.request_stop()
        release.set()
        manager.close(wait=True)
        assert not manager._thread.is_alive()
        assert manager.get_task(task.id).status is TaskStatus.CANCELLED
        restored = TaskManager("fake", storage_path=tmp_path / "repeated.json", auto_start=False)
        assert restored.get_task(task.id).status is TaskStatus.CANCELLED
    finally:
        release.set()
        manager.close(wait=True)


def test_stop_in_ready_to_run_gap_does_not_cancel_client_cleanup(tmp_path):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    ready_set, proceed = threading.Event(), threading.Event()
    class PausedReady(threading.Event):
        def set(self):
            super().set()
            ready_set.set()
            assert proceed.wait(5)
    close_client = AsyncMock()
    manager = TaskManager("fake", storage_path=tmp_path / "gap.json", auto_start=False)
    manager._ready = PausedReady()
    async def initialize():
        manager.client = SimpleNamespace(aio=SimpleNamespace(aclose=close_client))
    manager._initialize_loop_resources = initialize
    errors = []
    def start():
        try:
            manager.start()
        except RuntimeError as exc:
            errors.append(str(exc))
    starter = threading.Thread(target=start)
    starter.start()
    try:
        assert ready_set.wait(2)
        manager.request_stop()
        proceed.set()
        starter.join(3)
        manager.close(wait=True)
        assert not starter.is_alive() and not manager._thread.is_alive()
        assert errors and "arrêt" in errors[0]
        close_client.assert_awaited_once()
    finally:
        proceed.set()
        starter.join(3)
        manager.close(wait=True)
