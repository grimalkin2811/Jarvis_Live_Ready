"""Sauvegardes locales sans écrasement ni secrets de configuration.

SQLite est copié par son API de backup (WAL compris). Une restauration extrait
vers un NOUVEAU dossier de préparation, jamais vers les données actives. Il faut
fermer Jarvis, inspecter le dossier puis le sélectionner manuellement comme
JARVIS_DATA_DIR. Aucun rappel ou routine n'est lancé pendant la restauration.
"""
from __future__ import annotations

from contextlib import closing

import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
import zipfile

from . import paths

MAX_BYTES = 50 * 1024 * 1024
ALLOWED_FILES = frozenset({"memory.db", "schedule.db", "todo.db", "activity.db", "routines.json", "notes.json"})
_NAME = re.compile(r"jarvis_[0-9a-f]{32}\.zip\Z")


def _error(message):
    return {"success": False, "error": message}


def _storage():
    root = paths.data_dir().resolve()
    directory = root / "backups"
    if directory.is_symlink() or not directory.resolve().is_relative_to(root):
        raise ValueError("Dossier de sauvegarde redirigé")
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _sources():
    from .memory import get_default_memory_manager
    from .routines import get_default_routine_manager
    from .scheduler import get_default_scheduler
    from .todo import get_default_todo_manager
    from .activity import get_default_activity_log
    from . import tools
    return {
        "memory.db": Path(get_default_memory_manager().database_path),
        "schedule.db": Path(get_default_scheduler().database_path),
        "todo.db": Path(get_default_todo_manager().database_path),
        "activity.db": Path(get_default_activity_log().database_path),
        "routines.json": Path(get_default_routine_manager().path),
        "notes.json": Path(tools.NOTES_FILE),
    }


def _sqlite_snapshot(source, destination):
    with closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as reader:
        with closing(sqlite3.connect(destination)) as writer:
            deadline = time.monotonic() + 10
            page_size = reader.execute("PRAGMA page_size").fetchone()[0]

            def progress(status, remaining, total):
                if time.monotonic() > deadline or total * page_size > MAX_BYTES:
                    raise ValueError("Base trop grande ou occupée")

            reader.backup(writer, pages=256, progress=progress)


def backup_data(confirm=False):
    if confirm is not True:
        return _error("Accord requis : copie locale non chiffrée de données personnelles.")
    target = None
    created = False
    try:
        directory = _storage()
        with tempfile.TemporaryDirectory(dir=directory) as temporary:
            entries = {}
            total = 0
            for name, source in _sources().items():
                if not source.exists():
                    continue
                if source.is_symlink() or not source.is_file() or source.stat().st_size > MAX_BYTES:
                    raise ValueError("Source invalide ou trop grande")
                if name.endswith(".db"):
                    snapshot = Path(temporary) / name
                    _sqlite_snapshot(source, snapshot)
                    data = snapshot.read_bytes()
                else:
                    with source.open("rb") as handle:
                        data = handle.read(MAX_BYTES + 1)
                    json.loads(data)
                total += len(data)
                if total > MAX_BYTES:
                    raise ValueError("Sauvegarde trop grande")
                entries[name] = data
            if not entries:
                raise ValueError("Aucune donnée à sauvegarder")
            manifest = {"version": 1, "created_at": dt.datetime.now().isoformat(),
                        "files": {name: hashlib.sha256(data).hexdigest() for name, data in entries.items()}}
            target = directory / f"jarvis_{uuid.uuid4().hex}.zip"
            with target.open("xb") as output:
                created = True
                with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("manifest.json", json.dumps(manifest))
                    for name, data in entries.items():
                        archive.writestr(name, data)
        return {"success": True, "fichier": target.name, "files": sorted(entries),
                "message": "Copie locale non chiffrée ; aucune configuration ni clé API incluse."}
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile):
        if created and target is not None and target.exists():
            target.unlink()
        return _error("Sauvegarde impossible ; aucune donnée active modifiée.")


def list_backups():
    try:
        files = sorted((p for p in _storage().iterdir() if _NAME.fullmatch(p.name)
                        and p.is_file() and not p.is_symlink()), key=lambda p: p.stat().st_mtime, reverse=True)
        return {"success": True, "sauvegardes": [{"fichier": p.name, "octets": p.stat().st_size}
                                                 for p in files[:20]], "count": len(files)}
    except (OSError, ValueError):
        return _error("Liste des sauvegardes inaccessible.")


def restore_backup(filename, confirm=False):
    if confirm is not True:
        return _error("Accord explicite requis pour préparer une restauration locale.")
    if not isinstance(filename, str) or not _NAME.fullmatch(filename):
        return _error("Nom de sauvegarde invalide ; utilisez list_backups.")
    staging = None
    try:
        source = _storage() / filename
        if source.is_symlink() or source.stat().st_size > MAX_BYTES + 1024 * 1024:
            raise ValueError("Archive invalide")
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if (len(names) != len(set(names)) or "manifest.json" not in names
                    or not set(names) <= ALLOWED_FILES | {"manifest.json"}
                    or any(info.file_size < 0 for info in infos)
                    or sum(info.file_size for info in infos) > MAX_BYTES + 16384
                    or archive.getinfo("manifest.json").file_size > 16384):
                raise ValueError("Contenu d'archive interdit")
            manifest = json.loads(archive.read("manifest.json"))
            if (not isinstance(manifest, dict) or manifest.get("version") != 1
                    or not isinstance(manifest.get("files"), dict)
                    or set(manifest["files"]) != set(names) - {"manifest.json"}
                    or not manifest["files"]):
                raise ValueError("Manifeste invalide")
            entries = {}
            for name, digest in manifest["files"].items():
                data = archive.read(name)
                if hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("Empreinte incorrecte")
                if name.endswith(".json"):
                    payload = json.loads(data)
                    if not isinstance(payload, dict if name == "routines.json" else list):
                        raise ValueError("Format JSON incorrect")
                entries[name] = data
        staging = Path(tempfile.mkdtemp(prefix="restore_", dir=paths.data_dir()))
        for name, data in entries.items():
            target = staging / name
            with target.open("xb") as handle:
                handle.write(data)
            if name.endswith(".db"):
                conn = sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)
                try:
                    if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
                        raise ValueError("Base SQLite invalide")
                finally:
                    conn.close()
        return {"success": True, "dossier": str(staging), "active_data_changed": False,
                "message": "Restauration préparée seulement. Fermez Jarvis puis inspectez et sélectionnez ce dossier manuellement. "
                           "Les rappels et routines sauvegardés peuvent être actifs au prochain lancement."}
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, zipfile.BadZipFile, RuntimeError):
        if staging is not None:
            shutil.rmtree(staging)
        return _error("Restauration refusée ; données actives inchangées.")
