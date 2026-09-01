"""Liste de tâches persistante de Jarvis.

Les notes (``src.tools.take_note``) sont un simple journal texte : elles n'ont
pas d'état « fait / à faire ». Ce module ajoute une véritable **todo list**
durable, stockée en SQLite comme la mémoire (``memory.db``) et les rappels
(``schedule.db``) :

``~/.jarvis/todo.db`` (``JARVIS_TODO_DATABASE_PATH`` pour changer le chemin).

Philosophie identique au reste du projet :

* aucune exception ne remonte : toutes les méthodes renvoient un dictionnaire
  ``{"success": bool, ...}`` ;
* si SQLite est indisponible, Jarvis continue de fonctionner sans todo ;
* les suppressions larges exigent une confirmation explicite.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import threading

from .timeparse import format_datetime, parse_when

DEFAULT_DATA_DIR = os.environ.get(
    "JARVIS_DATA_DIR",
    os.path.join(os.path.expanduser("~"), ".jarvis"),
)
DEFAULT_TODO_DB = os.environ.get(
    "JARVIS_TODO_DATABASE_PATH",
    os.path.join(DEFAULT_DATA_DIR, "todo.db"),
)

_ISO = "%Y-%m-%dT%H:%M:%S"

#: Priorités acceptées, de la plus urgente à la plus basse.
PRIORITIES = ("haute", "normale", "basse")

MAX_TASKS = 500


def _ok(**payload) -> dict:
    result = {"success": True}
    result.update(payload)
    return result


def _err(message, **payload) -> dict:
    result = {"success": False, "error": str(message)}
    result.update(payload)
    return result


def _normalize_priority(value) -> str:
    text = str(value or "").strip().lower()
    if text in {"haute", "high", "urgente", "urgent", "1"}:
        return "haute"
    if text in {"basse", "low", "faible", "3"}:
        return "basse"
    return "normale"


class TodoManager:
    """Gestionnaire de tâches (SQLite, thread-safe)."""

    def __init__(self, database_path=None, enabled: bool = True) -> None:
        self.database_path = str(database_path or DEFAULT_TODO_DB)
        self.enabled = bool(enabled)
        self.available = False
        self.last_error: str | None = None
        self._lock = threading.RLock()
        if self.enabled:
            self._initialize()

    # ------------------------------------------------------------------
    # Infrastructure
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        try:
            directory = os.path.dirname(self.database_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS todos (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        label TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending',
                        priority TEXT NOT NULL DEFAULT 'normale',
                        due_at TEXT,
                        created_at TEXT NOT NULL,
                        done_at TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_todos_status ON todos(status)"
                )
            self.available = True
        except Exception as exc:  # pragma: no cover - dépend du système de fichiers
            self.available = False
            self.last_error = str(exc)

    def _unavailable(self) -> dict:
        return _err(
            "Liste de taches indisponible" + (f" ({self.last_error})" if self.last_error else ""),
            disponible=False,
        )

    def _ready(self) -> bool:
        return self.enabled and self.available

    # ------------------------------------------------------------------
    # Lecture / écriture
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_task(row) -> dict:
        due = row["due_at"]
        task = {
            "id": row["id"],
            "tache": row["label"],
            "statut": row["status"],
            "priorite": row["priority"],
            "cree_le": row["created_at"],
        }
        if due:
            task["echeance"] = due
            try:
                task["echeance_texte"] = format_datetime(dt.datetime.strptime(due, _ISO))
            except Exception:
                task["echeance_texte"] = due
        if row["done_at"]:
            task["fait_le"] = row["done_at"]
        return task

    def add_task(self, label: str, due: str = "", priority: str = "normale") -> dict:
        if not self._ready():
            return self._unavailable()
        text = str(label or "").strip()
        if not text:
            return _err("Tache vide")
        if len(text) > 300:
            text = text[:300]

        due_at = None
        due_text = str(due or "").strip()
        if due_text:
            moment = parse_when(due_text)
            if moment is None:
                return _err(
                    "Echeance incomprise",
                    hint="Exemples : 'demain a 9h', 'vendredi', 'dans 2 jours', '12/03/2026 a 14h'.",
                )
            due_at = moment.strftime(_ISO)

        now = dt.datetime.now().strftime(_ISO)
        try:
            with self._lock, self._connect() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM todos WHERE status = 'pending'"
                ).fetchone()[0]
                if count >= MAX_TASKS:
                    return _err(f"Trop de taches en attente (max {MAX_TASKS}).")
                cursor = conn.execute(
                    "INSERT INTO todos (label, status, priority, due_at, created_at) "
                    "VALUES (?, 'pending', ?, ?, ?)",
                    (text, _normalize_priority(priority), due_at, now),
                )
                task_id = int(cursor.lastrowid)
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM todos WHERE status = 'pending'"
                ).fetchone()[0]
        except Exception as exc:
            return _err(exc)

        payload = {"id": task_id, "tache": text, "priorite": _normalize_priority(priority)}
        if due_at:
            payload["echeance"] = due_at
            try:
                payload["echeance_texte"] = format_datetime(dt.datetime.strptime(due_at, _ISO))
            except Exception:
                pass
        payload["restantes"] = int(remaining)
        return _ok(**payload)

    def list_tasks(self, status: str = "pending", limit: int = 20) -> dict:
        if not self._ready():
            return self._unavailable()
        wanted = str(status or "pending").strip().lower()
        if wanted in {"fait", "faites", "done", "terminee", "terminees", "completed"}:
            wanted = "done"
        elif wanted in {"tout", "toutes", "all", "*"}:
            wanted = "all"
        else:
            wanted = "pending"
        try:
            limit = max(1, min(100, int(limit)))
        except Exception:
            limit = 20

        query = (
            "SELECT * FROM todos "
            + ("" if wanted == "all" else "WHERE status = ? ")
            + "ORDER BY CASE status WHEN 'pending' THEN 0 ELSE 1 END, "
            "CASE priority WHEN 'haute' THEN 0 WHEN 'normale' THEN 1 ELSE 2 END, "
            "CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at, id "
            "LIMIT ?"
        )
        params = (limit,) if wanted == "all" else (wanted, limit)
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(query, params).fetchall()
                pending = conn.execute(
                    "SELECT COUNT(*) FROM todos WHERE status = 'pending'"
                ).fetchone()[0]
                done = conn.execute(
                    "SELECT COUNT(*) FROM todos WHERE status = 'done'"
                ).fetchone()[0]
        except Exception as exc:
            return _err(exc)

        tasks = [self._row_to_task(row) for row in rows]
        return _ok(
            taches=tasks,
            count=len(tasks),
            restantes=int(pending),
            faites=int(done),
            filtre=wanted,
            message="Aucune tache." if not tasks else None,
        )

    def _find_task(self, conn, task_id=None, label: str = "", status: str | None = None):
        if task_id is not None:
            try:
                return conn.execute(
                    "SELECT * FROM todos WHERE id = ?", (int(task_id),)
                ).fetchone()
            except Exception:
                return None
        needle = str(label or "").strip().lower()
        if not needle:
            return None
        clause = "" if status is None else f"AND status = '{status}' "
        rows = conn.execute(
            f"SELECT * FROM todos WHERE LOWER(label) LIKE ? {clause}ORDER BY id DESC",
            (f"%{needle}%",),
        ).fetchall()
        return rows[0] if rows else None

    def complete_task(self, task_id=None, label: str = "") -> dict:
        if not self._ready():
            return self._unavailable()
        now = dt.datetime.now().strftime(_ISO)
        try:
            with self._lock, self._connect() as conn:
                row = self._find_task(conn, task_id, label, status="pending")
                if row is None:
                    row = self._find_task(conn, task_id, label)
                if row is None:
                    return _err("Tache introuvable", hint="Utilise list_todos pour voir les taches.")
                if row["status"] == "done":
                    return _ok(id=row["id"], tache=row["label"], deja_faite=True)
                conn.execute(
                    "UPDATE todos SET status = 'done', done_at = ? WHERE id = ?",
                    (now, row["id"]),
                )
                remaining = conn.execute(
                    "SELECT COUNT(*) FROM todos WHERE status = 'pending'"
                ).fetchone()[0]
        except Exception as exc:
            return _err(exc)
        return _ok(id=row["id"], tache=row["label"], statut="done", restantes=int(remaining))

    def reopen_task(self, task_id=None, label: str = "") -> dict:
        if not self._ready():
            return self._unavailable()
        try:
            with self._lock, self._connect() as conn:
                row = self._find_task(conn, task_id, label)
                if row is None:
                    return _err("Tache introuvable")
                conn.execute(
                    "UPDATE todos SET status = 'pending', done_at = NULL WHERE id = ?",
                    (row["id"],),
                )
        except Exception as exc:
            return _err(exc)
        return _ok(id=row["id"], tache=row["label"], statut="pending")

    def delete_task(self, task_id=None, label: str = "") -> dict:
        if not self._ready():
            return self._unavailable()
        try:
            with self._lock, self._connect() as conn:
                row = self._find_task(conn, task_id, label)
                if row is None:
                    return _err("Tache introuvable")
                conn.execute("DELETE FROM todos WHERE id = ?", (row["id"],))
        except Exception as exc:
            return _err(exc)
        return _ok(id=row["id"], tache=row["label"], action="supprimee")

    def clear_tasks(self, confirm: bool = False, only_done: bool = False) -> dict:
        if not self._ready():
            return self._unavailable()
        if not confirm and not only_done:
            return _err(
                "Confirmation requise : rappelle l'outil avec confirm=true apres accord explicite."
            )
        try:
            with self._lock, self._connect() as conn:
                if only_done:
                    cursor = conn.execute("DELETE FROM todos WHERE status = 'done'")
                else:
                    cursor = conn.execute("DELETE FROM todos")
                removed = cursor.rowcount if cursor.rowcount is not None else 0
        except Exception as exc:
            return _err(exc)
        return _ok(supprimees=int(removed), seulement_faites=bool(only_done))

    def export(self) -> list[dict]:
        """Toutes les tâches, pour la sauvegarde JSON."""
        if not self._ready():
            return []
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute("SELECT * FROM todos ORDER BY id").fetchall()
            return [self._row_to_task(row) for row in rows]
        except Exception:
            return []


# ---------------------------------------------------------------------------
# Gestionnaire par défaut (partagé par la boîte à outils)
# ---------------------------------------------------------------------------

_DEFAULT_MANAGER: TodoManager | None = None
_DEFAULT_LOCK = threading.RLock()


def get_default_todo_manager() -> TodoManager:
    global _DEFAULT_MANAGER
    with _DEFAULT_LOCK:
        if _DEFAULT_MANAGER is None:
            _DEFAULT_MANAGER = TodoManager()
        return _DEFAULT_MANAGER


def set_default_todo_manager(manager: TodoManager | None) -> None:
    global _DEFAULT_MANAGER
    with _DEFAULT_LOCK:
        _DEFAULT_MANAGER = manager
