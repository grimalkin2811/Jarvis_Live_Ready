"""Boîtes de dialogue du Dashboard : confirmations (niveaux 2/3), bibliothèque
de widgets, réglages d'instance, confirmation d'arrêt."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .glyphs import glyph_icon
from .security import LEVEL_CONFIRM, LEVEL_DESTRUCTIVE
from .widget_api import CAPABILITIES, CATEGORIES, DEPENDENCIES, WidgetSpec


class ConfirmDialog(QDialog):
    """Niveau 2 : confirmation simple. Niveau 3 : résumé + case explicite."""

    def __init__(self, level: int, title: str, summary: str, details: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)
        head = QLabel(summary)
        head.setWordWrap(True)
        head.setProperty("role", "title")
        layout.addWidget(head)
        if details:
            body = QLabel(details)
            body.setWordWrap(True)
            body.setProperty("role", "dim")
            body.setTextInteractionFlags(Qt.TextSelectableByMouse)
            layout.addWidget(body)
        self.check: QCheckBox | None = None
        buttons = QDialogButtonBox()
        self.ok_button = QPushButton("Confirmer")
        self.ok_button.setProperty("role", "danger" if level >= LEVEL_DESTRUCTIVE else "primary")
        buttons.addButton(self.ok_button, QDialogButtonBox.AcceptRole)
        cancel = buttons.addButton("Annuler", QDialogButtonBox.RejectRole)
        cancel.setDefault(True)
        cancel.setAutoDefault(True)
        if level >= LEVEL_DESTRUCTIVE:
            warning = QLabel("Action destructive : vérifie le résumé ci-dessus.")
            warning.setProperty("role", "error")
            layout.addWidget(warning)
            self.check = QCheckBox("Je confirme cette action")
            self.check.toggled.connect(self.ok_button.setEnabled)
            layout.addWidget(self.check)
            self.ok_button.setEnabled(False)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


def ask_confirmation(policy, effective_level: int, summary: str, details: str = "", parent=None,
                     executor=None) -> bool:
    """Applique la politique : niveau 1 → True sans dialogue."""
    if effective_level < LEVEL_CONFIRM:
        return True
    dialog = ConfirmDialog(effective_level, policy.label, summary, details, parent)
    if executor is not None:
        return bool(executor(dialog))
    return dialog.exec() == QDialog.Accepted


class WidgetLibraryDialog(QDialog):
    """Bibliothèque : description, catégorie, dépendances, disponibilité sur la page."""

    def __init__(self, registry, page, parent=None, icon_color=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Ajouter un widget")
        # Icônes des widgets peintes avec la police de symboles (correctif D01).
        self.icon_color = QColor(icon_color) if icon_color is not None else self.palette().color(QPalette.WindowText)
        self.resize(640, 520)
        self.selected: str | None = None
        layout = QVBoxLayout(self)
        self.category = QComboBox()
        self.category.addItem("Toutes les catégories", "")
        for key, text in CATEGORIES.items():
            self.category.addItem(text, key)
        self.category.currentIndexChanged.connect(self._fill)
        layout.addWidget(self.category)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._describe)
        self.list.itemDoubleClicked.connect(lambda _i: self._accept())
        layout.addWidget(self.list, 1)
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setProperty("role", "dim")
        layout.addWidget(self.description)
        buttons = QDialogButtonBox()
        self.add_button = buttons.addButton("Ajouter", QDialogButtonBox.AcceptRole)
        buttons.addButton("Fermer", QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.registry = registry
        self.page = page
        self._fill()

    def _fill(self) -> None:
        category = self.category.currentData()
        self.list.clear()
        for spec in sorted(self.registry.specs(), key=lambda s: (s.category, s.title)):
            if category and spec.category != category:
                continue
            allowed = self.registry.instances_allowed(spec.type_id, self.page)
            suffix = "" if allowed else "  (déjà présent sur cette page)"
            item = QListWidgetItem(f"{spec.title}{suffix}\n{CATEGORIES.get(spec.category)}")
            if spec.icon:
                item.setIcon(glyph_icon(spec.icon, self.icon_color, 18))
            item.setData(Qt.UserRole, spec.type_id)
            if not allowed:
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.list.addItem(item)

    def _describe(self, current, _previous) -> None:
        if current is None:
            self.description.setText("")
            return
        spec: WidgetSpec = self.registry.spec(current.data(Qt.UserRole))
        caps = ", ".join(CAPABILITIES[c] for c in sorted(spec.capabilities)) or "—"
        deps = ", ".join(DEPENDENCIES[d] for d in spec.dependencies) or "aucune"
        self.description.setText(
            f"{spec.description}\n\nCapacités : {caps}\nDépendances : {deps}\n"
            f"Taille minimale : {spec.min_size[0]}×{spec.min_size[1]} · par défaut {spec.default_size[0]}×"
            f"{spec.default_size[1]}"
        )

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is None or not (item.flags() & Qt.ItemIsEnabled):
            return
        self.selected = item.data(Qt.UserRole)
        self.accept()


class WidgetSettingsDialog(QDialog):
    """Formulaire généré depuis les ``SettingField`` (jamais de JSON brut)."""

    def __init__(self, spec: WidgetSpec, settings: dict, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Réglages — {spec.title}")
        self.spec = spec
        self.values = dict(settings)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.editors: dict[str, QWidget] = {}
        if not spec.settings:
            note = QLabel("Ce widget n'a pas de réglage propre.")
            note.setProperty("role", "dim")
            layout.addWidget(note)
        for field in spec.settings:
            value = self.values.get(field.key, field.default)
            if field.kind == "bool":
                editor = QCheckBox()
                editor.setChecked(bool(value))
            elif field.kind == "int":
                editor = QSpinBox()
                editor.setRange(field.minimum, field.maximum)
                editor.setValue(int(value))
            elif field.kind == "choice":
                editor = QComboBox()
                for key, text in field.choices:
                    editor.addItem(text, key)
                editor.setCurrentIndex(max(0, editor.findData(value)))
            else:
                editor = QLineEdit(str(value or ""))
                editor.setMaxLength(field.maximum or 200)
            if field.help:
                editor.setToolTip(field.help)
            form.addRow(field.label, editor)
            self.editors[field.key] = editor
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _save(self) -> None:
        for field in self.spec.settings:
            editor = self.editors[field.key]
            if isinstance(editor, QCheckBox):
                value = editor.isChecked()
            elif isinstance(editor, QSpinBox):
                value = editor.value()
            elif isinstance(editor, QComboBox):
                value = editor.currentData()
            else:
                value = editor.text()
            self.values[field.key] = field.validate(value)
        self.accept()


class ShutdownDialog(QDialog):
    """Arrêt complet : explique ce qui s'arrête ; confirmation renforcée si des tâches tournent."""

    def __init__(self, active_tasks: list[str], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Quitter Jarvis")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        title = QLabel("Fermer le Dashboard va quitter Jarvis (mode de fermeture « arrêt complet »).")
        title.setWordWrap(True)
        title.setProperty("role", "title")
        layout.addWidget(title)
        stops = ["la voix et la session Gemini Live", "la conversation en cours (non conservée)",
                 "les rappels et routines programmés jusqu'au prochain lancement"]
        if active_tasks:
            stops.append(f"{len(active_tasks)} tâche(s) d'arrière-plan en cours : " + ", ".join(active_tasks[:5]))
        body = QLabel("S'arrêteront :\n• " + "\n• ".join(stops) + "\n\nLa mémoire, les notes et les préférences "
                      "sont conservées.")
        body.setWordWrap(True)
        body.setProperty("role", "dim")
        layout.addWidget(body)
        self.check: QCheckBox | None = None
        row = QHBoxLayout()
        self.keep_button = QPushButton("Masquer seulement (Jarvis continue)")
        self.quit_button = QPushButton("Quitter Jarvis")
        self.quit_button.setProperty("role", "danger")
        cancel = QPushButton("Annuler")
        row.addWidget(self.keep_button)
        row.addStretch(1)
        row.addWidget(cancel)
        row.addWidget(self.quit_button)
        if active_tasks:
            self.check = QCheckBox("J'accepte d'interrompre les tâches en cours")
            self.check.toggled.connect(self.quit_button.setEnabled)
            layout.addWidget(self.check)
            self.quit_button.setEnabled(False)
        layout.addLayout(row)
        self.choice = "cancel"
        self.keep_button.clicked.connect(lambda: self._done("hide"))
        self.quit_button.clicked.connect(lambda: self._done("quit"))
        cancel.clicked.connect(lambda: self._done("cancel"))

    def _done(self, choice: str) -> None:
        self.choice = choice
        self.accept() if choice != "cancel" else self.reject()
