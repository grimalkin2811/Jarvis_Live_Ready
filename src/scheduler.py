"""Rappels persistants et déclenchement horaire des routines.

Contrairement aux minuteurs de ``src.tools`` (threads en mémoire, perdus au
redémarrage), ce module conserve les échéances dans SQLite
(``~/.jarvis/schedule.db``) et les rejoue au démarrage de Jarvis.

Il assure deux missions :

* **Rappels** — ponctuels (« rappelle-moi d'appeler Paul demain à 9h ») ou
  récurrents (``daily``, ``weekdays``, ``weekly``, ``monthly``…).
* **Routines planifiées** — exécution automatique d'une routine à une heure et
  des jours donnés (« mode travail, en semaine à 9h »).

Comme la mémoire, le sous-système est **non bloquant** : si SQLite est
indisponible, Jarvis continue de fonctionner sans rappels.
"""

from __future__ import annotations

import datetime as dt
import os
import sqlite3
import threading

from .timeparse import (
    describe_schedule,
    format_datetime,
    next_occurrence,
    parse_recurrence,
    parse_when,
)

DEFAULT_DATA_DIR = os.environ.get(
    "JARVIS_DATA_DIR",
    os.path.join(os.path.expanduser("~"), ".jarvis"),
)
DEFAULT_SCHEDULE_DB = os.path.join(DEFAULT_DATA_DIR, "schedule.db")

#: Fréquence de vérification des échéances.
TICK_SECONDS = 15
#: Fenêtre pendant laquelle une routine planifiée manquée peut encore partir.
ROUTINE_GRACE_SECONDS = 300
#: Au-delà, un rappel manqué (PC éteint) est signalé comme « en retard ».
LATE_THRESHOLD_SECONDS = 120

_ISO = "%Y-%m-%dT%H:%M:%S"


def _ok(**payload) -> dict:
    result = {"success": True}
    result.update(payload)
    return result


def _err(message: str, **payload) -> dict:
    result = {"success": False, "error": message}
    result.update(payload)
    return result


def _beep() -> None:
    try:
        import winsound

        for _ in range(2):
            winsound.Beep(880, 220)
    except Exception:
        pass


