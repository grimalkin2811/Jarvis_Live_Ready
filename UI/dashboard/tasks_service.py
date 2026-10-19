"""Couche de service « Tâches d'arrière-plan » du Dashboard — sans Qt.

Façade du ``TaskManager`` v1.8 EXISTANT, qui reste la seule source de vérité
(statut, progression, résultat). Seules les actions réellement supportées
sont exposées : voir le résultat, ouvrir les fichiers produits, annuler une
tâche active, relancer une tâche échouée/annulée. Il n'existe PAS de pause /
reprise dans le Task Manager : le Dashboard n'en affiche donc pas.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

STATUS_LABELS: dict[str, str] = {
    "QUEUED": "En file d'attente",
    "RUNNING": "En cours",
    "WAITING_FOR_TOOL": "En attente d'un outil",
    "COMPLETED": "Terminée",
    "FAILED": "Échouée",
    "CANCELLED": "Annulée",
}
ACTIVE_STATUSES = frozenset({"QUEUED", "RUNNING", "WAITING_FOR_TOOL"})
FINAL_STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED"})


def status_of(task: Any) -> str:
    status = getattr(task, "status", "")
    return str(getattr(status, "value", status) or "")


def supported_actions(task: Any) -> list[str]:
    """Actions réellement possibles pour une tâche (jamais pause/reprise)."""
    status = status_of(task)
    actions: list[str] = []
    if status in ACTIVE_STATUSES:
        actions.append("cancel")
    if status in {"FAILED", "CANCELLED"}:
        actions.append("retry")
    if status == "COMPLETED":
        actions.append("view")
        if [p for p in getattr(task, "files", []) or [] if p]:
            actions.append("open_files")
    elif status == "FAILED":
        actions.append("view")
    return actions


def elapsed_label(task: Any, now: datetime | None = None) -> str:
    source = getattr(task, "finished_at", None) or getattr(task, "started_at", None) or getattr(task, "created_at", None)
    try:
        instant = datetime.fromisoformat(str(source))
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        seconds = max(0, int(((now or datetime.now(timezone.utc)) - instant).total_seconds()))
    except Exception:
        return ""
    if seconds < 60:
        return f"il y a {seconds} s"
    if seconds < 3600:
        return f"il y a {seconds // 60} min"
    if seconds < 86400:
        return f"il y a {seconds // 3600} h"
    return f"il y a {seconds // 86400} j"


class TasksService:
    def __init__(self, manager_getter: Callable[[], Any] | None = None) -> None:
        if manager_getter is None:
            def manager_getter():
                # Jamais d'import de ``src.background_tasks`` depuis le thread
                # graphique : il charge google.genai (~0,4 s). Le module est
                # importé par la boucle vocale qui crée le Task Manager ; tant
                # qu'il ne l'est pas, le gestionnaire n'existe pas non plus.
                module = sys.modules.get("src.background_tasks.manager")
                if module is None:
                    raise RuntimeError("Le gestionnaire de tâches d'arrière-plan n'est pas encore initialisé.")
                return module.get_default_task_manager()
        self._manager_getter = manager_getter

    def manager(self):
        """Le Task Manager courant, ou ``None`` s'il n'est pas (encore) initialisé."""
        try:
            return self._manager_getter()
        except Exception:
            return None

    def available(self) -> bool:
        return self.manager() is not None

    def unavailable_reason(self) -> str:
        try:
            self._manager_getter()
        except Exception as exc:
            return str(exc) or "Gestionnaire de tâches indisponible."
        return ""

    def list_tasks(self) -> list[Any]:
        manager = self.manager()
        if manager is None:
            return []
        try:
            tasks = list(manager.list_tasks())
        except Exception:
            return []
        order = {"RUNNING": 0, "WAITING_FOR_TOOL": 1, "QUEUED": 2}
        return sorted(
            tasks,
            key=lambda t: (order.get(status_of(t), 3), str(getattr(t, "created_at", "") or "")),
            reverse=False,
        )

    def counts(self) -> dict[str, int]:
        counts = {key: 0 for key in STATUS_LABELS}
        for task in self.list_tasks():
            status = status_of(task)
            if status in counts:
                counts[status] += 1
        counts["active"] = sum(counts[s] for s in ACTIVE_STATUSES)
        manager = self.manager()
        try:
            counts["unread"] = len(manager.list_unread_completed_tasks()) if manager else 0
        except Exception:
            counts["unread"] = 0
        return counts

    def get(self, task_id: str) -> Any:
        manager = self.manager()
        return manager.get_task(task_id) if manager is not None else None

    def cancel(self, task_id: str) -> tuple[bool, str]:
        manager = self.manager()
        if manager is None:
            return False, self.unavailable_reason()
        try:
            ok = bool(manager.cancel_task(task_id))
        except Exception as exc:
            return False, str(exc)
        return (True, "Annulation demandée au Task Manager.") if ok else (False, "Cette tâche ne peut plus être annulée.")

    def retry(self, task_id: str) -> tuple[bool, str, Any]:
        manager = self.manager()
        if manager is None:
            return False, self.unavailable_reason(), None
        try:
            task = manager.retry_task(task_id)
        except Exception as exc:
            return False, str(exc) or "Relance impossible.", None
        return True, f"Nouvelle tâche créée : {getattr(task, 'title', '')}", task

    def result(self, task_id: str) -> dict[str, Any] | None:
        manager = self.manager()
        if manager is None:
            return None
        try:
            return manager.get_task_result(task_id, mark_seen=True)
        except Exception:
            return None

    @staticmethod
    def existing_files(task: Any) -> list[str]:
        return [str(p) for p in getattr(task, "files", []) or [] if p and os.path.exists(str(p))]

    def model_status(self) -> dict[str, Any]:
        """Routage réel des tâches (config + découverte + quota), sans invention."""
        manager = self.manager()
        if manager is None:
            return {"available": False, "reason": self.unavailable_reason()}
        info: dict[str, Any] = {"available": True}
        config = getattr(manager, "config", None)
        if config is not None:
            info.update(
                complex_model=getattr(config, "complex_model", ""),
                live_model_hint=getattr(config, "live_model_hint", ""),
                max_concurrent_tasks=getattr(config, "max_concurrent_tasks", None),
            )
        try:
            info["live"] = manager.live_model_status()
        except Exception as exc:
            info["live"] = {"model": None, "error": str(exc)}
        try:
            info["quota"] = manager.quota_status()
        except Exception as exc:
            info["quota"] = None
            info["quota_error"] = str(exc)
        return info
