"""Garde des données réelles pendant la validation (activée par ``sitecustomize``).

Chaque processus Python lancé par le protocole de validation (pytest, scripts
de contrôle, scénarios réels) charge ce module AVANT tout autre code quand la
variable ``JARVIS_VALIDATION_GARDE=1`` est posée. Il refuse alors, en levant
``PermissionError`` :

* tout accès (lecture OU écriture, y compris ``sqlite3.connect``) sous les
  racines « données réelles » (``JARVIS_VALIDATION_INTERDIT``) — dossier
  ``%LOCALAPPDATA%\\Jarvis`` (données ET installation quotidienne),
  ``~/.jarvis``… ;
* toute écriture, suppression ou renommage sous les racines « lecture seule »
  (``JARVIS_VALIDATION_LECTURE_SEULE``) — la copie de travail de l'utilisateur.

Chaque refus est consigné (JSON par ligne) dans ``JARVIS_VALIDATION_INCIDENTS``
avec l'opération, le chemin et l'emplacement du code appelant, pour que le
rapport puisse signaler précisément l'incident. Les données ne sont jamais
touchées : l'opération est bloquée avant d'atteindre le système de fichiers.

Ce module ne dépend que de la bibliothèque standard.
"""

from __future__ import annotations

import builtins
import functools
import io
import json
import os
import shutil
import sys
import threading
import time
import traceback

_INSTALLED = False
_LOCK = threading.Lock()
_STATE = {"test": "", "incidents": 0}
_ORIGINALS: dict[str, object] = {}

#: Drapeaux ``os.open`` qui impliquent une écriture.
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


@functools.lru_cache(maxsize=4096)
def _resolve(absolute: str) -> str:
    try:
        return os.path.normcase(os.path.realpath(absolute))
    except (OSError, ValueError):
        return os.path.normcase(absolute)


def _norm(path) -> str | None:
    """Chemin absolu normalisé (liens résolus, casse Windows) ou None."""
    try:
        if isinstance(path, int):
            return None
        raw = os.fsdecode(os.fspath(path))
    except TypeError:
        return None
    if raw.startswith("file:"):
        # URI SQLite (file:chemin?mode=ro).
        raw = raw[5:].split("?", 1)[0]
        if raw.startswith("///"):
            raw = raw[2:]
    if not raw or raw == ":memory:":
        return None
    try:
        absolute = os.path.abspath(os.path.expanduser(raw))
    except (OSError, ValueError):
        return None
    return _resolve(absolute)


def _roots(variable: str) -> list[str]:
    roots = []
    for item in os.environ.get(variable, "").split(os.pathsep):
        item = item.strip()
        if item:
            normalized = _norm(item)
            if normalized:
                roots.append(normalized.rstrip("\\/"))
    return roots


_FORBIDDEN: list[str] = []
_READ_ONLY: list[str] = []


def _under(path: str, roots: list[str]) -> bool:
    for root in roots:
        if path == root or path.startswith(root + os.sep) or path.startswith(root + "/"):
            return True
    return False


def set_current_test(nodeid: str) -> None:
    """Appelé par le plugin pytest : rattache les incidents au test courant."""
    _STATE["test"] = nodeid or ""


def incident_count() -> int:
    return int(_STATE["incidents"])


def _record(operation: str, path: str, detail: str = "") -> None:
    _STATE["incidents"] += 1
    target = os.environ.get("JARVIS_VALIDATION_INCIDENTS", "")
    frames = []
    # lookup_lines=False : aucune lecture de fichier source pendant un refus.
    stack = traceback.StackSummary.extract(traceback.walk_stack(sys._getframe(1)), limit=14, lookup_lines=False)
    for frame in reversed(stack):
        if "garde_donnees" in frame.filename:
            continue
        frames.append(f"{os.path.basename(frame.filename)}:{frame.lineno} ({frame.name})")
    entry = {
        "horodatage": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "pid": os.getpid(),
        "test": _STATE["test"],
        "operation": operation,
        "chemin": path,
        "detail": detail,
        "pile": frames[-6:],
    }
    if not target:
        return
    try:
        with _LOCK:
            opener = _ORIGINALS.get("open", builtins.open)
            with opener(target, "a", encoding="utf-8") as handle:  # type: ignore[operator]
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _deny(operation: str, path: str, detail: str = "") -> None:
    _record(operation, path, detail)
    raise PermissionError(
        f"[garde de validation] accès refusé aux données réelles ({operation}) : {path}"
    )


