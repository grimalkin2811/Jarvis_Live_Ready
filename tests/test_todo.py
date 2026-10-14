"""Tests de la liste de tâches persistante (``src.todo``).

    python -m pytest tests/test_todo.py
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.todo import TodoManager  # noqa: E402


class TestTodoManager(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.manager = TodoManager(os.path.join(self.directory.name, "todo.db"))

    def tearDown(self):
        self.directory.cleanup()

    def test_add_and_list(self):
        created = self.manager.add_task("réviser la présentation")
        self.assertTrue(created["success"])
        self.assertEqual(created["restantes"], 1)

        listing = self.manager.list_tasks()
        self.assertEqual(listing["count"], 1)
        self.assertEqual(listing["taches"][0]["tache"], "réviser la présentation")
        self.assertEqual(listing["taches"][0]["statut"], "pending")

    def test_empty_task_rejected(self):
        self.assertFalse(self.manager.add_task("   ")["success"])

    def test_due_date_parsed(self):
        created = self.manager.add_task("appeler Paul", due="demain à 9h")
        self.assertTrue(created["success"])
        self.assertIn("echeance", created)
        self.assertFalse(self.manager.add_task("x", due="n'importe quoi")["success"])

    def test_complete_by_label_and_id(self):
        first = self.manager.add_task("acheter du pain")
        second = self.manager.add_task("sortir le chien")

        done = self.manager.complete_task(label="acheter du pain")
        self.assertTrue(done["success"])
        self.assertEqual(done["id"], first["id"])
        self.assertEqual(done["restantes"], 1)

        done2 = self.manager.complete_task(task_id=second["id"])
        self.assertTrue(done2["success"])
        self.assertEqual(self.manager.list_tasks()["count"], 0)
        self.assertEqual(self.manager.list_tasks(status="done")["count"], 2)

    def test_reopen_and_delete(self):
        created = self.manager.add_task("ranger le bureau")
        self.manager.complete_task(task_id=created["id"])
        self.assertTrue(self.manager.reopen_task(task_id=created["id"])["success"])
        self.assertEqual(self.manager.list_tasks()["count"], 1)
        self.assertTrue(self.manager.delete_task(task_id=created["id"], confirm=True)["success"])
        self.assertEqual(self.manager.list_tasks()["count"], 0)

    def test_unknown_task(self):
        self.assertFalse(self.manager.complete_task(label="inexistante")["success"])
        self.assertFalse(self.manager.delete_task(task_id=999)["success"])

    def test_clear_requires_confirmation(self):
        self.manager.add_task("tâche 1")
        self.manager.add_task("tâche 2")
        self.manager.complete_task(label="tâche 1")

        self.assertFalse(self.manager.clear_tasks()["success"])

        only_done = self.manager.clear_tasks(only_done=True, confirm=True)
        self.assertTrue(only_done["success"])
        self.assertEqual(only_done["supprimees"], 1)

        self.assertTrue(self.manager.clear_tasks(confirm=True)["success"])
        self.assertEqual(self.manager.list_tasks(status="all")["count"], 0)

    def test_priority_normalized_and_sorted(self):
        self.manager.add_task("basse priorité", priority="basse")
        self.manager.add_task("urgent", priority="haute")
        tasks = self.manager.list_tasks()["taches"]
        self.assertEqual(tasks[0]["tache"], "urgent")
        self.assertEqual(tasks[0]["priorite"], "haute")

    def test_export(self):
        self.manager.add_task("exportable")
        exported = self.manager.export()
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["tache"], "exportable")


if __name__ == "__main__":
    unittest.main()


def test_persistence_negative_inputs_and_ambiguity(tmp_path):
    manager = TodoManager(tmp_path / "todo.db")
    item = manager.add_task("unique")
    assert TodoManager(manager.database_path).list_tasks()["count"] == 1
    for value in (None, 1, [], "x" * 301):
        assert not manager.add_task(value)["success"]
    assert not manager.add_task("x", priority="unknown")["success"]
    for value in (True, 1.5, "1", -1):
        assert not manager.complete_task(task_id=value)["success"]
    for value in (False, "true", 1, None):
        assert not manager.delete_task(item["id"], confirm=value)["success"]
        assert not manager.clear_tasks(confirm=value, only_done=True)["success"]
    for value in ("%", "uni", "_"):
        assert not manager.complete_task(label=value)["success"]
    manager.add_task("unique")
    assert not manager.complete_task(label="unique")["success"]
    assert manager.complete_task(item["id"])["success"]


def test_capacity_reopen_and_unavailable(tmp_path, monkeypatch):
    from src import todo
    monkeypatch.setattr(todo, "MAX_TASKS", 1)
    manager = TodoManager(tmp_path / "todo.db")
    item = manager.add_task("one")["id"]
    assert not manager.add_task("two")["success"]
    manager.complete_task(item)
    manager.add_task("two")
    assert not manager.reopen_task(item)["success"]
    assert not TodoManager(tmp_path / "disabled.db", enabled=False).list_tasks()["success"]


def test_connection_commit_rollback_and_close(tmp_path):
    import sqlite3
    import pytest
    manager = TodoManager(tmp_path / "todo.db")
    with pytest.raises(RuntimeError):
        with manager._connect() as conn:
            conn.execute("DELETE FROM todos")
            raise RuntimeError("rollback")
    with pytest.raises(sqlite3.ProgrammingError):
        conn.execute("SELECT 1")
