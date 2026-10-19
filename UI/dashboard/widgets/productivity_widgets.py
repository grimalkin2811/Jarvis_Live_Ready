"""Widgets « Productivité » : notes et documents réels."""

from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from ..notes_service import add_note, documents_root, list_documents, read_notes
from ..resources import human_bytes
from ..widget_api import STATE_EMPTY, STATE_READY, STATE_UNAVAILABLE, DashboardWidget, WidgetAction
from .common import button, hrow, label


class NotesWidget(DashboardWidget):
    """Notes de l'outil vocal ``take_note`` (même fichier, mêmes fonctions)."""

    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        layout.addWidget(self.list, 1)
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("Nouvelle note…")
        self.entry.returnPressed.connect(self._add)
        add = button("Noter", "primary")
        add.clicked.connect(self._add)
        layout.addLayout(hrow(self.entry, add, stretch_last=False))
        self.message = label("", "dim", wrap=True)
        layout.addWidget(self.message)

    def on_activate(self) -> None:
        self.listen(self.context.hub.notes_changed, self.refresh)
        self.refresh()

    def refresh(self) -> None:
        limit = int(self.settings.get("limit", 20) or 20)
        self.run_job(read_notes, limit, on_done=self._on_notes,
                     on_error=lambda e: self.set_state(STATE_UNAVAILABLE, f"Notes illisibles : {e}"))

    def _on_notes(self, result: dict) -> None:
        if not result.get("success"):
            self.set_state(STATE_UNAVAILABLE, result.get("error") or "Notes indisponibles.")
            return
        notes = list(result.get("notes") or [])
        self.list.clear()
        for note in reversed(notes):
            item = QListWidgetItem(f"{note.get('date', '')}\n{note.get('texte', '')}")
            self.list.addItem(item)
        self.set_subtitle(f"{result.get('count', len(notes))} note(s)")
        self.set_state(STATE_READY if notes else STATE_EMPTY)
        if not notes:
            self.message.setText("Aucune note. Écris-en une ou dis « prends note que… ».")

    def _add(self) -> None:
        text = self.entry.text().strip()
        if not text:
            return

        def _done(result: dict) -> None:
            if result.get("success"):
                self.entry.clear()
                self.message.setText("Note enregistrée.")
                self.refresh()
            else:
                self.message.setText(f"Note non enregistrée : {result.get('error')}")

        self.run_job(add_note, text, on_done=_done, on_error=lambda e: self.message.setText(f"Erreur : {e}"))


class DocumentsWidget(DashboardWidget):
    """Fichiers réellement présents dans ``user_content`` (écriture, tâches)."""

    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(self._open_item)
        layout.addWidget(self.list, 1)
        self.open_button = button("Ouvrir")
        self.open_button.clicked.connect(lambda: self._open_item(self.list.currentItem()))
        self.folder_button = button("Ouvrir le dossier")
        self.folder_button.clicked.connect(self._open_folder)
        self.info = label("", "dim", wrap=True)
        layout.addLayout(hrow(self.open_button, self.folder_button, None))
        layout.addWidget(self.info)
        self._root = ""

    def on_activate(self) -> None:
        self.listen(self.context.hub.task_updated, lambda _t: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        def _load():
            root = documents_root()
            return root, list_documents(root, int(self.settings.get("limit", 100) or 100))

        self.run_job(_load, on_done=self._on_docs, on_error=lambda e: self.set_state(STATE_UNAVAILABLE, str(e)))

    def _on_docs(self, result) -> None:
        root, docs = result
        self._root = root
        self.list.clear()
        for doc in docs:
            stamp = time.strftime("%d/%m %H:%M", time.localtime(doc.modified))
            folder = f"{doc.folder}/" if doc.folder else ""
            item = QListWidgetItem(f"{doc.name}\n{folder} · {human_bytes(doc.size)} · {stamp}")
            item.setData(Qt.UserRole, doc.path)
            item.setToolTip(doc.path)
            self.list.addItem(item)
        self.folder_button.setEnabled(bool(root) and os.path.isdir(root))
        self.open_button.setEnabled(bool(docs))
        self.set_subtitle(f"{len(docs)} fichier(s)")
        if docs:
            self.info.setText("")
            self.set_state(STATE_READY)
        else:
            self.info.setText("Aucun document : les textes créés par Jarvis et les résultats de tâches apparaîtront ici.")
            self.set_state(STATE_EMPTY)

    def _open_item(self, item) -> None:
        if item is None:
            return
        path = item.data(Qt.UserRole)
        if not path or not os.path.exists(path):
            self.context.toast("Fichier introuvable (déplacé ou supprimé).", "warning")
            self.refresh()
            return
        ok = self.context.open_path(path)
        self.context.toast("Document ouvert." if ok else "Le système n'a pas pu ouvrir le document.",
                           "success" if ok else "error")

    def _open_folder(self) -> None:
        if self._root and os.path.isdir(self._root):
            ok = self.context.open_path(self._root)
            self.context.toast("Dossier ouvert." if ok else "Ouverture impossible.", "success" if ok else "error")

    def actions(self) -> list[WidgetAction]:
        return [WidgetAction("folder", "Ouvrir le dossier des documents", self._open_folder,
                             bool(self._root and os.path.isdir(self._root)), "Dossier pas encore créé.")]