def check(path, *, write: bool, operation: str) -> None:
    """Lève ``PermissionError`` si l'opération vise une racine protégée."""
    normalized = _norm(path)
    if normalized is None:
        return
    if _under(normalized, _FORBIDDEN):
        _deny(operation, normalized, "racine interdite (données réelles de Jarvis)")
    if write and _under(normalized, _READ_ONLY):
        _deny(operation, normalized, "racine en lecture seule (copie de travail de l'utilisateur)")


def _mode_writes(mode) -> bool:
    text = str(mode or "r")
    return any(flag in text for flag in ("w", "a", "x", "+"))


def install() -> bool:
    """Installe les crochets (idempotent). Retourne True si la garde est active."""
    global _INSTALLED, _FORBIDDEN, _READ_ONLY
    if _INSTALLED:
        return True
    _FORBIDDEN = _roots("JARVIS_VALIDATION_INTERDIT")
    _READ_ONLY = _roots("JARVIS_VALIDATION_LECTURE_SEULE")
    if not _FORBIDDEN and not _READ_ONLY:
        return False

    original_open = builtins.open
    _ORIGINALS["open"] = original_open

    def guarded_open(file, mode="r", *args, **kwargs):
        check(file, write=_mode_writes(mode), operation=f"open[{mode}]")
        return original_open(file, mode, *args, **kwargs)

    builtins.open = guarded_open
    io.open = guarded_open  # pathlib.Path.open/write_text passent par io.open

    original_os_open = os.open

    def guarded_os_open(path, flags, *args, **kwargs):
        check(path, write=bool(flags & _WRITE_FLAGS), operation="os.open")
        return original_os_open(path, flags, *args, **kwargs)

    os.open = guarded_os_open

    def wrap_write(module, name: str, *, arg_count: int = 1) -> None:
        original = getattr(module, name, None)
        if original is None:
            return

        def guarded(*args, **kwargs):
            for value in args[:arg_count]:
                check(value, write=True, operation=f"{module.__name__}.{name}")
            return original(*args, **kwargs)

        guarded.__name__ = name
        guarded.__doc__ = getattr(original, "__doc__", None)
        setattr(module, name, guarded)

    for name in ("remove", "unlink", "rmdir", "mkdir", "makedirs", "removedirs", "truncate", "chmod", "utime"):
        wrap_write(os, name)
    for name in ("rename", "replace", "link", "symlink"):
        wrap_write(os, name, arg_count=2)
    for name in ("rmtree",):
        wrap_write(shutil, name)
    for name in ("move", "copy", "copy2", "copyfile", "copytree"):
        # Destination (2e argument) : écriture ; source (1er) : lecture.
        original = getattr(shutil, name, None)
        if original is None:
            continue

        def make(original=original, name=name):
            def guarded(src, dst, *args, **kwargs):
                check(src, write=(name == "move"), operation=f"shutil.{name}[source]")
                check(dst, write=True, operation=f"shutil.{name}[destination]")
                return original(src, dst, *args, **kwargs)

            guarded.__name__ = name
            return guarded

        setattr(shutil, name, make())

    try:
        import sqlite3

        original_connect = sqlite3.connect

        def guarded_connect(database, *args, **kwargs):
            if isinstance(database, (str, bytes, os.PathLike)):
                check(database, write=True, operation="sqlite3.connect")
            return original_connect(database, *args, **kwargs)

        sqlite3.connect = guarded_connect
        if getattr(sqlite3, "dbapi2", None) is not None:
            sqlite3.dbapi2.connect = guarded_connect
    except Exception:  # pragma: no cover - sqlite3 absent
        pass

    _INSTALLED = True
    return True


def summary() -> dict:
    return {
        "active": _INSTALLED,
        "interdit": list(_FORBIDDEN),
        "lecture_seule": list(_READ_ONLY),
        "incidents": incident_count(),
        "python": sys.version.split()[0],
    }
