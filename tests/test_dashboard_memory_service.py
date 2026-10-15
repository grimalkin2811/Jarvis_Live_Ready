"""Couche de service mémoire du Dashboard — bases ISOLÉES uniquement.

Aucun test ne touche la mémoire réelle de l'utilisateur : chaque test crée
sa propre base SQLite dans un dossier temporaire.
"""

from __future__ import annotations

import os
import sqlite3
import threading

import pytest

from src.memory import MEMORY_REVISIONS_KEPT, MEMORY_SCHEMA_VERSION, MemoryManager
from UI.dashboard.memory_service import MemoryService, describe_changes, text_diff, validate_form, version_of


@pytest.fixture
def manager(tmp_path):
    return MemoryManager(str(tmp_path / "memory.db"))


@pytest.fixture
def service(manager):
    return MemoryService(lambda: manager)


def _create(service, content="Je préfère le thé.", category="preference", importance=3, **kw):
    result = service.create(content, category, importance, **kw)
    assert result["success"], result
    return result["memory"]


def test_schema_migrates_to_v2_with_revision_table(manager, tmp_path):
    conn = sqlite3.connect(manager.database_path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == MEMORY_SCHEMA_VERSION == 2
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    finally:
        conn.close()
    assert {"memory_revisions", "memories_revision_au", "memories_revision_ad"} <= names


def test_existing_v1_database_is_migrated_without_data_loss(tmp_path):
    """Base v1 réaliste (créée par le code v1 : table + FTS + déclencheurs FTS)."""
    path = str(tmp_path / "old.db")
    MemoryManager(path).add_memory("Souvenir v1", category="fact", importance=4, source="voice")
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS memories_revision_au;
        DROP TRIGGER IF EXISTS memories_revision_ad;
        DROP TABLE IF EXISTS memory_revisions;
        ALTER TABLE memories ADD COLUMN colonne_future TEXT DEFAULT 'conservee';
        PRAGMA user_version = 1;
        """
    )
    conn.commit()
    conn.close()
    manager = MemoryManager(path)  # réouverture : migration v1 -> v2
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        conn.close()
    record = manager.get_memory_record(1)["memory"]
    assert record["content"] == "Souvenir v1"
    assert record["colonne_future"] == "conservee"  # colonne inconnue préservée
    service = MemoryService(lambda: manager)
    updated = service.update(1, version_of(record), "Souvenir v2", "fact", 4)
    assert updated["success"], updated
    assert updated["memory"]["colonne_future"] == "conservee"
    history = service.history(1)["revisions"]
    assert [r["content"] for r in history] == ["Souvenir v1"]
    assert manager.check_integrity()["ok"] is True


def test_create_never_overwrites_silently(service):
    first = _create(service, "Mon prénom est Paul", "identity", 5)
    conflict = service.create("Mon prénom est Pierre", "identity", 5)
    assert conflict["success"] is False and conflict["conflict"] is True
    assert conflict["existing"]["id"] == first["id"]
    assert service.get(first["id"])["memory"]["content"] == "Mon prénom est Paul"
    distinct = service.create("Mon prénom est Pierre", "identity", 5, distinct=True)
    assert distinct["success"] and distinct["memory"]["subject_key"] is None


def test_invalid_input_is_rejected_before_any_write(service, manager):
    for content, category, importance in (("", "fact", 3), ("x" * 4001, "fact", 3), ("ok", "inconnue", 3),
                                          ("ok", "fact", 9), ("ok", "fact", "abc")):
        result = service.create(content, category, importance)
        assert result["success"] is False and result.get("validation")
    assert manager.memory_stats()["total"] == 0
    assert validate_form("  a  ", "fact", "2").cleaned == {"content": "a", "category": "fact", "importance": 2}


def test_update_records_history_and_diff(service):
    memory = _create(service)
    result = service.update(memory["id"], version_of(memory), "Je préfère le café.", "preference", 4)
    assert result["success"]
    history = service.history(memory["id"])["revisions"]
    assert history[0]["content"] == "Je préfère le thé."
    assert history[0]["importance"] == 3
    assert ("-", "thé.") in text_diff("Je préfère le thé.", "Je préfère le café.")
    assert "Importance : 3 → 4" in describe_changes(history[0], result["memory"])


def test_unchanged_update_is_not_written(service):
    memory = _create(service)
    result = service.update(memory["id"], version_of(memory), memory["content"], memory["category"],
                            memory["importance"])
    assert result.get("unchanged")
    assert service.history(memory["id"])["revisions"] == []


def test_concurrent_modification_is_detected_even_within_the_same_second(service, manager):
    memory = _create(service)
    stale = version_of(memory)
    # Modification concurrente (voix) — même seconde que la création.
    manager.update_memory(memory["id"], content="Je préfère le chocolat.")
    result = service.update(memory["id"], stale, "Je préfère l'eau.", "preference", 3)
    assert result["success"] is False and result["conflict"] is True
    assert result["current"]["content"] == "Je préfère le chocolat."
    assert service.get(memory["id"])["memory"]["content"] == "Je préfère le chocolat."


def test_parallel_writers_only_one_wins(service):
    memory = _create(service)
    base = version_of(memory)
    results = []
    barrier = threading.Barrier(4)

    def writer(index):
        barrier.wait()
        results.append(service.update(memory["id"], base, f"Version {index}", "preference", 3))

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    winners = [r for r in results if r.get("success")]
    assert len(winners) == 1
    assert all(r.get("conflict") for r in results if not r.get("success"))


def test_delete_requires_current_version_and_is_soft(service, manager):
    memory = _create(service)
    manager.update_memory(memory["id"], importance=5)
    stale = service.delete(memory["id"], version_of(memory))
    assert stale["conflict"] is True
    current = service.get(memory["id"])["memory"]
    assert service.delete(memory["id"], version_of(current))["success"]
    assert service.get(memory["id"])["success"] is False
    assert service.delete(memory["id"], version_of(current))["not_found"] is True
    # Suppression = archivage (comportement historique de delete_memory).
    assert manager.get_memory_record(memory["id"], include_archived=True)["memory"]["archived"] is True


def test_restore_previous_version_is_itself_reversible(service):
    memory = _create(service)
    updated = service.update(memory["id"], version_of(memory), "Version B", "preference", 3)["memory"]
    revision = service.history(memory["id"])["revisions"][0]
    restored = service.restore(memory["id"], revision["id"], version_of(updated))
    assert restored["success"] and restored["memory"]["content"] == "Je préfère le thé."
    history = [r["content"] for r in service.history(memory["id"])["revisions"]]
    assert history[:2] == ["Version B", "Je préfère le thé."]
    stale = service.restore(memory["id"], revision["id"], version_of(updated))
    assert stale["conflict"] is True


def test_history_is_bounded(service):
    memory = _create(service)
    current = memory
    for index in range(MEMORY_REVISIONS_KEPT + 5):
        current = service.update(memory["id"], version_of(current), f"Version {index}", "preference", 3)["memory"]
    assert len(service.history(memory["id"])["revisions"]) == MEMORY_REVISIONS_KEPT


def test_write_failure_rolls_back_and_reports(service, manager, monkeypatch):
    memory = _create(service)
    real_connect = manager._connect

    class FailingConnection:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if sql.lstrip().upper().startswith("UPDATE"):
                raise sqlite3.OperationalError("disk I/O error")
            return self._conn.execute(sql, *args)

    from contextlib import contextmanager

    @contextmanager
    def failing():
        with real_connect() as conn:
            yield FailingConnection(conn)

    monkeypatch.setattr(manager, "_connect", failing)
    result = service.update(memory["id"], version_of(memory), "Jamais écrit", "preference", 3)
    assert result["success"] is False and "rien n'a été modifié" in result["error"]
    monkeypatch.setattr(manager, "_connect", real_connect)
    assert manager.available is True, "une erreur d'écriture attendue ne rend pas la mémoire indisponible"
    assert service.get(memory["id"])["memory"]["content"] == "Je préfère le thé."
    assert manager.check_integrity()["ok"] is True


def test_restart_consistency(tmp_path):
    path = str(tmp_path / "memory.db")
    first = MemoryService(lambda m=MemoryManager(path): m)
    memory = _create(first)
    first.update(memory["id"], version_of(memory), "Après modification", "fact", 2)
    reopened = MemoryManager(path)
    service = MemoryService(lambda: reopened)
    record = service.get(memory["id"])["memory"]
    assert (record["content"], record["category"], record["importance"]) == ("Après modification", "fact", 2)
    assert len(service.history(memory["id"])["revisions"]) == 1
    assert reopened.check_integrity()["ok"] is True


def test_browse_has_no_side_effect_and_handles_accents(service, manager):
    _create(service, "Le café du matin est sacré.", "habit", 2)
    found = service.browse("cafe")
    assert found["total"] == 1
    assert found["memories"][0]["access_count"] == 0  # pas d'effet de bord


def test_disabled_memory_is_reported(tmp_path):
    manager = MemoryManager(str(tmp_path / "m.db"), enabled=False)
    service = MemoryService(lambda: manager)
    assert service.status()["enabled"] is False
    assert service.create("x", "fact", 3)["success"] is False


def test_hard_delete_removes_revisions_for_privacy(manager, service):
    memory = _create(service)
    service.update(memory["id"], version_of(memory), "B", "preference", 3)
    with manager._connect() as conn:
        conn.execute("DELETE FROM memories WHERE id = ?", (memory["id"],))
    assert service.history(memory["id"])["revisions"] == []


def test_no_real_user_database_is_used(manager):
    from src import paths

    assert os.path.abspath(manager.database_path) != os.path.abspath(str(paths.memory_db()))


# ---------------------------------------------------------------------------
# Audit pré-fusion (PR #50) : migration, purge, restauration
# ---------------------------------------------------------------------------


def _downgrade_to_v1(path: str) -> None:
    """Base v1 RÉALISTE : créée par le code actuel puis ramenée au schéma v1."""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            DROP TRIGGER IF EXISTS memories_revision_au;
            DROP TRIGGER IF EXISTS memories_revision_ad;
            DROP TABLE IF EXISTS memory_revisions;
            PRAGMA user_version = 1;
            """
        )
        conn.commit()
    finally:
        conn.close()


