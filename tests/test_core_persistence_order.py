"""A save waiting for persistence must snapshot only after it owns the lock."""
import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

from src.background_tasks.manager import TaskManager


def test_snapshot_cannot_overtake_newer_state(tmp_path):
    class SignallingLock:
        def __init__(self):
            self.lock = threading.Lock()
            self.waiting = threading.Event()

        def __enter__(self):
            self.waiting.set()
            self.lock.acquire()
            return self

        def __exit__(self, *args):
            self.lock.release()

    manager = TaskManager.__new__(TaskManager)
    manager._lock = threading.RLock()
    manager._persistence_lock = SignallingLock()
    manager.storage_path = tmp_path / "tasks.json"
    state = {"version": 1}
    manager._tasks = {"task": SimpleNamespace(to_dict=lambda: dict(state))}
    writes = []
    manager._persistence_lock.lock.acquire()
    with patch("src.background_tasks.manager.json_state.write_text_atomic",
               side_effect=lambda path, text: writes.append(json.loads(text))):
        worker = threading.Thread(target=manager._save)
        worker.start()
        assert manager._persistence_lock.waiting.wait(2)
        with manager._lock:
            state["version"] = 2
        manager._persistence_lock.lock.release()
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert writes[0]["tasks"][0]["version"] == 2
