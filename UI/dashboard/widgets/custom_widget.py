"""« Panneau personnalisé » : mécanisme de widget personnalisé SANS plugin.

L'utilisateur compose un panneau à partir d'une liste blanche d'indicateurs
réels (``METRICS``) + un titre et une note libres. Aucun code arbitraire,
aucune donnée inventée : chaque indicateur lit une source existante du hub.
Plusieurs instances, aux réglages indépendants, peuvent coexister.

Ajouter un indicateur = ajouter une entrée à ``METRICS`` (clé, libellé,
fonction de lecture) : le réglage correspondant apparaît automatiquement.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtWidgets import QGridLayout, QVBoxLayout

from src.runtime_status import HEALTH_LABELS

from ..widget_api import STATE_EMPTY, STATE_READY, DashboardWidget, SettingField
from .common import StatusDot, label


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    read: Callable[["CustomPanelWidget"], tuple[str, str]]  # (texte, statut)
    needs: str = ""  # "resources" | "memory" | ""


def _engine(w):
    snap = w.context.hub.engine()
    return snap.get("engine_label", "?"), {"ready": "ok"}.get(snap.get("engine_state"), "warning")


def _health(w):
    health = w.context.hub.engine().get("health", "unknown")
    return HEALTH_LABELS.get(health, "Inconnu"), {"operational": "ok", "degraded": "warning",
                                                  "unavailable": "error"}.get(health, "unknown")


def _presence(w):
    from .jarvis_widgets import PRESENCE_LABELS

    presence = w.context.hub.presence()
    return PRESENCE_LABELS.get(presence, presence), "ok" if presence in ("listening", "speaking") else "unknown"


def _interface(w):
    mode = w.context.hub.interface_mode()
    return {"blob": "Orbe", "desktop": "Desktop"}.get(mode, mode or "inconnu"), "ok" if mode else "unknown"


def _tasks_active(w):
    if not w.context.hub.tasks_available():
        return "indisponible", "unknown"
    return str(w.context.hub.tasks.counts().get("active", 0)), "ok"


def _tasks_unread(w):
    if not w.context.hub.tasks_available():
        return "indisponible", "unknown"
    return str(w.context.hub.tasks.counts().get("unread", 0)), "ok"


def _notifications(w):
    return str(w.context.hub.notifications.unread_count()), "ok"


def _turns(w):
    context = w.context.hub.conversation()
    if context is None:
        return "indisponible", "unknown"
    return str(context.snapshot().get("turns", 0)), "ok"


def _cpu(w):
    history = w.context.hub.resources.history()
    if not history:
        return "en attente", "unknown"
    return f"{history[-1].cpu_percent:.0f} %", "ok"


def _ram(w):
    history = w.context.hub.resources.history()
    if not history:
        return "en attente", "unknown"
    return f"{history[-1].ram_percent:.0f} %", "ok"


def _memories(w):
    stats = w._memory_stats
    if stats is None:
        return "…", "unknown"
    if not stats.get("success"):
        return "indisponible", "error"
    return str(stats.get("total", 0)), "ok"


METRICS: tuple[Metric, ...] = (
    Metric("health", "Santé globale", _health),
    Metric("engine", "Moteur", _engine),
    Metric("presence", "Présence", _presence),
    Metric("interface", "Interface", _interface),
    Metric("tasks_active", "Tâches actives", _tasks_active),
    Metric("tasks_unread", "Résultats non vus", _tasks_unread),
    Metric("notifications", "Notifications non lues", _notifications),
    Metric("turns", "Tours de conversation", _turns),
    Metric("memories", "Souvenirs", _memories, needs="memory"),
    Metric("cpu", "Processeur", _cpu, needs="resources"),
    Metric("ram", "Mémoire vive", _ram, needs="resources"),
)

DEFAULT_METRICS = {"health", "tasks_active", "notifications"}

SETTINGS: tuple[SettingField, ...] = (
    SettingField("title", "Titre", "text", "Mon panneau", maximum=40),
    SettingField("note", "Note", "text", "", maximum=200, help="Texte libre affiché sous les indicateurs."),
) + tuple(SettingField(f"m_{m.key}", f"Afficher : {m.label}", "bool", m.key in DEFAULT_METRICS) for m in METRICS)


class CustomPanelWidget(DashboardWidget):
    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        self._memory_stats = None
        self._resource_key = f"custom:{context.instance_id}"
        self._resources_on = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.grid = QGridLayout()
        self.grid.setColumnStretch(1, 1)
        layout.addLayout(self.grid)
        self.note = label("", "dim", wrap=True)
        layout.addWidget(self.note)
        layout.addStretch(1)
        self._rows: dict[str, tuple[StatusDot, object]] = {}

    def selected(self) -> list[Metric]:
        return [m for m in METRICS if self.settings.get(f"m_{m.key}", m.key in DEFAULT_METRICS)]

    def on_activate(self) -> None:
        hub = self.context.hub
        for signal in (hub.engine_changed, hub.presence_changed, hub.task_updated, hub.notifications_changed,
                       hub.conversation_changed, hub.interface_mode_changed, hub.resource_sample):
            self.listen(signal, lambda *_a: self._render_values())
        self.listen(hub.memory_changed, self._load_memory)
        self.refresh()

    def on_deactivate(self) -> None:
        if self._resources_on:
            self.context.hub.unsubscribe_resources(self._resource_key)
            self._resources_on = False

    def refresh(self) -> None:
        self._build()
        metrics = self.selected()
        wants_resources = any(m.needs == "resources" for m in metrics)
        if wants_resources and not self._resources_on:
            self._resources_on = self.context.hub.subscribe_resources(self._resource_key)
        elif not wants_resources and self._resources_on:
            self.context.hub.unsubscribe_resources(self._resource_key)
            self._resources_on = False
        if any(m.needs == "memory" for m in metrics):
            self._load_memory()
        self._render_values()

    def _load_memory(self) -> None:
        def _done(stats):
            self._memory_stats = stats
            self._render_values()

        self.run_job(self.context.hub.memory.stats, on_done=_done,
                     on_error=lambda e: _done({"success": False, "error": str(e)}))

    def _build(self) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._rows.clear()
        for row, metric in enumerate(self.selected()):
            dot = StatusDot(self.context.theme_manager, size=9)
            value = label("", "title")
            self.grid.addWidget(dot, row, 0)
            self.grid.addWidget(label(metric.label, "dim"), row, 1)
            self.grid.addWidget(value, row, 2)
            self._rows[metric.key] = (dot, value)
        title = str(self.settings.get("title") or "").strip()
        self.set_subtitle(title if title and title != "Mon panneau" else "")
        note = str(self.settings.get("note") or "").strip()
        if not self._rows:
            note = (note + "\n\n" if note else "") + (
                "Aucun indicateur sélectionné : ouvre « Réglages du widget » (menu ⋯) pour composer ce panneau."
            )
        self.note.setText(note)
        self.note.setVisible(bool(note))

    def _render_values(self) -> None:
        metrics = self.selected()
        for metric in metrics:
            row = self._rows.get(metric.key)
            if row is None:
                continue
            try:
                text, status = metric.read(self)
            except Exception as exc:
                text, status = f"erreur ({exc.__class__.__name__})", "error"
            row[0].set_status(status)
            row[1].setText(text)
        self.set_state(STATE_READY if metrics else STATE_EMPTY)
