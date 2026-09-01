"""Journal d'activité local de Jarvis.

Chaque action déclenchée par Gemini (appel d'outil) est enregistrée localement
dans SQLite — ``~/.jarvis/activity.db`` — afin de pouvoir répondre à
« qu'as-tu fait aujourd'hui ? » sans rien envoyer sur Internet.

Le journal est **volontairement sobre** :

* seul le nom de l'outil, un résumé court des arguments, le succès et l'heure
  sont conservés ;
* les outils de lecture pure (heure, journal lui-même…) sont ignorés pour ne
  pas polluer le récit ;
* les entrées plus vieilles que ``JARVIS_ACTIVITY_RETENTION_DAYS`` (30 jours
  par défaut) sont purgées automatiquement ;
* toute erreur est avalée : le journal ne doit jamais casser une action.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
import threading

DEFAULT_DATA_DIR = os.environ.get(
    "JARVIS_DATA_DIR",
    os.path.join(os.path.expanduser("~"), ".jarvis"),
)
DEFAULT_ACTIVITY_DB = os.environ.get(
    "JARVIS_ACTIVITY_DATABASE_PATH",
    os.path.join(DEFAULT_DATA_DIR, "activity.db"),
)

_ISO = "%Y-%m-%dT%H:%M:%S"

try:
    RETENTION_DAYS = max(1, int(os.environ.get("JARVIS_ACTIVITY_RETENTION_DAYS", "30")))
except Exception:  # pragma: no cover
    RETENTION_DAYS = 30

#: Outils trop bavards ou sans intérêt narratif : jamais journalisés.
IGNORED_TOOLS = {
    "get_activity_log",
    "clear_activity_log",
    "get_local_time",
    "get_local_date",
    "get_datetime",
    "get_volume",
    "list_applications",
    "list_websites",
    "list_routine_tools",
    "check_internet",
    "recall",
}

#: Libellés parlés, pour un récit naturel du journal.
LABELS = {
    "open_application": "ouvert l'application",
    "close_application": "ferme l'application",
    "open_website": "ouvert le site",
    "open_url": "ouvert une page",
    "web_search": "cherche sur le Web",
    "set_volume": "regle le volume",
    "take_screenshot": "pris une capture d'ecran",
    "set_reminder": "programme un rappel",
    "set_timer": "lance un minuteur",
    "add_todo": "ajoute une tache",
    "complete_todo": "termine une tache",
    "remember": "memorise une information",
    "run_routine": "lance une routine",
    "show_notification": "affiche une notification",
    "draft_email": "prepare un email",
    "backup_data": "sauvegarde les donnees",
}


def _ok(**payload) -> dict:
    result = {"success": True}
    result.update(payload)
    return result


def _err(message, **payload) -> dict:
    result = {"success": False, "error": str(message)}
    result.update(payload)
    return result


def summarize_arguments(arguments) -> str:
    """Résumé court et lisible des arguments d'un outil."""
    if not isinstance(arguments, dict) or not arguments:
        return ""
    parts = []
    for key, value in list(arguments.items())[:4]:
        if key in {"confirm", "api_key", "key"}:
            continue
        text = str(value)
        if len(text) > 60:
            text = text[:57] + "..."
        parts.append(f"{key}={text}")
    return ", ".join(parts)


