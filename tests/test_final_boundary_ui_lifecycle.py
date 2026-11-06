"""Proofs for remaining synchronous dashboard access and shutdown races."""
import asyncio
import threading
from dataclasses import replace

from PySide6.QtCore import QCoreApplication

from UI.dashboard.actions import conversation_new
from UI.dashboard.config import ConfigStore
from UI.dashboard.hub import DashboardHub
from src.core.runtime import CoreRuntime
from src.runtime_status import EngineState, RuntimeStatusBoard
from tests.test_core_runtime import config, factory_set


def test_dashboard_synchronous_read_holds_core_and_refuses_late_access():
    app = QCoreApplication.instance() or QCoreApplication([])
    entered, release = threading.Event(), threading.Event()
    closed = []
    results = []

    class Conversation:
        def __init__(self):
            self.resets = 0
        def start_new_conversation(self, reason):
            self.resets += 1
            return {"conversation_id": "one"}
        def get_messages(self):
            entered.set()
            assert release.wait(8)
            # This access would fail if the runtime had destroyed its services.
            assert core.services.require("conversation") is self
            return ()
        def snapshot(self):
            return {"turns": 1}

    conversation = Conversation()

    async def scenario():
        nonlocal core
        core = CoreRuntime(config(), factories=replace(factory_set(closed), conversation=lambda: conversation),
                           status=RuntimeStatusBoard())
        hub = DashboardHub(ConfigStore(None), core=core)
        hub.start()
        runtime_task = asyncio.create_task(core.run())
        worker = None
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            app.processEvents()
            assert hub.conversation() is conversation is core.services.require("conversation")
            worker = threading.Thread(target=lambda: results.append(hub.conversation_data()))
            worker.start()
            assert await asyncio.to_thread(entered.wait, 2)
            core.request_stop()
            assert hub.conversation() is None
            assert hub.conversation_data() is None
            refused = conversation_new(core.services.require, activity_boundary=core.activity_boundary)
            assert not refused.success and conversation.resets == 1
            await asyncio.wait_for(runtime_task, 8)
            assert core.status.snapshot()["engine_state"] == EngineState.FAILED
            assert CoreRuntime._owner is core and not closed and worker.is_alive()
            assert not core.complete_deferred_cleanup()
            release.set()
            await asyncio.to_thread(worker.join, 3)
            assert not worker.is_alive() and results == [((), {"turns": 1})]
            for _ in range(300):
                if CoreRuntime._owner is None:
                    break
                await asyncio.sleep(.01)
            assert CoreRuntime._owner is None
            assert closed == ["tasks", "scheduler"]
            assert core.complete_deferred_cleanup()
            assert hub.conversation() is None and hub.conversation_data() is None
        finally:
            release.set()
            if worker is not None:
                await asyncio.to_thread(worker.join, 3)
            core.request_stop()
            await asyncio.wait_for(runtime_task, 8)
            hub.shutdown()
    core = None
    asyncio.run(scenario())


def test_dashboard_submission_after_stop_creates_no_executor_thread():
    from UI.dashboard.async_jobs import JobRunner
    app = QCoreApplication.instance() or QCoreApplication([])
    assert app is not None
    core = CoreRuntime(config(), factories=factory_set([]), status=RuntimeStatusBoard())
    runner = JobRunner(activity_boundary=core.activity_boundary)
    try:
        core.request_stop()
        assert runner.submit(lambda: None) == 0
        assert not runner._executor._threads
    finally:
        runner.shutdown()


