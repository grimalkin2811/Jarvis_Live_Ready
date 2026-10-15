"""Widget « Mémoire » : parcourir, rechercher, créer, modifier, supprimer,
consulter l'historique et restaurer une version — via formulaires structurés.

Règles de confirmation (cahier des charges §8) :
création sans confirmation ; modification confirmée (niveau 2 imposé) ;
suppression avec confirmation renforcée (niveau 3) ; restauration écrasant la
version actuelle confirmée (niveau 2 imposé). Toute écriture passe par
``MemoryService`` (validation, transaction, contrôle de version).
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..memory_service import CATEGORY_LABELS, describe_changes, text_diff, validate_form, version_of
from ..widget_api import STATE_READY, STATE_UNAVAILABLE, DashboardWidget, WidgetAction
from .common import button, esc, hrow, iso_short, label


class MemoryWidget(DashboardWidget):
    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        self._records: list[dict] = []
        self._current: dict | None = None
        self._base: dict | None = None
        self._mode = "view"  # view | create
        self._loading_form = False
        self._dirty = False
        self._revisions: list[dict] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        splitter = QSplitter(Qt.Horizontal)
        layout.addWidget(splitter, 1)

        # -- liste -----------------------------------------------------------------
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 4, 0)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Rechercher dans la mémoire…")
        self.search.setClearButtonEnabled(True)
        self.category_filter = QComboBox()
        self.category_filter.addItem("Toutes les catégories", "")
        for key, text in CATEGORY_LABELS.items():
            self.category_filter.addItem(text, key)
        left_layout.addLayout(hrow(self.search, self.category_filter, stretch_last=False))
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        left_layout.addWidget(self.list, 1)
        self.count = label("", "dim")
        self.new_button = button("+ Nouveau souvenir", "primary")
        self.new_button.clicked.connect(self._new)
        left_layout.addLayout(hrow(self.count, None, self.new_button))
        splitter.addWidget(left)

        # -- détail / édition ---------------------------------------------------------
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 0, 0, 0)
        self.stack = QStackedWidget()
        right_layout.addWidget(self.stack, 1)
        empty = QWidget()
        empty_layout = QVBoxLayout(empty)
        empty_layout.addStretch(1)
        self.empty_label = label("Sélectionne un souvenir ou crée-en un nouveau.", "dim", wrap=True)
        self.empty_label.setAlignment(Qt.AlignCenter)
        empty_layout.addWidget(self.empty_label)
        empty_layout.addStretch(1)
        self.stack.addWidget(empty)

        editor = QWidget()
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        self.form_title = label("", "title")
        editor_layout.addWidget(self.form_title)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignLeft)
        self.content = QPlainTextEdit()
        self.content.setPlaceholderText("Ex. : Je préfère les réponses courtes le matin.")
        self.content.setMinimumHeight(80)
        self.content.textChanged.connect(self._mark_dirty)
        self.content_error = label("", "error", wrap=True)
        self.category = QComboBox()
        for key, text in CATEGORY_LABELS.items():
            self.category.addItem(text, key)
        self.category.currentIndexChanged.connect(self._mark_dirty)
        self.importance = QSpinBox()
        self.importance.setRange(1, 5)
        self.importance.setSuffix(" / 5")
        self.importance.valueChanged.connect(self._mark_dirty)
        form.addRow("Contenu", self.content)
        form.addRow("", self.content_error)
        form.addRow("Catégorie", self.category)
        form.addRow("Importance", self.importance)
        editor_layout.addLayout(form)
        self.meta = label("", "dim", wrap=True)
        editor_layout.addWidget(self.meta)
        self.banner = label("", "error", wrap=True)
        self.banner.hide()
        editor_layout.addWidget(self.banner)
        self.save_button = button("Enregistrer", "primary")
        self.save_button.clicked.connect(self._save)
        self.revert_button = button("Annuler les modifications")
        self.revert_button.clicked.connect(self._revert)
        self.delete_button = button("Supprimer…", "danger")
        self.delete_button.clicked.connect(self._delete)
        self.history_button = button("Historique")
        self.history_button.setCheckable(True)
        self.history_button.toggled.connect(self._toggle_history)
        editor_layout.addLayout(hrow(self.save_button, self.revert_button, None, self.history_button,
                                     self.delete_button))
        self.history_panel = QWidget()
        history_layout = QVBoxLayout(self.history_panel)
        history_layout.setContentsMargins(0, 4, 0, 0)
        self.history_list = QListWidget()
        self.history_list.setMaximumHeight(120)
        self.history_list.currentItemChanged.connect(self._on_revision)
        self.diff_view = QTextBrowser()
        self.diff_view.setMaximumHeight(120)
        self.restore_button = QPushButton("Restaurer cette version…")
        self.restore_button.clicked.connect(self._restore)
        history_layout.addWidget(label("Versions antérieures (enregistrées automatiquement à chaque modification)",
                                       "dim", wrap=True))
        history_layout.addWidget(self.history_list)
        history_layout.addWidget(self.diff_view)
        history_layout.addLayout(hrow(None, self.restore_button))
        self.history_panel.hide()
        editor_layout.addWidget(self.history_panel)
        editor_layout.addStretch(1)
        self.stack.addWidget(editor)
        splitter.addWidget(right)
        splitter.setSizes([340, 460])

        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(250)
        self._search_timer.timeout.connect(self.refresh)
        self.search.textChanged.connect(lambda _t: self._search_timer.start())
        self.category_filter.currentIndexChanged.connect(lambda _i: self.refresh())
        self._update_buttons()

    # -- cycle de vie -----------------------------------------------------------------

    def on_activate(self) -> None:
        self.listen(self.context.hub.memory_changed, self._external_change)
        self.refresh()

    def on_deactivate(self) -> None:
        self._search_timer.stop()

    def _external_change(self) -> None:
        # Modifiée par la voix pendant l'affichage : on recharge la liste ; le
        # formulaire en cours n'est PAS écrasé (le contrôle de version
        # signalera le conflit à l'enregistrement).
        self.refresh(keep_form=True)

    def refresh(self, keep_form: bool = False) -> None:
        service = self.context.hub.memory

        def _load():
            status = service.status()
            if not status.get("enabled", True) or not status.get("available", False):
                return status, None
            return status, service.browse(self.search.text(), self.category_filter.currentData() or None,
                                          int(self.settings.get("limit", 200) or 200))

        self.run_job(_load, on_done=lambda result: self._on_loaded(result, keep_form),
                     on_error=lambda e: self.set_state(STATE_UNAVAILABLE, str(e)))

    def _on_loaded(self, result, keep_form: bool) -> None:
        status, browse = result
        if browse is None:
            if not status.get("enabled", True):
                self.set_state(STATE_UNAVAILABLE, "Mémoire désactivée (menu Memory → Long-term Memory, ou "
                                                  "JARVIS_MEMORY_ENABLED=0).")
            else:
                self.set_state(STATE_UNAVAILABLE, status.get("error") or "Base mémoire indisponible.")
            return
        if not browse.get("success"):
            self.set_state("error", browse.get("error") or "Lecture impossible.")
            return
        self._records = list(browse.get("memories") or [])
        selected = self._current.get("id") if self._current else None
        self.list.blockSignals(True)
        self.list.clear()
        for record in self._records:
            stars = "★" * int(record.get("importance") or 0)
            excerpt = " ".join(str(record.get("content") or "").split())
            excerpt = excerpt[:90] + ("…" if len(excerpt) > 90 else "")
            item = QListWidgetItem(f"{excerpt}\n{CATEGORY_LABELS.get(record.get('category'), record.get('category'))}"
                                   f" · {stars} · {iso_short(record.get('updated_at'))}")
            item.setData(Qt.UserRole, record.get("id"))
            self.list.addItem(item)
            if record.get("id") == selected:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        total = browse.get("total", len(self._records))
        self.count.setText(f"{total} souvenir(s)" + (f" — {len(self._records)} affiché(s)" if total > len(self._records) else ""))
        self.set_subtitle(f"{total} souvenir(s)")
        self.set_state(STATE_READY)
        if not self._records and self._mode != "create":
            self.empty_label.setText("Aucun souvenir ne correspond." if self.search.text() or
                                     self.category_filter.currentData() else
                                     "La mémoire est vide. Crée un souvenir ou dis « souviens-toi que… ».")
            if not keep_form:
                self.stack.setCurrentIndex(0)

    # -- formulaire -----------------------------------------------------------------

    def _fill_form(self, record: dict | None) -> None:
        self._loading_form = True
        if record is None:
            self.content.setPlainText("")
            self.category.setCurrentIndex(max(0, self.category.findData("fact")))
            self.importance.setValue(3)
            self.meta.setText("")
        else:
            self.content.setPlainText(str(record.get("content") or ""))
            self.category.setCurrentIndex(max(0, self.category.findData(record.get("category"))))
            self.importance.setValue(int(record.get("importance") or 3))
            self.meta.setText(
                f"N° {record.get('id')} · créé {iso_short(record.get('created_at'))} · modifié "
                f"{iso_short(record.get('updated_at'))} · source {record.get('source') or '—'} · "
                f"consulté {record.get('access_count', 0)} fois"
                + (f" · sujet « {record.get('subject_key')} »" if record.get("subject_key") else "")
            )
        self.content_error.setText("")
        self.banner.hide()
        self._loading_form = False
        self._dirty = False
        self._update_buttons()

    def _mark_dirty(self, *_args) -> None:
        if not self._loading_form:
            self._dirty = True
            self._update_buttons()

    def _update_buttons(self) -> None:
        editing = self.stack.currentIndex() == 1
        self.save_button.setEnabled(editing and (self._dirty or self._mode == "create"))
        self.revert_button.setEnabled(editing and self._dirty)
        has_record = self._mode == "view" and self._current is not None
        self.delete_button.setEnabled(has_record)
        self.history_button.setEnabled(has_record)
        self.save_button.setText("Créer" if self._mode == "create" else "Enregistrer")

    def _confirm_discard(self) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(self, "Modifications non enregistrées",
                                      "Abandonner les modifications en cours ?")
        return answer == QMessageBox.Yes

    def _on_select(self, current, _previous) -> None:
        if current is None:
            return
        memory_id = current.data(Qt.UserRole)
        if self._current and self._current.get("id") == memory_id and self._mode == "view":
            return
        if not self._confirm_discard():
            return
        self.run_job(self.context.hub.memory.get, memory_id, on_done=self._on_record,
                     on_error=lambda e: self._show_banner(str(e)))

    def _on_record(self, result: dict) -> None:
        if not result.get("success"):
            self.context.toast(result.get("error") or "Souvenir introuvable.", "warning")
            self.refresh()
            return
        self._mode = "view"
        self._current = result["memory"]
        self._base = version_of(self._current)
        self.form_title.setText("Souvenir")
        self.stack.setCurrentIndex(1)
        self._fill_form(self._current)
        if self.history_button.isChecked():
            self._load_history()

    def _new(self) -> None:
        if not self._confirm_discard():
            return
        self._mode = "create"
        self._current = None
        self._base = None
        self.history_button.setChecked(False)
        self.form_title.setText("Nouveau souvenir")
        self.stack.setCurrentIndex(1)
        self._fill_form(None)
        self.content.setFocus()

    def _revert(self) -> None:
        self._fill_form(self._current if self._mode == "view" else None)

    def _show_banner(self, text: str) -> None:
        self.banner.setText(text)
        self.banner.show()

    def _form_values(self):
        return self.content.toPlainText(), self.category.currentData(), self.importance.value()

    # -- écriture -------------------------------------------------------------------

    def _save(self) -> None:
        content, category, importance = self._form_values()
        check = validate_form(content, category, importance)
        self.content_error.setText(check.errors.get("content", ""))
        if not check.ok:
            self._show_banner("Formulaire invalide : " + " ".join(check.errors.values()))
            return
        service = self.context.hub.memory
        if self._mode == "create":
            self.run_job(service.create, content, category, importance, on_done=self._on_created,
                         on_error=lambda e: self._show_banner(f"Échec : {e}"))
            return
        if self._current is None:
            return
        changes = describe_changes(self._base or {}, check.cleaned)
        if not self.context.confirm("memory.update", "Enregistrer la modification de ce souvenir ?",
                                    "\n".join(changes) or "Aucun changement détecté."):
            return
        memory_id = self._current["id"]
        self.run_job(service.update, memory_id, self._base, content, category, importance,
                     on_done=lambda r: self._on_written(r, "Souvenir modifié."),
                     on_error=lambda e: self._show_banner(f"Échec : {e}"))

    def _on_created(self, result: dict) -> None:
        if result.get("success"):
            self.context.toast("Souvenir créé.", "success")
            self._mode = "view"
            self._current = result.get("memory")
            self._base = version_of(self._current)
            self._fill_form(self._current)
            self.form_title.setText("Souvenir")
            self.refresh(keep_form=True)
            return
        if result.get("conflict") and result.get("existing"):
            existing = result["existing"]
            box = QMessageBox(self)
            box.setWindowTitle("Souvenir similaire")
            box.setText("Un souvenir portant le même sujet existe déjà :")
            box.setInformativeText(f"« {existing.get('content')} »\n\nQue veux-tu faire ?")
            update = box.addButton("Mettre à jour l'existant", QMessageBox.AcceptRole)
            distinct = box.addButton("Créer un souvenir distinct", QMessageBox.ActionRole)
            box.addButton("Annuler", QMessageBox.RejectRole)
            box.exec()
            content, category, importance = self._form_values()
            service = self.context.hub.memory
            if box.clickedButton() is update:
                self.run_job(service.update, existing["id"], version_of(existing), content, category, importance,
                             on_done=lambda r: self._on_written(r, "Souvenir existant mis à jour."),
                             on_error=lambda e: self._show_banner(f"Échec : {e}"))
            elif box.clickedButton() is distinct:
                self.run_job(service.create, content, category, importance, distinct=True,
                             on_done=self._on_created, on_error=lambda e: self._show_banner(f"Échec : {e}"))
            return
        self._show_banner(result.get("error") or "Création impossible (rien n'a été écrit).")
        if result.get("validation"):
            self.content_error.setText(result["validation"].get("content", ""))

    def _on_written(self, result: dict, success_message: str) -> None:
        if result.get("success"):
            self.context.toast(success_message, "success")
            self._mode = "view"
            self._current = result.get("memory")
            self._base = version_of(self._current)
            self._fill_form(self._current)
            self.refresh(keep_form=True)
            if self.history_button.isChecked():
                self._load_history()
            return
        if result.get("conflict") and result.get("current"):
            self._resolve_conflict(result["current"])
            return
        if result.get("not_found"):
            self.context.toast(result.get("error") or "Souvenir introuvable.", "warning")
            self._mode = "view"
            self._current = None
            self.stack.setCurrentIndex(0)
            self.refresh()
            return
        if result.get("unchanged"):
            self.context.toast(result.get("error") or "Aucune modification à enregistrer.", "info")
            return
        self._show_banner((result.get("error") or "Écriture impossible") + " — rien n'a été modifié.")

    def _resolve_conflict(self, current: dict) -> None:
        content, category, importance = self._form_values()
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle("Modification concurrente")
        box.setText("Ce souvenir a été modifié entre-temps (par la voix ou une autre vue).")
        box.setInformativeText(f"Version actuelle :\n« {current.get('content')} »\n\nTa version :\n« {content} »")
        reload_btn = box.addButton("Recharger la version actuelle", QMessageBox.AcceptRole)
        overwrite = box.addButton("Écraser avec ma version…", QMessageBox.DestructiveRole)
        box.addButton("Continuer l'édition", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is reload_btn:
            self._current = current
            self._base = version_of(current)
            self._fill_form(current)
        elif box.clickedButton() is overwrite:
            if not self.context.confirm("memory.update", "Écraser la version actuelle ?",
                                        "La version actuelle restera consultable dans l'historique."):
                return
            self.run_job(self.context.hub.memory.update, current["id"], version_of(current), content, category,
                         importance, on_done=lambda r: self._on_written(r, "Souvenir modifié."),
                         on_error=lambda e: self._show_banner(f"Échec : {e}"))

    def _delete(self) -> None:
        if self._current is None:
            return
        excerpt = str(self._current.get("content") or "")[:160]
        if not self.context.confirm(
            "memory.delete", "Supprimer ce souvenir ?",
            f"« {excerpt} »\nCatégorie : {CATEGORY_LABELS.get(self._current.get('category'))}\n"
            "Jarvis ne s'en servira plus (même comportement que « oublie… » à la voix).",
        ):
            return
        self.run_job(self.context.hub.memory.delete, self._current["id"], self._base,
                     on_done=self._on_deleted, on_error=lambda e: self._show_banner(f"Échec : {e}"))

    def _on_deleted(self, result: dict) -> None:
        if result.get("success"):
            self.context.toast("Souvenir supprimé.", "success")
            self._current = None
            self._base = None
            self._dirty = False
            self.stack.setCurrentIndex(0)
            self.history_button.setChecked(False)
            self.refresh()
        elif result.get("conflict") and result.get("current"):
            self.context.toast("Le souvenir a changé : vérifie la nouvelle version avant de supprimer.", "warning")
            self._current = result["current"]
            self._base = version_of(self._current)
            self._fill_form(self._current)
        else:
            self._show_banner((result.get("error") or "Suppression impossible") + " — rien n'a été modifié.")

    # -- historique -----------------------------------------------------------------

    def _toggle_history(self, checked: bool) -> None:
        self.history_panel.setVisible(checked)
        if checked:
            self._load_history()

    def _load_history(self) -> None:
        if self._current is None:
            return
        self.run_job(self.context.hub.memory.history, self._current["id"], on_done=self._on_history,
                     on_error=lambda e: self.diff_view.setPlainText(str(e)))

    def _on_history(self, result: dict) -> None:
        self.history_list.clear()
        self._revisions = list(result.get("revisions") or [])
        if not result.get("success"):
            self.diff_view.setPlainText(result.get("error") or "Historique illisible.")
            return
        if not result.get("available", True):
            self.diff_view.setPlainText("Historique indisponible pour cette base (schéma antérieur).")
            return
        for revision in self._revisions:
            state = " · supprimée" if revision.get("archived") else ""
            excerpt = " ".join(str(revision.get("content") or "").split())[:70]
            item = QListWidgetItem(f"Jusqu'au {iso_short(revision.get('recorded_at'))}{state} — {excerpt}")
            item.setData(Qt.UserRole, revision.get("id"))
            self.history_list.addItem(item)
        if not self._revisions:
            self.diff_view.setPlainText("Aucune version antérieure : ce souvenir n'a jamais été modifié "
                                        "depuis l'activation de l'historique.")
        self.restore_button.setEnabled(False)

    def _on_revision(self, current, _previous) -> None:
        if current is None or self._current is None:
            self.restore_button.setEnabled(False)
            return
        revision = next((r for r in self._revisions if r.get("id") == current.data(Qt.UserRole)), None)
        if revision is None:
            return
        t = self.context.theme
        parts = []
        for op, text in text_diff(self._current.get("content", ""), revision.get("content", "")):
            if op == "=":
                parts.append(esc(text))
            elif op == "-":
                parts.append(f"<span style='color:{t.danger.name()}; text-decoration: line-through'>{esc(text)}</span>")
            else:
                parts.append(f"<span style='color:{t.success.name()}'>{esc(text)}</span>")
        changes = describe_changes(self._current, revision)
        header = "<br>".join(esc(c) for c in changes if c != "Contenu modifié")
        self.diff_view.setHtml(
            f"<p style='color:{t.text_dim.name()}'>Différences entre la version actuelle et la version choisie "
            f"(<span style='color:{t.danger.name()}'>retiré</span> / "
            f"<span style='color:{t.success.name()}'>restauré</span>)</p>"
            + (f"<p>{header}</p>" if header else "") + f"<p>{' '.join(parts)}</p>"
        )
        self.restore_button.setEnabled(True)

    def _restore(self) -> None:
        item = self.history_list.currentItem()
        if item is None or self._current is None:
            return
        if self._dirty and not self._confirm_discard():
            return
        if not self.context.confirm("memory.restore", "Restaurer cette version ?",
                                    "La version actuelle sera remplacée ; elle restera dans l'historique."):
            return
        self.run_job(self.context.hub.memory.restore, self._current["id"], item.data(Qt.UserRole), self._base,
                     on_done=lambda r: self._on_written(r, "Version restaurée."),
                     on_error=lambda e: self._show_banner(f"Échec : {e}"))

    def actions(self) -> list[WidgetAction]:
        return [WidgetAction("new", "Nouveau souvenir", self._new)]
