import hashlib
import json
from pathlib import Path
import sqlite3
from unittest.mock import Mock
import zipfile

import pytest

from src import backup


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setattr(backup.paths, "data_dir", lambda: root)
    database = root / "todo.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE todos (text TEXT)")
        conn.execute("INSERT INTO todos VALUES ('keep')")
    conn.close()
    (root / "routines.json").write_text(json.dumps({"version": 2, "routines": [], "installed_presets": ["hydration"]}))
    (root / "notes.json").write_text('[{"texte": "note"}]')
    (root / "config.json").write_text('{"api_key": "never-back-up"}')
    monkeypatch.setattr(backup, "_sources", lambda: {n: root / n for n in ("todo.db", "routines.json", "notes.json")})
    return root


def test_backup_roundtrip_stages_without_overwrite(storage):
    assert not backup.backup_data()["success"]
    first = backup.backup_data(True)
    second = backup.backup_data(True)
    assert first["success"] and first["fichier"] != second["fichier"]
    assert backup.list_backups()["count"] == 2
    assert "config.json" not in first["files"]
    original = {p.name: p.read_bytes() for p in storage.iterdir() if p.is_file()}
    result = backup.restore_backup(first["fichier"], True)
    assert result["success"] and result["active_data_changed"] is False
    restored = Path(result["dossier"])
    assert json.loads((restored / "routines.json").read_text())["installed_presets"] == ["hydration"]
    assert not (restored / "config.json").exists()
    conn = sqlite3.connect(restored / "todo.db")
    assert conn.execute("SELECT text FROM todos").fetchone() == ("keep",)
    conn.close()
    assert original == {p.name: p.read_bytes() for p in storage.iterdir() if p.is_file()}
    assert backup.restore_backup(first["fichier"], True)["dossier"] != result["dossier"]


def test_wal_and_exclusive_filename(storage, monkeypatch):
    conn = sqlite3.connect(storage / "todo.db")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO todos VALUES ('wal')")
    conn.commit()
    result = backup.backup_data(True)
    restored = backup.restore_backup(result["fichier"], True)
    other = sqlite3.connect(Path(restored["dossier"]) / "todo.db")
    assert other.execute("SELECT COUNT(*) FROM todos").fetchone()[0] == 2
    other.close()
    conn.close()
    monkeypatch.setattr(backup.uuid, "uuid4", lambda: Mock(hex=result["fichier"][7:-4]))
    archive = storage / "backups" / result["fichier"]
    before = archive.read_bytes()
    assert not backup.backup_data(True)["success"]
    assert archive.read_bytes() == before


@pytest.mark.parametrize("filename", ["../x.zip", "/tmp/x.zip", "C:\\x.zip", "other.zip", None])
def test_restore_bad_paths(storage, filename):
    assert not backup.restore_backup(filename, True)["success"]


@pytest.mark.parametrize("kind", ["traversal", "duplicate", "hash", "invalid_sqlite", "invalid_json", "version", "empty", "oversize"])
def test_restore_rejects_corruption_and_zip_attacks(storage, kind, monkeypatch):
    directory = backup._storage()
    name = "jarvis_" + "a" * 32 + ".zip"
    content = b"not a sqlite file"
    entries = {"todo.db": content}
    manifest = {"version": 1, "files": {"todo.db": hashlib.sha256(content).hexdigest()}}
    if kind == "traversal":
        entries["../escape.txt"] = b"escape"
    elif kind == "hash":
        manifest["files"]["todo.db"] = "wrong"
    elif kind == "invalid_json":
        entries = {"notes.json": b"{}"}
        manifest["files"] = {"notes.json": hashlib.sha256(b"{}").hexdigest()}
    elif kind == "version":
        manifest["version"] = 99
    elif kind == "empty":
        entries = {}
        manifest["files"] = {}
    elif kind == "oversize":
        monkeypatch.setattr(backup, "MAX_BYTES", 1)
        entries = {"notes.json": b" " * 20000}
    with zipfile.ZipFile(directory / name, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for n, data in entries.items():
            archive.writestr(n, data)
        if kind == "duplicate":
            with pytest.warns(UserWarning):
                archive.writestr("todo.db", content)
    assert not backup.restore_backup(name, True)["success"]
    assert not list(storage.glob("restore_*"))
    assert not (storage / "escape.txt").exists()


def test_strict_confirmation_and_bad_source(storage):
    for confirm in (1, "true", None):
        assert not backup.backup_data(confirm)["success"]
        assert not backup.restore_backup("x", confirm)["success"]
    (storage / "notes.json").write_text("bad-json")
    assert not backup.backup_data(True)["success"]
    assert backup.list_backups()["count"] == 0


def test_restore_preserves_current_non_resurrection_contract(storage):
    from src.routine_presets import builtin_routines
    from src.routines import RoutineManager
    payload = {"version": 1, "routines": [], "installed_presets": [p["preset_id"] for p in builtin_routines()]}
    (storage / "routines.json").write_text(json.dumps(payload))
    result = backup.backup_data(True)
    restored = backup.restore_backup(result["fichier"], True)
    manager = RoutineManager(Path(restored["dossier"]) / "routines.json")
    assert manager.list_routines()["count"] == 0


def test_invalid_database_and_symlink_backup_directory(storage, tmp_path):
    (storage / "todo.db").write_bytes(b"bad sqlite")
    assert not backup.backup_data(True)["success"]
    assert backup.list_backups()["count"] == 0
    # A failed snapshot closed every handle: replace/remove is possible on Windows too.
    (storage / "todo.db").unlink()
    (storage / "backups").rmdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (storage / "backups").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Symlinks non autorisés par le runner")
    assert not backup.backup_data(True)["success"]
    assert not list(outside.iterdir())