def test_leased_worker_keeps_owned_service_alive_until_real_exit():
    from UI.dashboard.async_jobs import JobRunner
    app = QCoreApplication.instance() or QCoreApplication([])
    entered, release = threading.Event(), threading.Event()
    observed = []

    class Service:
        def __init__(self):
            self.close_count = 0
        def close(self):
            self.close_count += 1
        def use(self):
            assert self.close_count == 0
            observed.append("valid")

    async def scenario():
        core = CoreRuntime(config(), factories=factory_set([]), status=RuntimeStatusBoard())
        service = core.services.register("lifecycle_probe", Service(), close=lambda: service.close())
        runner = JobRunner(activity_boundary=core.activity_boundary)
        runtime_task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            def work():
                owned = core.services.require("lifecycle_probe")
                entered.set()
                assert release.wait(8)
                owned.use()
            assert runner.submit(work)
            assert await asyncio.to_thread(entered.wait, 2)
            core.request_stop()
            await asyncio.wait_for(runtime_task, 8)
            assert service.close_count == 0 and CoreRuntime._owner is core
            assert runner.submit(lambda: observed.append("late")) == 0
            assert not core.complete_deferred_cleanup()
            release.set()
            for _ in range(300):
                app.processEvents()
                if CoreRuntime._owner is None:
                    break
                await asyncio.sleep(.01)
            assert CoreRuntime._owner is None and observed == ["valid"]
            assert service.close_count == 1
            assert core.complete_deferred_cleanup() and core.complete_deferred_cleanup()
            assert service.close_count == 1
        finally:
            release.set()
            runner.shutdown()
            core.request_stop()
            await asyncio.wait_for(runtime_task, 8)
    asyncio.run(scenario())


def test_unresponsive_provider_remains_owned_until_async_disposal_finishes():
    from tests.test_core_runtime import Provider
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        closed = []
        class SlowProvider(Provider):
            async def shutdown(self):
                entered.set()
                await release.wait()
        core = CoreRuntime(config(), factories=factory_set(closed, provider=SlowProvider),
                           status=RuntimeStatusBoard())
        task = asyncio.create_task(core.run())
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            core.request_stop()
            await asyncio.wait_for(entered.wait(), 2)
            assert not task.done() and CoreRuntime._owner is core and not closed
            assert core.activity_boundary.snapshot()["closing"]
            core.request_stop()
            release.set()
            await asyncio.wait_for(task, 8)
            assert closed == ["tasks", "scheduler"] and CoreRuntime._owner is None
            assert core.provider.closed == 1
        finally:
            release.set()
            core.request_stop()
            await asyncio.wait_for(task, 8)
    asyncio.run(scenario())


def test_protocol_admission_is_atomic_even_when_thread_start_is_delayed(monkeypatch):
    from types import SimpleNamespace
    from src import protocols
    entered, release = threading.Event(), threading.Event()
    closed = []
    results = []
    class SlowStartThread(threading.Thread):
        def start(self):
            entered.set()
            assert release.wait(8)
            super().start()
    protocol = protocols.Protocol("boundary-proof", "proof", "proof", "proof", "done", ())

    async def scenario():
        core = CoreRuntime(config(), factories=factory_set(closed), status=RuntimeStatusBoard())
        run_task = asyncio.create_task(core.run())
        launcher = None
        try:
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            def play(*args, **kwargs):
                assert core.services.require("conversation") is not None
                assert not closed
                return {"success": True}
            monkeypatch.setattr(protocols, "play_protocol", play)
            monkeypatch.setattr(protocols, "threading", SimpleNamespace(Thread=SlowStartThread, Event=threading.Event))
            launcher = threading.Thread(target=lambda: results.append(
                protocols.start_protocol(protocol, fast=True, services=core.services)))
            launcher.start()
            assert await asyncio.to_thread(entered.wait, 2)
            core.request_stop()
            # The late attempt is refused before constructing a second thread.
            assert protocols.start_protocol(protocol, fast=True, services=core.services) is None
            await asyncio.wait_for(run_task, 8)
            assert CoreRuntime._owner is core and not closed
            assert core.activity_boundary.snapshot()["active"] == ("protocol.run",)
            release.set()
            await asyncio.to_thread(launcher.join, 3)
            assert not launcher.is_alive() and len(results) == 1
            await asyncio.to_thread(results[0].join, 3)
            assert not results[0].running and results[0].result == {"success": True}
            for _ in range(300):
                if CoreRuntime._owner is None:
                    break
                await asyncio.sleep(.01)
            assert CoreRuntime._owner is None and closed == ["tasks", "scheduler"]
        finally:
            release.set()
            if launcher is not None:
                await asyncio.to_thread(launcher.join, 3)
            core.request_stop()
            await asyncio.wait_for(run_task, 8)
    asyncio.run(scenario())
