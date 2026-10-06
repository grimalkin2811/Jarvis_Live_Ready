"""Dialogue non modal des tâches v1.8, alimenté exclusivement par TaskManager."""
from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout, QWidget,
)


class _TaskSignal(QObject):
    changed = Signal(object)


class BackgroundTasksDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Jarvis — Tâches d'arrière-plan")
        self.resize(900, 620)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self._signal = _TaskSignal(self)
        self._signal.changed.connect(self._on_task_changed, Qt.QueuedConnection)
        self._hook = self._signal.changed.emit
        self.manager.add_hook(self._hook)

        self.counts = QLabel()
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._select)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.cancel_button = QPushButton("Annuler la tâche")
        self.cancel_button.clicked.connect(self._cancel)
        self.refresh_button = QPushButton("Actualiser")
        self.refresh_button.clicked.connect(self.refresh)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(QLabel("En cours et historique"))
        left_layout.addWidget(self.list)
        buttons = QHBoxLayout()
        buttons.addWidget(self.refresh_button)
        buttons.addWidget(self.cancel_button)
        left_layout.addLayout(buttons)
        splitter = QSplitter()
        splitter.addWidget(left)
        splitter.addWidget(self.detail)
        splitter.setSizes([360, 540])
        layout = QVBoxLayout(self)
        layout.addWidget(self.counts)
        layout.addWidget(splitter)
        self.refresh()

    @staticmethod
    def _elapsed(task) -> str:
        source = task.finished_at or task.started_at or task.created_at
        try:
            instant = datetime.fromisoformat(source)
            seconds = max(0, int((datetime.now(timezone.utc) - instant).total_seconds()))
            if seconds < 60:
                return f"il y a {seconds} s"
            if seconds < 3600:
                return f"il y a {seconds // 60} min"
            return f"il y a {seconds // 3600} h"
        except Exception:
            return ""

    @Slot(object)
    def _on_task_changed(self, _task) -> None:
        self.refresh()

    @Slot()
    def refresh(self) -> None:
        selected = self.list.currentItem().data(Qt.UserRole) if self.list.currentItem() else None
        active = self.manager.list_active_tasks()
        unread = self.manager.list_unread_completed_tasks()
        self.counts.setText(f"{len(active)} active(s)  •  {len(unread)} nouveau(x) résultat(s)")
        self.list.clear()
        for task in self.manager.list_tasks():
            badge = "NEW" if task.status.value == "COMPLETED" and not task.seen else task.status.value
            model = task.model or "Routage…"
            step = f"{task.progress}% — {task.current_step}" if task.status.value not in {"COMPLETED", "FAILED", "CANCELLED"} else self._elapsed(task)
            item = QListWidgetItem(f"[{badge}] {task.title}\n{model} • {step}")
            item.setData(Qt.UserRole, task.id)
            self.list.addItem(item)
            if task.id == selected:
                self.list.setCurrentItem(item)
        if self.list.count() and self.list.currentItem() is None:
            self.list.setCurrentRow(0)

    @Slot(object, object)
    def _select(self, current, _previous) -> None:
        if current is None:
            self.detail.clear()
            self.cancel_button.setEnabled(False)
            return
        task_id = current.data(Qt.UserRole)
        task = self.manager.get_task(task_id)
        if task is None:
            return
        if task.status.value == "COMPLETED" and not task.seen:
            self.manager.mark_task_as_seen(task.id)
            task = self.manager.get_task(task.id)
        self.cancel_button.setEnabled(task.status.value in {"QUEUED", "RUNNING", "WAITING_FOR_TOOL"})
        files = "\n".join(f"• {path}" for path in task.files) or "Aucun"
        result = task.result or task.error or "Résultat pas encore disponible."
        self.detail.setPlainText(
            f"{task.title}\n\nStatut : {task.status.value}\nModèle : {task.model or '—'}\n"
            f"Complexité : {task.complexity.value if task.complexity else '—'}\n"
            f"Étape : {task.current_step_number}/{task.total_steps} — {task.current_step}\n"
            f"Progression : {task.progress}%\nRoutage : {task.routing_reason or '—'}\n\n"
            f"RÉSUMÉ\n{task.summary or '—'}\n\nRÉSULTAT\n{result}\n\nFICHIERS\n{files}"
        )

    @Slot()
    def _cancel(self) -> None:
        item = self.list.currentItem()
        if item and not self.manager.cancel_task(item.data(Qt.UserRole)):
            QMessageBox.information(self, "Jarvis", "Cette tâche ne peut plus être annulée.")

    def closeEvent(self, event) -> None:
        self.manager.remove_hook(self._hook)
        super().closeEvent(event)


_DIALOG = None


def show_background_tasks_dialog(parent=None):
    global _DIALOG
    try:
        from src.background_tasks import get_default_task_manager
        manager = get_default_task_manager()
    except Exception as exc:
        QMessageBox.warning(parent, "Jarvis — Tâches", str(exc))
        return None
    if _DIALOG is None:
        _DIALOG = BackgroundTasksDialog(manager, parent)
        _DIALOG.destroyed.connect(lambda *_: _clear_dialog())
    _DIALOG.show()
    _DIALOG.raise_()
    _DIALOG.activateWindow()
    return _DIALOG


def _clear_dialog():
    global _DIALOG
    _DIALOG = None
