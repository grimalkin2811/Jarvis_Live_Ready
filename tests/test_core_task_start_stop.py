"""A task worker closed while its event loop initializes must not survive."""
import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.background_tasks.manager import TaskManager


def test_close_during_worker_initialization_stops_worker(tmp_path):
    entered, release = threading.Event(), threading.Event()
    manager = TaskManager("key", storage_path=tmp_path / "tasks.json", auto_start=False)

    async def initialize():
        entered.set()
        release.wait(3)

    manager._initialize_loop_resources = initialize
    error = []
    def start():
        try:
            manager.start()
        except Exception as exc:
            error.append(exc)
    starter = threading.Thread(target=start)
    starter.start()
    assert entered.wait(2)
    try:
        manager.close(wait=False)
        with pytest.raises(RuntimeError, match="en cours d'arrêt"):
            manager.start()
        release.set()
        starter.join(3)
        manager._thread.join(3)
        assert not manager._thread.is_alive()
        assert len(error) == 1 and "arrêté pendant son démarrage" in str(error[0])
    finally:
        release.set()
        if manager._thread and manager._thread.is_alive():
            manager._loop.call_soon_threadsafe(manager._loop.stop)
            manager._thread.join(3)
        starter.join(3)


def test_close_after_initialization_is_idempotent(tmp_path):
    manager = TaskManager("key", storage_path=tmp_path / "tasks.json", auto_start=False)
    manager._initialize_loop_resources = lambda: asyncio.sleep(0)
    manager.start()
    worker = manager._thread
    manager.close(wait=True)
    manager.close(wait=True)
    assert not worker.is_alive()


def test_initialization_failure_still_closes_partially_created_client(tmp_path):
    manager = TaskManager("key", storage_path=tmp_path / "tasks.json", auto_start=False)
    close_client = AsyncMock()
    async def initialize():
        manager.client = SimpleNamespace(aio=SimpleNamespace(aclose=close_client))
        raise RuntimeError("initialization failed")
    manager._initialize_loop_resources = initialize
    with pytest.raises(RuntimeError, match="initialization failed"):
        manager.start()
    manager._thread.join(3)
    assert not manager._thread.is_alive()
    close_client.assert_awaited_once()
