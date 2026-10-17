"""Widgets « Tâches » : tâches d'arrière-plan (Task Manager) et tâches personnelles."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..tasks_service import ACTIVE_STATUSES, STATUS_LABELS, elapsed_label, status_of, supported_actions
from ..widget_api import STATE_EMPTY, STATE_READY, STATE_UNAVAILABLE, DashboardWidget, WidgetAction
from .common import button, hrow, label

FILTERS = {
    "all": ("Toutes", None),
    "active": ("Actives", ACTIVE_STATUSES),
    "done": ("Terminées", frozenset({"COMPLETED"})),
    "failed": ("Échouées / annulées", frozenset({"FAILED", "CANCELLED"})),
}


class ResultDialog(QDialog):
    def __init__(self, title: str, text: str, files: list[str], open_path, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(720, 520)
        layout = QVBoxLayout(self)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(text)
        layout.addWidget(view, 1)
        for path in files:
            btn = button(f"Ouvrir {path.rsplit('/', 1)[-1].rsplit(chr(92), 1)[-1]}")
            btn.clicked.connect(lambda _=False, p=path: open_path(p))
            layout.addWidget(btn)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)


class BackgroundTasksWidget(DashboardWidget):
    """Statuts réels du Task Manager ; actions limitées à celles supportées."""

    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.filter = QComboBox()
        for key, (text, _statuses) in FILTERS.items():
            self.filter.addItem(text, key)
        self.filter.currentIndexChanged.connect(self._on_filter)
        self.counts = label("", "dim")
        layout.addLayout(hrow(self.filter, None, self.counts))
        splitter = QSplitter(Qt.Vertical)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.currentItemChanged.connect(lambda *_: self._show_detail())
        splitter.addWidget(self.list)
        detail = QWidget()
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(0, 4, 0, 0)
        self.detail_title = label("", "title", wrap=True)
        self.detail_meta = label("", "dim", wrap=True)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.detail_text = label("", wrap=True)
        self.detail_text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_meta)
        detail_layout.addWidget(self.progress)
        detail_layout.addWidget(self.detail_text, 1)
        self.view_button = button("Voir le résultat")
        self.view_button.clicked.connect(self._view)
        self.files_button = button("Ouvrir les fichiers")
        self.files_button.clicked.connect(self._open_files)
        self.cancel_button = button("Annuler", "danger")
        self.cancel_button.clicked.connect(self._cancel)
        self.retry_button = button("Relancer")
        self.retry_button.clicked.connect(self._retry)
        detail_layout.addLayout(hrow(self.view_button, self.files_button, self.retry_button, self.cancel_button, None))
        splitter.addWidget(detail)
        splitter.setSizes([220, 160])
        layout.addWidget(splitter, 1)
        self._tasks = []
        self._apply_filter_setting()

    def _apply_filter_setting(self) -> None:
        key = self.settings.get("filter", "all")
        index = max(0, self.filter.findData(key))
        self.filter.blockSignals(True)
        self.filter.setCurrentIndex(index)
        self.filter.blockSignals(False)

    def on_settings_changed(self) -> None:
        self._apply_filter_setting()
        super().on_settings_changed()

    def _on_filter(self) -> None:
        self.settings["filter"] = self.filter.currentData()
        self.render()

    def on_activate(self) -> None:
        hub = self.context.hub
        self.listen(hub.task_updated, lambda _t: self.refresh())
        self.listen(hub.tasks_attached, lambda _a: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        service = self.context.hub.tasks
        if service.manager() is None:
            self._tasks = []
            self.set_state(STATE_UNAVAILABLE, service.unavailable_reason() or "Task Manager non initialisé.")
            return
        self.run_job(service.list_tasks, on_done=self._on_tasks, on_error=lambda e: self.set_state("error", str(e)))

    def _on_tasks(self, tasks) -> None:
        self._tasks = list(tasks)
        self.render()

    def render(self) -> None:
        selected = self.list.currentItem().data(Qt.UserRole) if self.list.currentItem() else None
        statuses = FILTERS.get(self.filter.currentData(), FILTERS["all"])[1]
        visible = [t for t in self._tasks if statuses is None or status_of(t) in statuses]
        active = sum(1 for t in self._tasks if status_of(t) in ACTIVE_STATUSES)
        unread = sum(1 for t in self._tasks if status_of(t) == "COMPLETED" and not getattr(t, "seen", True))
        self.counts.setText(f"{active} active(s) · {unread} nouveau(x)")
        self.set_subtitle(f"{active} en cours" if active else "")
        self.list.blockSignals(True)
        self.list.clear()
        for task in visible:
            status = status_of(task)
            badge = "NOUVEAU" if status == "COMPLETED" and not getattr(task, "seen", True) else STATUS_LABELS.get(status, status)
            line2 = (f"{task.progress}% — {task.current_step}" if status in ACTIVE_STATUSES else elapsed_label(task))
            item = QListWidgetItem(f"[{badge}] {task.title}\n{task.model or 'Routage…'} · {line2}")
            item.setData(Qt.UserRole, task.id)
            self.list.addItem(item)
            if task.id == selected:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if self.list.count() and self.list.currentItem() is None:
            self.list.setCurrentRow(0)
        self._show_detail()
        if not self._tasks:
            self.set_state(STATE_EMPTY)
            self.detail_title.setText("Aucune tâche d'arrière-plan.")
            self.detail_meta.setText("Demande par exemple à Jarvis : « fais une recherche approfondie sur… ».")
        else:
            self.set_state(STATE_READY)

    def _current(self):
        item = self.list.currentItem()
        if item is None:
            return None
        task_id = item.data(Qt.UserRole)
        for task in self._tasks:
            if task.id == task_id:
                return task
        return None

    def _show_detail(self) -> None:
        task = self._current()
        for b in (self.view_button, self.files_button, self.cancel_button, self.retry_button):
            b.setVisible(False)
        if task is None:
            self.detail_title.setText("")
            self.detail_meta.setText("")
            self.detail_text.setText("")
            self.progress.setVisible(False)
            return
        status = status_of(task)
        self.detail_title.setText(task.title)
        complexity = getattr(getattr(task, "complexity", None), "value", "") or "—"
        self.detail_meta.setText(
            f"{STATUS_LABELS.get(status, status)} · modèle {task.model or '—'} · complexité {complexity}"
            f" · étape {task.current_step_number}/{task.total_steps}"
        )
        self.progress.setVisible(status in ACTIVE_STATUSES)
        self.progress.setValue(int(task.progress or 0))
        if status == "FAILED":
            self.detail_text.setText(f"Erreur : {task.error or 'non précisée'}")
        elif status == "COMPLETED":
            self.detail_text.setText((task.summary or task.result or "")[:600])
        else:
            self.detail_text.setText(task.current_step or task.routing_reason or "")
        actions = supported_actions(task)
        self.view_button.setVisible("view" in actions)
        self.files_button.setVisible("open_files" in actions)
        self.cancel_button.setVisible("cancel" in actions)
        self.retry_button.setVisible("retry" in actions)

    def _view(self) -> None:
        task = self._current()
        if task is None:
            return
        service = self.context.hub.tasks

        def _done(result) -> None:
            if not result:
                self.context.toast("Résultat indisponible.", "warning")
                return
            text = result.get("result") or result.get("error") or result.get("summary") or "(vide)"
            files = service.existing_files(task)
            ResultDialog(task.title, str(text), files, self.context.open_path, self).exec()
            self.refresh()

        self.run_job(service.result, task.id, on_done=_done, on_error=lambda e: self.context.toast(str(e), "error"))

    def _open_files(self) -> None:
        task = self._current()
        if task is None:
            return
        files = self.context.hub.tasks.existing_files(task)
        if not files:
            self.context.toast("Aucun fichier existant pour cette tâche.", "warning")
            return
        if not self.context.confirm("task.open_result", "Ouvrir les fichiers produits ?", "\n".join(files)):
            return
        opened = sum(1 for path in files if self.context.open_path(path))
        self.context.toast(f"{opened}/{len(files)} fichier(s) ouvert(s).", "success" if opened else "error")

    def _cancel(self) -> None:
        task = self._current()
        if task is None:
            return
        if not self.context.confirm("task.cancel", f"Annuler « {task.title} » ?",
                                    "Le travail en cours de cette tâche sera perdu."):
            return
        ok, message = self.context.hub.tasks.cancel(task.id)
        self.context.toast(message, "success" if ok else "warning")
        self.refresh()

    def _retry(self) -> None:
        task = self._current()
        if task is None:
            return
        if not self.context.confirm("task.retry", f"Relancer « {task.title} » ?",
                                    "Une nouvelle tâche est créée ; elle consomme du quota de modèle."):
            return
        ok, message, _new = self.context.hub.tasks.retry(task.id)
        self.context.toast(message, "success" if ok else "error")
        self.refresh()

    def actions(self) -> list[WidgetAction]:
        task = self._current()
        acts = supported_actions(task) if task is not None else []
        return [
            WidgetAction("cancel", "Annuler la tâche sélectionnée", self._cancel, "cancel" in acts,
                         "Seules les tâches actives peuvent être annulées."),
            WidgetAction("retry", "Relancer la tâche sélectionnée", self._retry, "retry" in acts,
                         "Seules les tâches échouées ou annulées peuvent être relancées."),
        ]


class TodoWidget(DashboardWidget):
    """Tâches personnelles (même base que add_todo / list_todos à la voix)."""

    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        layout.addWidget(self.list, 1)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Nouvelle tâche personnelle…")
        self.entry.returnPressed.connect(self._add)
        self.due = QLineEdit()
        self.due.setPlaceholderText("Échéance (ex. demain 9h)")
        self.due.setMaximumWidth(170)
        add = button("Ajouter", "primary")
        add.clicked.connect(self._add)
        layout.addLayout(hrow(self.entry, self.due, add, stretch_last=False))
        self.message = label("", "dim", wrap=True)
        layout.addWidget(self.message)
        self._building = False

    def on_activate(self) -> None:
        self.listen(self.context.hub.todo_changed, self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.run_job(self.context.hub.todo.list_pending, 50, on_done=self._on_list,
                     on_error=lambda e: self.set_state(STATE_UNAVAILABLE, str(e)))

    def _on_list(self, result) -> None:
        if not result.get("success"):
            self.set_state(STATE_UNAVAILABLE, result.get("error") or "Liste indisponible.")
            return
        tasks = result.get("taches") or result.get("tasks") or []
        self._building = True
        self.list.clear()
        for task in tasks:
            text = task.get("tache") or task.get("label") or "?"
            due = task.get("echeance_texte") or ""
            item = QListWidgetItem()
            self.list.addItem(item)
            box = QCheckBox(f"{text}" + (f"  — {due}" if due else "") + (
                "  (haute)" if task.get("priorite") == "haute" else ""))
            box.toggled.connect(lambda checked, tid=task.get("id"): self._complete(tid) if checked else None)
            self.list.setItemWidget(item, box)
        if not tasks:
            # État vide DANS la liste : la ligne de message reste réservée au
            # retour de la dernière action (« Tâche terminée. »…).
            placeholder = QListWidgetItem("Aucune tâche personnelle en attente.")
            placeholder.setFlags(Qt.NoItemFlags)
            self.list.addItem(placeholder)
        self._building = False
        self.set_subtitle(f"{len(tasks)} en attente")
        self.set_state(STATE_READY if tasks else STATE_EMPTY)

    def _add(self) -> None:
        text = self.entry.text().strip()
        if not text:
            return
        due = self.due.text().strip()

        def _done(result) -> None:
            if result.get("success"):
                self.entry.clear()
                self.due.clear()
                self.message.setText("Tâche ajoutée.")
                self.refresh()
            else:
                self.message.setText(f"Non ajoutée : {result.get('error')}" + (
                    f" — {result.get('hint')}" if result.get("hint") else ""))

        self.run_job(self.context.hub.todo.add, text, due, on_done=_done,
                     on_error=lambda e: self.message.setText(f"Erreur : {e}"))

    def _complete(self, task_id) -> None:
        if self._building or task_id is None:
            return

        def _done(result) -> None:
            self.message.setText("Tâche terminée." if result.get("success") else f"Échec : {result.get('error')}")
            self.refresh()

        self.run_job(self.context.hub.todo.complete, task_id, on_done=_done,
                     on_error=lambda e: self.message.setText(f"Erreur : {e}"))
