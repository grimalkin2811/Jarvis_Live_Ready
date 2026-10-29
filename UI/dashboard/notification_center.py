"""Centre de notifications du Dashboard — sans Qt, thread-safe.

Rôle
----
* Reçoit des **faits** classés par catégorie (tâche terminée/échouée,
  intervention requise, réseau, audio, seuil de ressources, mise à jour).
* Applique les préférences par catégorie : affichage DANS le Dashboard,
  notification **native** (zone de notification Windows, via le pont existant
  ``src.notifications`` → ``UI.notification_bridge``), ou les deux.
* Évite les doublons : clé d'évènement (une tâche ne notifie qu'une fois son
  statut final), fenêtre anti-rafale, et — si le Dashboard est au premier plan
  avec le canal « Dashboard » actif — pas de bulle native en plus.
* Fonctionne Dashboard FERMÉ : le centre vit dans le ``DashboardController``
  tant que Jarvis tourne (orbe ou Desktop) ; les notifications natives sont
  alors préfixées « Dashboard — » pour indiquer leur origine.

Le centre ne fabrique jamais un succès : chaque notification est créée par
l'appelant à partir d'un statut réel (Task Manager, moteur, psutil…).
"""

from __future__ import annotations

import itertools
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import notification_title
from .config import NOTIFICATION_CATEGORIES, NotificationPrefs

#: Catégorie technique des notifications Jarvis existantes (rappels, routines)
#: recopiées dans le fil du Dashboard. Jamais renvoyées en natif : le pont
#: existant les a déjà affichées.
CATEGORY_JARVIS = "jarvis"

#: Titre historique publié par le Task Manager à la fin d'une tâche. Le centre
#: gère lui-même cette catégorie (avec préférences) : le relais natif
#: historique est filtré pour ne pas l'afficher deux fois.
LEGACY_TASK_DONE_TITLE = "Tâche terminée"

LEVELS = ("info", "success", "warning", "error")


@dataclass
class DashboardNotification:
    id: int
    category: str
    title: str
    message: str
    level: str = "info"
    created_at: float = field(default_factory=time.time)
    key: str = ""
    read: bool = False
    in_dashboard: bool = True
    native_requested: bool = False
    native_sent: bool = False
    native_error: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "category": self.category, "title": self.title, "message": self.message,
            "level": self.level, "created_at": self.created_at, "read": self.read,
            "native_sent": self.native_sent, "native_error": self.native_error,
        }


NativeSender = Callable[[str, str], Any]
Listener = Callable[[DashboardNotification], None]