def _schema(path: str) -> tuple[int, set[str], list[str]]:
    conn = sqlite3.connect(path)
    try:
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
        contents = [row[0] for row in conn.execute("SELECT content FROM memories ORDER BY id")]
    finally:
        conn.close()
    return version, names, contents


def test_failed_v2_migration_is_rolled_back_then_retried_without_data_loss(tmp_path, monkeypatch):
    import src.memory as memory_module

    path = str(tmp_path / "v1.db")
    MemoryManager(path).add_memory("Souvenir à préserver", category="fact", importance=4, source="voice")
    _downgrade_to_v1(path)

    def failing_migration(conn):
        # Même forme que la vraie migration (une transaction), échec au milieu.
        conn.executescript("BEGIN; CREATE TABLE memory_revisions (id INTEGER PRIMARY KEY);")
        raise sqlite3.OperationalError("database or disk is full")

    real_migration = memory_module._MIGRATIONS[2]
    monkeypatch.setitem(memory_module._MIGRATIONS, 2, failing_migration)
    manager = MemoryManager(path)
    assert manager.available is False and "disk is full" in (manager.last_error or "")
    version, names, contents = _schema(path)
    assert version == 1, "la base reste en v1"
    assert "memory_revisions" not in names, "aucune table à moitié créée (transaction annulée)"
    assert contents == ["Souvenir à préserver"], "données intactes"

    # La cause disparaît : l'accès suivant retente la migration, sans redémarrage.
    monkeypatch.setitem(memory_module._MIGRATIONS, 2, real_migration)
    listing = manager.list_memories()
    assert manager.available is True, listing
    version, names, contents = _schema(path)
    assert version == 2
    assert {"memory_revisions", "memories_revision_au", "memories_revision_ad"} <= names
    assert contents == ["Souvenir à préserver"]
    assert manager.check_integrity()["ok"] is True


