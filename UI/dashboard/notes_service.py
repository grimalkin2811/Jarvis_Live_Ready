"""Notes, documents et tâches personnelles — sans Qt.

* **Notes** : celles de l'outil vocal ``take_note``/``read_notes`` (même
  fichier, mêmes fonctions : ``src.tools``).
* **Documents** : fichiers RÉELLEMENT présents dans ``user_content`` (textes
  du système d'écriture, résultats des tâches d'arrière-plan). Rien n'est
  inventé : un dossier absent donne une liste vide.
* **Tâches personnelles** : le ``TodoManager`` existant (``add_todo``…).

Les appels peuvent toucher le disque : l'interface les exécute hors du
thread graphique (``async_jobs``).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

MAX_DOCUMENTS = 200
MAX_DEPTH = 2


@dataclass(frozen=True)
class DocumentEntry:
    path: str
    name: str
    folder: str
    size: int
    modified: float


def documents_root() -> str:
    from src import paths

    return str(paths.user_content_dir())


def list_documents(root: str | None = None, limit: int = MAX_DOCUMENTS) -> list[DocumentEntry]:
    """Fichiers réels sous ``root`` (profondeur ≤ 2), du plus récent au plus ancien."""
    base = root or documents_root()
    if not base or not os.path.isdir(base):
        return []
    entries: list[DocumentEntry] = []
    base_depth = base.rstrip(os.sep).count(os.sep)
    for current, dirs, files in os.walk(base):
        depth = current.rstrip(os.sep).count(os.sep) - base_depth
        dirs[:] = [d for d in dirs if not d.startswith(".")] if depth < MAX_DEPTH else []
        for name in files:
            if name.startswith(".") or name.endswith((".tmp", ".part")):
                continue
            path = os.path.join(current, name)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            if not os.path.isfile(path):
                continue
            folder = os.path.relpath(current, base)
            entries.append(DocumentEntry(path, name, "" if folder == "." else folder, int(stat.st_size), stat.st_mtime))
    entries.sort(key=lambda e: e.modified, reverse=True)
    return entries[: max(1, int(limit))]


def _tools():
    from src import tools

    return tools


def read_notes(limit: int = 20) -> dict[str, Any]:
    result = _tools().read_notes(limit=limit)
    if not isinstance(result, dict):
        return {"success": False, "error": "Réponse inattendue du module de notes."}
    return result


def add_note(text: str) -> dict[str, Any]:
    content = str(text or "").strip()
    if not content:
        return {"success": False, "error": "La note est vide."}
    if len(content) > 2000:
        return {"success": False, "error": "Note trop longue (2000 caractères maximum)."}
    result = _tools().take_note(content)
    return result if isinstance(result, dict) else {"success": False, "error": "Réponse inattendue."}


class TodoService:
    def __init__(self, manager_getter: Callable[[], Any] | None = None) -> None:
        if manager_getter is None:
            def manager_getter():
                from src.todo import get_default_todo_manager

                return get_default_todo_manager()
        self._manager_getter = manager_getter

    def manager(self):
        return self._manager_getter()

    def list_pending(self, limit: int = 50) -> dict[str, Any]:
        return self.manager().list_tasks(status="pending", limit=max(1, min(100, int(limit))))

    def add(self, label: str, due: str = "", priority: str = "normale") -> dict[str, Any]:
        return self.manager().add_task(str(label or ""), due=str(due or ""), priority=priority)

    def complete(self, task_id: int) -> dict[str, Any]:
        return self.manager().complete_task(task_id=int(task_id))
