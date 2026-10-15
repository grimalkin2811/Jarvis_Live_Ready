"""État réactif partagé du Dashboard (thread Qt).

Le hub est le **seul** point de contact entre les systèmes de Jarvis et les
widgets. Il ne duplique aucun état métier : il relaie des faits (signaux Qt)
et donne accès aux services (qui interrogent les vraies sources). Plusieurs
widgets affichant la même donnée — sur une ou plusieurs pages — lisent donc
la même source et reçoivent les mêmes signaux.

Sources branchées (toutes en « push », aucun sondage coûteux) :

* ``src.runtime_status.RUNTIME`` (état moteur publié par la boucle vocale) ;
* ``ConversationContext`` (écouteurs de changement) ;
* ``TaskManager`` (hooks, instantanés clonés) ;
* évènements vocaux réels relayés par le contrôleur (présence, outils,
  transcriptions, niveaux audio — jamais de seconde capture audio) ;
* ``src.notifications`` (rappels/routines recopiés dans le fil) ;
* journal applicatif (``log_buffer``).

Le hub vit tant que Jarvis tourne : les notifications fonctionnent donc
Dashboard fermé. Il est léger (aucun minuteur actif hors attente du Task
Manager au démarrage).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot

from src.runtime_status import RUNTIME, EngineState

from .async_jobs import JobRunner
from .config import ConfigStore
from .log_buffer import LogRingHandler, attach as attach_log, detach as detach_log
from .memory_service import MemoryService
from .notes_service import TodoService
from .notification_center import (
    CATEGORY_JARVIS,
    LEGACY_TASK_DONE_TITLE,
    NotificationCenter,
    notification_for_task,
)
from .resources import ResourceMonitor, threshold_breaches
from .security import SecurityPolicy
from .tasks_service import TasksService

#: Outils vocaux qui modifient la mémoire : leur fin déclenche un
#: rafraîchissement des vues mémoire (pas de sondage de la base).
MEMORY_TOOLS = frozenset(
    {"remember", "forget", "update_memory", "delete_memory", "clear_memory", "recall_and_update"}
)
TODO_TOOLS = frozenset({"add_todo", "complete_todo", "delete_todo", "clear_todos", "reopen_todo"})
NOTES_TOOLS = frozenset({"take_note", "delete_notes"})


class DashboardHub(QObject):
    # Relais internes (émis depuis n'importe quel thread).
    _engine_relay = Signal(object)
    _conversation_relay = Signal(object)
    _task_relay = Signal(object)
    _notification_relay = Signal(object)
    _log_relay = Signal(object)
    _external_notification_relay = Signal(str, str)
    _resource_relay = Signal(object)

    # Signaux publics (toujours dans le thread Qt).
    engine_changed = Signal(object)
    conversation_changed = Signal(object)
    task_updated = Signal(object)
    tasks_attached = Signal(bool)
    notification_added = Signal(object)
    notifications_changed = Signal()
    log_entry = Signal(object)
    presence_changed = Signal(str)
    voice_event = Signal(str, object)
    level_changed = Signal(float, str)
    interface_mode_changed = Signal(str)
    memory_changed = Signal()
    todo_changed = Signal()
    notes_changed = Signal()
    resource_sample = Signal(object)

    def __init__(self, store: ConfigStore, *, parent: QObject | None = None,
                 conversation_getter: Callable[[], Any] | None = None,
                 tasks_service: TasksService | None = None,
                 memory_service: MemoryService | None = None,
                 todo_service: TodoService | None = None,
                 resource_monitor: ResourceMonitor | None = None,
                 runtime=RUNTIME) -> None:
        super().__init__(parent)
        self.store = store
        self.runtime = runtime
        self.jobs = JobRunner(parent=self)
        self.tasks = tasks_service or TasksService()
        self.memory = memory_service or MemoryService()
        self.todo = todo_service or TodoService()
        self.resources = resource_monitor or ResourceMonitor(interval=store.config.monitoring.interval_s)
        self.security = SecurityPolicy.default(store.config.security.confirm)
        self.logs = LogRingHandler()
        self.notifications = NotificationCenter(lambda: self.store.config.notifications)
        if conversation_getter is None:
            def conversation_getter():
                from src.conversation import get_default_conversation_context

                return get_default_conversation_context()
        self._conversation_getter = conversation_getter
        self._conversation = None
        self._task_manager = None
        self._task_hook = None
        self._presence = "hidden"
        self._interface_mode = ""
        self._engine = runtime.snapshot()
        self._network_incident = 0
        self._last_engine_state = self._engine.get("engine_state")
        self._audio_alerted = False
        self._wake_alerted = False
        self._started = False
        self._background_monitoring = False
        self._resource_keys: set[str] = set()
        self._resource_subscribed = False

        self._engine_relay.connect(self._on_engine, Qt.QueuedConnection)
        self._conversation_relay.connect(self._on_conversation, Qt.QueuedConnection)
        self._task_relay.connect(self._on_task, Qt.QueuedConnection)
        self._notification_relay.connect(self._on_notification, Qt.QueuedConnection)
        self._log_relay.connect(self.log_entry.emit, Qt.QueuedConnection)
        self._external_notification_relay.connect(self._on_external_notification, Qt.QueuedConnection)
        self._resource_relay.connect(self._on_resource_sample, Qt.QueuedConnection)

        self._tasks_timer = QTimer(self)
        self._tasks_timer.setInterval(2000)
        self._tasks_timer.timeout.connect(self._attach_tasks)

        # Les hooks backend émettent les relais (thread-safe par construction).
        self._runtime_hook = self._engine_relay.emit
        self._conversation_hook = self._conversation_relay.emit
        self._center_hook = self._notification_relay.emit
        self._log_hook = self._log_relay.emit
        self._external_hook = self._external_notification_relay.emit
        self._resource_hook = self._resource_relay.emit

    # ------------------------------------------------------------------
    # Cycle de vie
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.runtime.add_hook(self._runtime_hook)
        self.notifications.add_listener(self._center_hook)
        attach_log(self.logs)
        self.logs.add_listener(self._log_hook)
        try:
            from src import notifications as backend_notifications

            backend_notifications.add_hook(self._external_hook)
        except Exception:
            pass
        self._attach_conversation()
        self._attach_tasks()
        if self._task_manager is None:
            self._tasks_timer.start()
        self.apply_monitoring_prefs()
        self._engine = self.runtime.snapshot()

    def shutdown(self) -> None:
        if not self._started:
            return
        self._started = False
        self._tasks_timer.stop()
        self.runtime.remove_hook(self._runtime_hook)
        self.notifications.remove_listener(self._center_hook)
        self.logs.remove_listener(self._log_hook)
        detach_log(self.logs)
        try:
            from src import notifications as backend_notifications

            backend_notifications.remove_hook(self._external_hook)
        except Exception:
            pass
        if self._conversation is not None:
            try:
                self._conversation.remove_listener(self._conversation_hook)
            except Exception:
                pass
            self._conversation = None
        self._detach_tasks()
        self._resource_keys.clear()
        self._background_monitoring = False
        self._sync_resource_subscription()
        self.resources.stop()
        self.jobs.shutdown()

    @property
    def started(self) -> bool:
        return self._started

    # ------------------------------------------------------------------
    # Moteur / présence / évènements vocaux
    # ------------------------------------------------------------------

    def engine(self) -> dict[str, Any]:
        return dict(self._engine)

    def presence(self) -> str:
        return self._presence

    def interface_mode(self) -> str:
        return self._interface_mode

    @Slot(object)
    def _on_engine(self, snapshot: dict[str, Any]) -> None:
        previous = self._last_engine_state
        self._engine = dict(snapshot)
        state = snapshot.get("engine_state")
        self._last_engine_state = state
        if state != previous:
            self.logs.record_event(
                f"Moteur : {snapshot.get('engine_label', state)}"
                + (f" — {snapshot.get('engine_detail')}" if snapshot.get("engine_detail") else ""),
                level=logging.WARNING if state in (EngineState.RECONNECTING, EngineState.AUTH_ERROR,
                                                   EngineState.FAILED) else logging.INFO,
                source="moteur", category="engine",
            )
            if state == EngineState.RECONNECTING and previous == EngineState.READY:
                self._network_incident += 1
                self.notifications.notify(
                    "network", "Connexion au modèle perdue",
                    "Jarvis tente de se reconnecter automatiquement.", level="warning",
                    key=f"network:{self._network_incident}",
                )
            elif state == EngineState.AUTH_ERROR:
                self.notifications.notify(
                    "intervention", "Clé API refusée",
                    "La conversation est arrêtée : vérifie GEMINI_API_KEY dans le fichier .env.",
                    level="error", key="intervention:auth",
                )
            elif state == EngineState.FAILED:
                self.notifications.notify(
                    "intervention", "Backend vocal indisponible",
                    snapshot.get("engine_detail") or "Le moteur vocal n'a pas pu démarrer.",
                    level="error", key=f"intervention:failed:{snapshot.get('engine_detail', '')}",
                )
        if snapshot.get("audio_state") == "failed" and not self._audio_alerted:
            self._audio_alerted = True
            self.notifications.notify(
                "audio", "Problème audio",
                snapshot.get("audio_detail") or "Le micro ou les haut-parleurs n'ont pas pu démarrer.",
                level="error", key="audio:failed",
            )
        if snapshot.get("wake_word") == "unavailable" and not self._wake_alerted:
            self._wake_alerted = True
            self.notifications.notify(
                "audio", "Mot d'éveil indisponible",
                "« Hey Jarvis » ne peut pas être détecté : utilise l'écoute continue.",
                level="warning", key="audio:wakeword",
            )
        self.engine_changed.emit(dict(snapshot))

    def handle_presence(self, state: str) -> None:
        """Entrée Qt (déjà dans le thread Qt via le contrôleur)."""
        self._presence = str(state or "hidden")
        self.presence_changed.emit(self._presence)

    def handle_voice_event(self, event: str, payload: dict[str, Any]) -> None:
        name = str(event or "")
        data = dict(payload or {})
        if name == "level":
            try:
                self.level_changed.emit(float(data.get("value", 0.0)), str(data.get("source", "input")))
            except (TypeError, ValueError):
                pass
            return
        if name == "tool_end":
            tool = str(data.get("name") or "")
            self.logs.record_event(
                f"Outil {tool} : {'réussi' if data.get('success', True) else 'échec'}",
                level=logging.INFO if data.get("success", True) else logging.WARNING,
                source="outil", category="tools",
            )
            if tool in MEMORY_TOOLS:
                self.memory_changed.emit()
            if tool in TODO_TOOLS:
                self.todo_changed.emit()
            if tool in NOTES_TOOLS:
                self.notes_changed.emit()
        elif name == "error":
            self.logs.record_event(f"Erreur vocale : {data.get('reason') or 'inconnue'}", level=logging.WARNING,
                                   source="voix", category="engine")
        self.voice_event.emit(name, data)

    def handle_interface_mode(self, mode: str) -> None:
        self._interface_mode = str(mode or "")
        self.logs.record_event(f"Interface active : {self._interface_mode}", source="interface", category="ui")
        self.interface_mode_changed.emit(self._interface_mode)

    # ------------------------------------------------------------------
    # Conversation
    # ------------------------------------------------------------------

    def conversation(self):
        if self._conversation is None:
            self._attach_conversation()
        return self._conversation

    def _attach_conversation(self) -> None:
        try:
            context = self._conversation_getter()
        except Exception:
            context = None
        if context is self._conversation:
            return
        if self._conversation is not None:
            try:
                self._conversation.remove_listener(self._conversation_hook)
            except Exception:
                pass
        self._conversation = context
        if context is not None and hasattr(context, "add_listener"):
            context.add_listener(self._conversation_hook)

    @Slot(object)
    def _on_conversation(self, event: dict[str, Any]) -> None:
        self.conversation_changed.emit(dict(event or {}))

    # ------------------------------------------------------------------
    # Tâches
    # ------------------------------------------------------------------

    def tasks_available(self) -> bool:
        return self._task_manager is not None

    @Slot()
    def _attach_tasks(self) -> None:
        manager = self.tasks.manager()
        if manager is self._task_manager and manager is not None:
            self._tasks_timer.stop()
            return
        if manager is None:
            return
        self._detach_tasks()
        self._task_manager = manager
        self._task_hook = self._task_relay.emit
        try:
            manager.add_hook(self._task_hook)
        except Exception:
            self._task_manager = None
            return
        self._tasks_timer.stop()
        self.tasks_attached.emit(True)
        self.task_updated.emit(None)

    def _detach_tasks(self) -> None:
        if self._task_manager is not None and self._task_hook is not None:
            try:
                self._task_manager.remove_hook(self._task_hook)
            except Exception:
                pass
        self._task_manager = None
        self._task_hook = None

    @Slot(object)
    def _on_task(self, task: Any) -> None:
        status = getattr(getattr(task, "status", None), "value", "")
        if task is not None:
            self.logs.record_event(
                f"Tâche « {getattr(task, 'title', '?')} » : {status}",
                level=logging.WARNING if status == "FAILED" else logging.INFO,
                source="tâches", category="tasks",
            )
            mapped = notification_for_task(task)
            if mapped is not None:
                category, title, message, level, key = mapped
                self.notifications.notify(category, title, message, level=level, key=key,
                                          data={"task_id": getattr(task, "id", "")})
        self.task_updated.emit(task)

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    @Slot(object)
    def _on_notification(self, notification: Any) -> None:
        if notification is None:
            self.notifications_changed.emit()
            return
        self.notification_added.emit(notification)
        self.notifications_changed.emit()

    @Slot(str, str)
    def _on_external_notification(self, title: str, message: str) -> None:
        from . import DASHBOARD_NAME

        if title.startswith(f"{DASHBOARD_NAME} — ") or title == LEGACY_TASK_DONE_TITLE:
            # Notre propre bulle native, ou fin de tâche déjà gérée par le
            # centre depuis le Task Manager : jamais en double dans le fil.
            return
        self.notifications.notify(CATEGORY_JARVIS, title, message, level="info")

    # ------------------------------------------------------------------
    # Ressources
    # ------------------------------------------------------------------

    def apply_monitoring_prefs(self) -> None:
        prefs = self.store.config.monitoring
        self.resources.set_interval(prefs.interval_s)
        self._background_monitoring = bool(prefs.enabled and prefs.background)
        self._sync_resource_subscription()

    def subscribe_resources(self, key: str) -> bool:
        """Un widget visible demande des mesures. False si surveillance désactivée."""
        self._resource_keys.add(str(key))
        self._sync_resource_subscription()
        return bool(self.store.config.monitoring.enabled)

    def unsubscribe_resources(self, key: str) -> None:
        self._resource_keys.discard(str(key))
        self._sync_resource_subscription()

    def resource_consumers(self) -> int:
        return len(self._resource_keys) + (1 if self._background_monitoring else 0)

    def _sync_resource_subscription(self) -> None:
        enabled = bool(self.store.config.monitoring.enabled)
        want = enabled and (bool(self._resource_keys) or self._background_monitoring)
        if want and not self._resource_subscribed:
            self.resources.subscribe("__hub__", self._resource_hook)
            self._resource_subscribed = True
        elif not want and self._resource_subscribed:
            self.resources.unsubscribe("__hub__")
            self._resource_subscribed = False

    @Slot(object)
    def _on_resource_sample(self, sample: Any) -> None:
        prefs = self.store.config.monitoring
        if prefs.enabled:
            for metric, message in threshold_breaches(sample, prefs.cpu_threshold, prefs.ram_threshold,
                                                      prefs.disk_threshold):
                self.notifications.notify("resources", "Seuil de ressources", message, level="warning",
                                          key=f"resources:{metric}")
        self.resource_sample.emit(sample)