class Scheduler:
    """Planificateur persistant. Un unique thread de fond suffit."""

    def __init__(
        self,
        database_path: str | os.PathLike | None = None,
        enabled: bool = True,
        routine_manager=None,
    ) -> None:
        self.database_path = str(database_path or DEFAULT_SCHEDULE_DB)
        self.enabled = bool(enabled)
        self.available = False
        self.last_error: str | None = None
        self._lock = threading.RLock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._routine_manager = routine_manager
        self._notify_hooks: list = []
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
                    CREATE TABLE IF NOT EXISTS reminders (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        text TEXT NOT NULL,
                        due_at TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        recurrence TEXT NOT NULL DEFAULT '',
                        routine TEXT,
                        status TEXT NOT NULL DEFAULT 'pending',
                        fired_at TEXT
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at)"
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS routine_fires (
                        key TEXT PRIMARY KEY,
                        fired_at TEXT NOT NULL
                    )
                    """
                )
            self.available = True
            self.last_error = None
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)
            print(f"[Scheduler] Rappels indisponibles : {exc}")

    def _ready(self) -> bool:
        return self.enabled and self.available

    def _unavailable(self) -> dict:
        if not self.enabled:
            return _err("Rappels désactivés.")
        return _err(self.last_error or "Rappels indisponibles.")

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    def add_notify_hook(self, hook) -> None:
        """Ajoute un ``hook(titre, message)`` appelé à chaque échéance."""
        with self._lock:
            self._notify_hooks.append(hook)

    def set_routine_manager(self, manager) -> None:
        with self._lock:
            self._routine_manager = manager

    def _routines(self):
        with self._lock:
            manager = self._routine_manager
        if manager is not None:
            return manager
        from .routines import get_default_routine_manager

        return get_default_routine_manager()

    def _notify(self, title: str, message: str) -> None:
        print(f"\n[Jarvis] 🔔 {title} : {message}")
        _beep()
        with self._lock:
            hooks = list(self._notify_hooks)
        for hook in hooks:
            try:
                hook(title, message)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Rappels
    # ------------------------------------------------------------------
    def add_reminder(self, text, when=None, recurrence="", routine=None) -> dict:
        if not self._ready():
            return self._unavailable()

        content = str(text or "").strip()
        moment = parse_when(when) if when else None

        if moment is None:
            from .timeparse import extract_when

            moment, cleaned = extract_when(content)
            if moment is not None and cleaned:
                content = cleaned

        if moment is None:
            return _err(
                "Échéance incomprise. Exemples : « demain à 9h », « dans 20 minutes », « lundi à 8h30 »."
            )

        if not content and not routine:
            return _err("Contenu du rappel manquant.")

        now = dt.datetime.now()
        if moment <= now:
            return _err(
                f"L'échéance ({format_datetime(moment)}) est déjà passée.",
                echeance=moment.strftime(_ISO),
            )
        if moment > now + dt.timedelta(days=730):
            return _err("Échéance trop lointaine (deux ans maximum).")

        normalized_recurrence = parse_recurrence(recurrence)
        routine_name = str(routine).strip() if routine else None

        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "INSERT INTO reminders (text, due_at, created_at, recurrence, routine, status) "
                    "VALUES (?, ?, ?, ?, ?, 'pending')",
                    (
                        content or f"routine {routine_name}",
                        moment.strftime(_ISO),
                        dt.datetime.now().strftime(_ISO),
                        normalized_recurrence,
                        routine_name,
                        ),
                )
                reminder_id = cursor.lastrowid
        except Exception as exc:
            return _err(f"Enregistrement impossible : {exc}")

        return _ok(
            id=reminder_id,
            texte=content,
            echeance=format_datetime(moment),
            echeance_iso=moment.strftime(_ISO),
            recurrence=normalized_recurrence or "aucune",
            routine=routine_name,
        )

    def list_reminders(self, limit=20, include_done=False) -> dict:
        if not self._ready():
            return self._unavailable()
        try:
            limit = max(1, min(100, int(limit or 20)))
        except Exception:
            limit = 20

        statuses = ("pending", "done") if include_done else ("pending",)
        placeholders = ",".join("?" for _ in statuses)
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT * FROM reminders WHERE status IN ({placeholders}) "
                    "ORDER BY due_at ASC LIMIT ?",
                    (*statuses, limit),
                ).fetchall()
        except Exception as exc:
            return _err(f"Lecture impossible : {exc}")

        reminders = []
        for row in rows:
            try:
                moment = dt.datetime.strptime(row["due_at"], _ISO)
                echeance = format_datetime(moment)
            except Exception:
                echeance = row["due_at"]
            reminders.append(
                {
                    "id": row["id"],
                    "texte": row["text"],
                    "echeance": echeance,
                    "echeance_iso": row["due_at"],
                    "recurrence": row["recurrence"] or "aucune",
                    "routine": row["routine"],
                    "statut": row["status"],
                }
            )
        return _ok(rappels=reminders, count=len(reminders))

    def cancel_reminder(self, reminder_id=None, confirm=False) -> dict:
        if not self._ready():
            return self._unavailable()

        if reminder_id is None:
            if not confirm:
                pending = self.list_reminders(limit=100)
                return _err(
                    "Confirmation requise pour annuler tous les rappels.",
                    confirmation_requise=True,
                    count=pending.get("count", 0),
                )
            try:
                with self._connect() as conn:
                    cursor = conn.execute(
                        "UPDATE reminders SET status = 'cancelled' WHERE status = 'pending'"
                    )
                return _ok(annules=cursor.rowcount)
            except Exception as exc:
                return _err(f"Annulation impossible : {exc}")

        try:
            key = int(reminder_id)
        except Exception:
            return _err("Identifiant de rappel invalide.")

        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM reminders WHERE id = ? AND status = 'pending'", (key,)
                ).fetchone()
                if row is None:
                    return _err("Rappel introuvable ou déjà passé.")
                conn.execute("UPDATE reminders SET status = 'cancelled' WHERE id = ?", (key,))
        except Exception as exc:
            return _err(f"Annulation impossible : {exc}")

        return _ok(id=key, texte=row["text"], annule=True)

    # ------------------------------------------------------------------
    # Boucle de fond
    # ------------------------------------------------------------------
    def _fire_reminder(self, row) -> None:
        late = ""
        try:
            due = dt.datetime.strptime(row["due_at"], _ISO)
            delay = (dt.datetime.now() - due).total_seconds()
            if delay > LATE_THRESHOLD_SECONDS:
                late = f" (prévu {format_datetime(due)})"
        except Exception:
            due = dt.datetime.now()

        routine_name = row["routine"]
        if routine_name:
            try:
                result = self._routines().run_routine(routine_name)
                status = "exécutée" if result.get("success") else "partiellement exécutée"
                self._notify("Routine planifiée", f"{routine_name} {status}{late}")
            except Exception as exc:
                self._notify("Routine planifiée", f"{routine_name} a échoué : {exc}")
        else:
            self._notify("Rappel", f"{row['text']}{late}")

        follow_up = next_occurrence(due, row["recurrence"] or "")
        now = dt.datetime.now()
        while follow_up is not None and follow_up <= now:
            follow_up = next_occurrence(follow_up, row["recurrence"] or "")

        try:
            with self._connect() as conn:
                if follow_up is not None:
                    conn.execute(
                        "UPDATE reminders SET due_at = ?, fired_at = ? WHERE id = ?",
                        (follow_up.strftime(_ISO), now.strftime(_ISO), row["id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE reminders SET status = 'done', fired_at = ? WHERE id = ?",
                        (now.strftime(_ISO), row["id"]),
                    )
        except Exception as exc:
            print(f"[Scheduler] Mise à jour du rappel impossible : {exc}")

    def _due_reminders(self) -> list:
        try:
            with self._connect() as conn:
                return conn.execute(
                    "SELECT * FROM reminders WHERE status = 'pending' AND due_at <= ? ORDER BY due_at",
                    (dt.datetime.now().strftime(_ISO),),
                ).fetchall()
        except Exception as exc:
            self.last_error = str(exc)
            return []

    def _claim_routine_fire(self, key: str) -> bool:
        """Marque une exécution planifiée, en évitant tout double déclenchement."""
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO routine_fires (key, fired_at) VALUES (?, ?)",
                    (key, dt.datetime.now().strftime(_ISO)),
                )
                conn.execute(
                    "DELETE FROM routine_fires WHERE fired_at < ?",
                    ((dt.datetime.now() - dt.timedelta(days=7)).strftime(_ISO),),
                )
            return True
        except sqlite3.IntegrityError:
            return False
        except Exception:
            return False

    def _check_scheduled_routines(self) -> None:
        now = dt.datetime.now()
        try:
            routines = self._routines().scheduled_routines()
        except Exception:
            return

        for routine in routines:
            schedule = routine.get("schedule") or {}
            days = schedule.get("days") or []
            if now.weekday() not in days:
                continue
            try:
                hour, minute = (int(part) for part in str(schedule.get("time", "")).split(":"))
            except Exception:
                continue

            target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            elapsed = (now - target).total_seconds()
            if not (0 <= elapsed <= ROUTINE_GRACE_SECONDS):
                continue

            key = f"{routine['name']}|{target.strftime('%Y-%m-%d %H:%M')}"
            if not self._claim_routine_fire(key):
                continue

            try:
                result = self._routines().run_routine(routine["name"])
                status = "exécutée" if result.get("success") else "partiellement exécutée"
            except Exception as exc:
                status = f"en échec ({exc})"
            self._notify("Routine planifiée", f"{routine['name']} {status}")

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                for row in self._due_reminders():
                    self._fire_reminder(row)
                self._check_scheduled_routines()
            except Exception as exc:  # pragma: no cover - robustesse
                print(f"[Scheduler] Tour ignoré : {exc}")
            self._stop.wait(TICK_SECONDS)

    def start(self) -> bool:
        """Démarre le thread de fond (idempotent)."""
        if not self._ready():
            return False
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return True
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="jarvis-scheduler", daemon=True)
            self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)

    def status(self) -> dict:
        pending = self.list_reminders(limit=100)
        try:
            scheduled = self._routines().scheduled_routines()
        except Exception:
            scheduled = []
        return _ok(
            actif=self._thread is not None and self._thread.is_alive(),
            disponible=self.available,
            rappels_en_attente=pending.get("count", 0) if pending.get("success") else 0,
            routines_planifiees=[
                {"nom": item["name"], "quand": describe_schedule(item["schedule"])} for item in scheduled
            ],
            base=self.database_path,
        )


# ---------------------------------------------------------------------------
# Instance par défaut
# ---------------------------------------------------------------------------

_DEFAULT_SCHEDULER: Scheduler | None = None
_DEFAULT_LOCK = threading.Lock()


def get_default_scheduler() -> Scheduler:
    global _DEFAULT_SCHEDULER
    with _DEFAULT_LOCK:
        if _DEFAULT_SCHEDULER is None:
            enabled = os.environ.get("JARVIS_REMINDERS_ENABLED", "1").strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
            path = os.environ.get("JARVIS_SCHEDULE_DATABASE_PATH", DEFAULT_SCHEDULE_DB)
            _DEFAULT_SCHEDULER = Scheduler(path, enabled=enabled)
        return _DEFAULT_SCHEDULER


def set_default_scheduler(scheduler: Scheduler | None) -> None:
    global _DEFAULT_SCHEDULER
    with _DEFAULT_LOCK:
        _DEFAULT_SCHEDULER = scheduler


def start_default_scheduler() -> Scheduler:
    """Démarre le planificateur global (appelé au lancement de Jarvis)."""
    scheduler = get_default_scheduler()
    scheduler.start()
    return scheduler
