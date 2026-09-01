"""Sauvegarde et export des données locales de Jarvis.

Un seul fichier JSON lisible rassemble tout ce que Jarvis a appris et retenu :

* la mémoire durable (``memory.db``) ;
* les routines (``routines.json``) ;
* la liste de tâches (``todo.db``) ;
* les rappels en attente (``schedule.db``) ;
* les notes (``notes.json``) ;
* un extrait du journal d'activité (``activity.db``).

Par défaut les sauvegardes vont dans ``~/.jarvis/backups/``. Rien n'est envoyé
sur Internet : c'est un simple export local, facile à copier sur une clé USB.
"""

from __future__ import annotations

import datetime as dt
import json
import os

DEFAULT_DATA_DIR = os.environ.get(
    "JARVIS_DATA_DIR",
    os.path.join(os.path.expanduser("~"), ".jarvis"),
)
DEFAULT_BACKUP_DIR = os.environ.get(
    "JARVIS_BACKUP_DIR",
    os.path.join(DEFAULT_DATA_DIR, "backups"),
)

BACKUP_VERSION = 1
MAX_BACKUPS_LISTED = 20


def _ok(**payload) -> dict:
    result = {"success": True}
    result.update(payload)
    return result


def _err(message, **payload) -> dict:
    result = {"success": False, "error": str(message)}
    result.update(payload)
    return result


def collect_snapshot() -> dict:
    """Rassemble l'état courant de Jarvis (jamais bloquant)."""
    snapshot: dict = {
        "version": BACKUP_VERSION,
        "genere_le": dt.datetime.now().isoformat(timespec="seconds"),
        "machine": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "",
    }

    # --- Mémoire ---------------------------------------------------------
    try:
        from .memory import get_default_memory_manager

        result = get_default_memory_manager().list_memories(limit=500)
        snapshot["memoire"] = result.get("memories", [])
    except Exception as exc:
        snapshot["memoire"] = []
        snapshot.setdefault("erreurs", []).append(f"memoire: {exc}")

    # --- Routines --------------------------------------------------------
    try:
        from .routines import DEFAULT_ROUTINES_PATH, get_default_routine_manager

        manager = get_default_routine_manager()
        path = getattr(manager, "path", DEFAULT_ROUTINES_PATH)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                snapshot["routines"] = json.load(handle)
        else:
            snapshot["routines"] = {"version": 1, "routines": []}
    except Exception as exc:
        snapshot["routines"] = {"version": 1, "routines": []}
        snapshot.setdefault("erreurs", []).append(f"routines: {exc}")

    # --- Tâches ----------------------------------------------------------
    try:
        from .todo import get_default_todo_manager

        snapshot["taches"] = get_default_todo_manager().export()
    except Exception as exc:
        snapshot["taches"] = []
        snapshot.setdefault("erreurs", []).append(f"taches: {exc}")

    # --- Rappels ---------------------------------------------------------
    try:
        from .scheduler import get_default_scheduler

        result = get_default_scheduler().list_reminders(limit=100)
        snapshot["rappels"] = result.get("rappels") or result.get("reminders") or []
    except Exception as exc:
        snapshot["rappels"] = []
        snapshot.setdefault("erreurs", []).append(f"rappels: {exc}")

    # --- Notes -----------------------------------------------------------
    try:
        notes_file = os.path.join(DEFAULT_DATA_DIR, "notes.json")
        if os.path.exists(notes_file):
            with open(notes_file, "r", encoding="utf-8") as handle:
                snapshot["notes"] = json.load(handle)
        else:
            snapshot["notes"] = []
    except Exception as exc:
        snapshot["notes"] = []
        snapshot.setdefault("erreurs", []).append(f"notes: {exc}")

    # --- Journal d'activité ---------------------------------------------
    try:
        from .activity import get_default_activity_log

        snapshot["journal"] = get_default_activity_log().export(limit=500)
    except Exception as exc:
        snapshot["journal"] = []
        snapshot.setdefault("erreurs", []).append(f"journal: {exc}")

    return snapshot


def create_backup(destination: str = "") -> dict:
    """Écrit une sauvegarde JSON et renvoie son chemin."""
    snapshot = collect_snapshot()

    target = str(destination or "").strip()
    if target:
        target = os.path.expanduser(target)
        if os.path.isdir(target):
            target = os.path.join(target, _default_filename())
    else:
        target = os.path.join(DEFAULT_BACKUP_DIR, _default_filename())

    if not target.lower().endswith(".json"):
        target += ".json"

    try:
        directory = os.path.dirname(target)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(target, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle, ensure_ascii=False, indent=2)
        size = os.path.getsize(target)
    except Exception as exc:
        return _err(f"Sauvegarde impossible : {exc}")

    return _ok(
        fichier=target,
        taille_ko=round(size / 1024, 1),
        souvenirs=len(snapshot.get("memoire", [])),
        routines=len((snapshot.get("routines") or {}).get("routines", [])),
        taches=len(snapshot.get("taches", [])),
        rappels=len(snapshot.get("rappels", [])),
        notes=len(snapshot.get("notes", [])),
    )


def _default_filename() -> str:
    return dt.datetime.now().strftime("jarvis_backup_%Y%m%d_%H%M%S.json")


def list_backups(folder: str = "") -> dict:
    """Liste les sauvegardes déjà réalisées, de la plus récente à la plus ancienne."""
    directory = os.path.expanduser(str(folder or "").strip() or DEFAULT_BACKUP_DIR)
    if not os.path.isdir(directory):
        return _ok(dossier=directory, sauvegardes=[], count=0, message="Aucune sauvegarde pour l'instant.")
    try:
        entries = []
        for name in os.listdir(directory):
            if not name.lower().endswith(".json"):
                continue
            path = os.path.join(directory, name)
            try:
                stat = os.stat(path)
            except Exception:
                continue
            entries.append(
                {
                    "fichier": name,
                    "chemin": path,
                    "date": dt.datetime.fromtimestamp(stat.st_mtime).strftime("%d/%m/%Y %H:%M"),
                    "taille_ko": round(stat.st_size / 1024, 1),
                    "_mtime": stat.st_mtime,
                }
            )
    except Exception as exc:
        return _err(exc)

    entries.sort(key=lambda item: item["_mtime"], reverse=True)
    for entry in entries:
        entry.pop("_mtime", None)
    return _ok(
        dossier=directory,
        sauvegardes=entries[:MAX_BACKUPS_LISTED],
        count=len(entries),
        message="Aucune sauvegarde pour l'instant." if not entries else None,
    )