class NotificationCenter:
    def __init__(
        self,
        prefs_getter: Callable[[], NotificationPrefs],
        *,
        native_sender: NativeSender | None = None,
        dashboard_foreground: Callable[[], bool] | None = None,
        clock: Callable[[], float] = time.time,
        max_items: int = 200,
        dedup_window: float = 600.0,
    ) -> None:
        self._prefs_getter = prefs_getter
        self._native_sender = native_sender
        self._foreground = dashboard_foreground or (lambda: False)
        self._clock = clock
        self._lock = threading.RLock()
        self._items: deque[DashboardNotification] = deque(maxlen=max(10, int(max_items)))
        self._recent_keys: dict[str, float] = {}
        self._dedup_window = float(dedup_window)
        self._ids = itertools.count(1)
        self._listeners: list[Listener] = []
        self.native_failures = 0
        self.suppressed_duplicates = 0

    # -- abonnements -----------------------------------------------------------

    def add_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        with self._lock:
            if listener in self._listeners:
                self._listeners.remove(listener)

    def set_native_sender(self, sender: NativeSender | None) -> None:
        with self._lock:
            self._native_sender = sender

    def set_foreground_probe(self, probe: Callable[[], bool] | None) -> None:
        with self._lock:
            self._foreground = probe or (lambda: False)

    # -- émission --------------------------------------------------------------

    def _is_duplicate_locked(self, key: str, now: float) -> bool:
        if not key:
            return False
        # Purge paresseuse des clés expirées (borne mémoire).
        if len(self._recent_keys) > 512:
            for stale in [k for k, t in self._recent_keys.items() if now - t > self._dedup_window]:
                self._recent_keys.pop(stale, None)
        last = self._recent_keys.get(key)
        if last is not None and now - last < self._dedup_window:
            return True
        self._recent_keys[key] = now
        return False

    def notify(self, category: str, title: str, message: str, *, level: str = "info",
               key: str = "", data: dict[str, Any] | None = None) -> DashboardNotification | None:
        """Publie une notification. Retourne ``None`` si filtrée (doublon,
        catégorie désactivée sur les deux canaux, catégorie inconnue)."""
        category = str(category or "").strip()
        if category not in NOTIFICATION_CATEGORIES and category != CATEGORY_JARVIS:
            return None
        level = level if level in LEVELS else "info"
        now = self._clock()
        try:
            prefs = self._prefs_getter()
        except Exception:
            prefs = NotificationPrefs()
        if category == CATEGORY_JARVIS:
            in_dashboard, native = True, False
        else:
            in_dashboard = prefs.wants(category, "dashboard")
            native = prefs.wants(category, "native")
        if not in_dashboard and not native:
            return None
        with self._lock:
            if self._is_duplicate_locked(key, now):
                self.suppressed_duplicates += 1
                return None
            notification = DashboardNotification(
                next(self._ids), category, str(title or "")[:120], str(message or "")[:600], level, now,
                key, False, in_dashboard, native, data=dict(data or {}),
            )
            if in_dashboard:
                self._items.append(notification)
            sender = self._native_sender
            listeners = list(self._listeners)
            foreground = self._foreground
        if native:
            try:
                skip_native = bool(in_dashboard and foreground())
            except Exception:
                skip_native = False
            if skip_native:
                notification.native_error = "déjà visible dans le Dashboard (premier plan)"
            elif sender is None:
                notification.native_error = "aucun canal natif disponible"
            else:
                try:
                    sender(notification_title(notification.title), notification.message)
                    notification.native_sent = True
                except Exception as exc:
                    # L'échec du canal natif n'empêche jamais le fil interne.
                    self.native_failures += 1
                    notification.native_error = str(exc)[:200] or exc.__class__.__name__
        for listener in listeners:
            try:
                listener(notification)
            except Exception:
                pass
        return notification

    # -- consultation ----------------------------------------------------------

    def items(self) -> list[DashboardNotification]:
        with self._lock:
            return list(self._items)

    def unread_count(self) -> int:
        with self._lock:
            return sum(1 for item in self._items if not item.read)

    def mark_all_read(self) -> int:
        with self._lock:
            count = 0
            for item in self._items:
                if not item.read:
                    item.read = True
                    count += 1
            listeners = list(self._listeners)
        if count:
            for listener in listeners:
                try:
                    listener(None)  # type: ignore[arg-type]
                except Exception:
                    pass
        return count

    def clear(self) -> int:
        with self._lock:
            count = len(self._items)
            self._items.clear()
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(None)  # type: ignore[arg-type]
            except Exception:
                pass
        return count


# ---------------------------------------------------------------------------
# Traduction des faits réels en notifications
# ---------------------------------------------------------------------------


def notification_for_task(task: Any) -> tuple[str, str, str, str, str] | None:
    """(catégorie, titre, message, niveau, clé) pour un instantané de tâche.

    Seuls les statuts FINAUX réels produisent une notification : COMPLETED
    (succès), FAILED (échec, avec l'erreur réelle). Un statut inconnu ou
    intermédiaire ne produit RIEN — jamais de succès supposé.
    """
    status = getattr(getattr(task, "status", None), "value", getattr(task, "status", None))
    task_id = str(getattr(task, "id", "") or "")
    title = str(getattr(task, "title", "") or "Tâche")
    if not task_id:
        return None
    if status == "COMPLETED":
        return ("task_completed", "Tâche terminée", f"J'ai terminé : {title}", "success",
                f"task:{task_id}:COMPLETED")
    if status == "FAILED":
        error = str(getattr(task, "error", "") or "erreur non précisée")
        from src.runtime_status import redact_secrets

        return ("task_failed", "Tâche échouée", f"{title} — {redact_secrets(error, limit=200)}", "error",
                f"task:{task_id}:FAILED")
    return None