class ActivityLog:
    """Journal local des actions (SQLite, thread-safe, tolérant aux pannes)."""

    def __init__(self, database_path=None, enabled: bool = True) -> None:
        self.database_path = str(database_path or DEFAULT_ACTIVITY_DB)
        self.enabled = bool(enabled)
        self.available = False
        self.last_error: str | None = None
        self._lock = threading.RLock()
        if self.enabled:
            self._initialize()

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
                    CREATE TABLE IF NOT EXISTS activity (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        at TEXT NOT NULL,
                        tool TEXT NOT NULL,
                        details TEXT NOT NULL DEFAULT '',
                        success INTEGER NOT NULL DEFAULT 1,
                        error TEXT
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_activity_at ON activity(at)")
            self.available = True
            self.purge_old()
        except Exception as exc:  # pragma: no cover
            self.available = False
            self.last_error = str(exc)

    def _ready(self) -> bool:
        return self.enabled and self.available

    # ------------------------------------------------------------------
    def log(self, tool: str, arguments=None, success: bool = True, error: str = "") -> None:
        """Enregistre une action. Ne lève jamais."""
        if not self._ready():
            return
        name = str(tool or "").strip()
        if not name or name in IGNORED_TOOLS:
            return
        try:
            with self._lock, self._connect() as conn:
                conn.execute(
                    "INSERT INTO activity (at, tool, details, success, error) VALUES (?, ?, ?, ?, ?)",
                    (
                        dt.datetime.now().strftime(_ISO),
                        name,
                        summarize_arguments(arguments),
                        1 if success else 0,
                        (str(error)[:200] if error else None),
                    ),
                )
        except Exception:
            pass

    def purge_old(self, days: int | None = None) -> None:
        if not self._ready():
            return
        horizon = dt.datetime.now() - dt.timedelta(days=int(days or RETENTION_DAYS))
        try:
            with self._lock, self._connect() as conn:
                conn.execute("DELETE FROM activity WHERE at < ?", (horizon.strftime(_ISO),))
        except Exception:
            pass

    # ------------------------------------------------------------------
    def query(self, day: str = "aujourd'hui", limit: int = 30) -> dict:
        """Actions d'une journée : « aujourd'hui », « hier » ou une date."""
        if not self._ready():
            return _err("Journal d'activite indisponible", disponible=False)

        target = self._resolve_day(day)
        if target is None:
            return _err("Date incomprise", hint="Exemples : aujourd'hui, hier, 12/03/2026.")
        try:
            limit = max(1, min(200, int(limit)))
        except Exception:
            limit = 30

        start = dt.datetime.combine(target, dt.time.min).strftime(_ISO)
        end = dt.datetime.combine(target, dt.time.max).strftime(_ISO)
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM activity WHERE at BETWEEN ? AND ? ORDER BY id DESC LIMIT ?",
                    (start, end, limit),
                ).fetchall()
                total = conn.execute(
                    "SELECT COUNT(*) FROM activity WHERE at BETWEEN ? AND ?", (start, end)
                ).fetchone()[0]
                top = conn.execute(
                    "SELECT tool, COUNT(*) AS n FROM activity WHERE at BETWEEN ? AND ? "
                    "GROUP BY tool ORDER BY n DESC LIMIT 5",
                    (start, end),
                ).fetchall()
        except Exception as exc:
            return _err(exc)

        actions = []
        for row in rows:
            entry = {
                "heure": row["at"][11:16],
                "outil": row["tool"],
                "action": LABELS.get(row["tool"], row["tool"].replace("_", " ")),
                "reussi": bool(row["success"]),
            }
            if row["details"]:
                entry["details"] = row["details"]
            if row["error"]:
                entry["erreur"] = row["error"]
            actions.append(entry)

        return _ok(
            jour=target.strftime("%d/%m/%Y"),
            actions=actions,
            count=len(actions),
            total=int(total),
            principaux=[{"outil": r["tool"], "fois": int(r["n"])} for r in top],
            message="Aucune action enregistree ce jour-la." if not actions else None,
        )

    @staticmethod
    def _resolve_day(day) -> dt.date | None:
        text = str(day or "").strip().lower()
        today = dt.date.today()
        if not text or text in {"aujourd'hui", "aujourdhui", "today", "ce jour"}:
            return today
        if text in {"hier", "yesterday"}:
            return today - dt.timedelta(days=1)
        if text in {"avant-hier", "avant hier"}:
            return today - dt.timedelta(days=2)
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y", "%d-%m-%Y"):
            try:
                return dt.datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    def clear(self, confirm: bool = False) -> dict:
        if not self._ready():
            return _err("Journal d'activite indisponible", disponible=False)
        if not confirm:
            return _err("Confirmation requise pour effacer le journal d'activite.")
        try:
            with self._lock, self._connect() as conn:
                cursor = conn.execute("DELETE FROM activity")
                removed = cursor.rowcount if cursor.rowcount is not None else 0
        except Exception as exc:
            return _err(exc)
        return _ok(supprimees=int(removed))

    def export(self, limit: int = 1000) -> list[dict]:
        """Dernières entrées, pour la sauvegarde JSON."""
        if not self._ready():
            return []
        try:
            with self._lock, self._connect() as conn:
                rows = conn.execute(
                    "SELECT * FROM activity ORDER BY id DESC LIMIT ?", (int(limit),)
                ).fetchall()
            return [dict(row) for row in rows]
        except Exception:
            return []


_DEFAULT_LOG: ActivityLog | None = None
_DEFAULT_LOCK = threading.RLock()


def get_default_activity_log() -> ActivityLog:
    global _DEFAULT_LOG
    with _DEFAULT_LOCK:
        if _DEFAULT_LOG is None:
            enabled = os.environ.get("JARVIS_ACTIVITY_ENABLED", "1").strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
                "non",
            }
            _DEFAULT_LOG = ActivityLog(enabled=enabled)
        return _DEFAULT_LOG


def set_default_activity_log(log: ActivityLog | None) -> None:
    global _DEFAULT_LOG
    with _DEFAULT_LOCK:
        _DEFAULT_LOG = log


def log_action(tool: str, arguments=None, success: bool = True, error: str = "") -> None:
    """Raccourci sans risque : journalise une action."""
    try:
        get_default_activity_log().log(tool, arguments, success, error)
    except Exception:
        pass


def dumps(payload) -> str:  # pragma: no cover - utilitaire
    return json.dumps(payload, ensure_ascii=False, indent=2)