def test_history_keeps_exactly_the_most_recent_versions(service):
    memory = _create(service, "Version initiale")
    current = memory
    for index in range(MEMORY_REVISIONS_KEPT + 5):
        current = service.update(memory["id"], version_of(current), f"Version {index}", "preference", 3)["memory"]
    contents = [r["content"] for r in service.history(memory["id"])["revisions"]]
    # Version actuelle = « Version 24 » ; l'historique garde les 20 états précédents
    # les plus récents, du plus récent au plus ancien : Version 23 … Version 4.
    assert contents == [f"Version {i}" for i in range(MEMORY_REVISIONS_KEPT + 3, 3, -1)]
    assert "Version initiale" not in contents


def test_restoring_a_version_identical_to_the_current_one_writes_nothing(service):
    memory = _create(service, "Texte A")
    version_b = service.update(memory["id"], version_of(memory), "Texte B", "preference", 3)["memory"]
    back_to_a = service.update(memory["id"], version_of(version_b), "Texte A", "preference", 3)["memory"]
    revisions = service.history(memory["id"])["revisions"]
    identical = next(r for r in revisions if r["content"] == "Texte A")
    before = service.get(memory["id"])["memory"]
    result = service.restore(memory["id"], identical["id"], version_of(back_to_a))
    assert result["success"] is False and result.get("unchanged") is True, result
    assert "identique" in result["error"]
    after = service.get(memory["id"])["memory"]
    assert after["updated_at"] == before["updated_at"], "rien n'a été écrit"
    assert len(service.history(memory["id"])["revisions"]) == len(revisions)
