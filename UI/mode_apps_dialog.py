"""Réglage des applications fermées par les modes jeu / focus.

Fenêtre PySide6 (dans l'esprit du catalogue de routines) qui lit/écrit la
configuration PERSISTANTE des modes (``mode.json`` → ``game_apps`` /
``focus_apps``) via le gestionnaire ``src/modes.JarvisModeManager``.

Chaque case à cocher correspond à une application du catalogue générique
(``src/mode_apps.KNOWN_APPS``) ; des applications « sur mesure » saisies par
l'utilisateur s'ajoutent en bas de liste. Les deux modes sont totalement
indépendants : modifier le mode jeu ne touche jamais au mode focus, et
inversement. Les modifications sont enregistrées immédiatement.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src import mode_apps
from src.modes import MODE_FOCUS, MODE_GAME, get_default_mode_manager

MODE_TITLES = {
    MODE_GAME: "Mode jeu",
    MODE_FOCUS: "Mode focus",
}


class _ModeSection(QWidget):
    """Une section (mode jeu OU mode focus) : cases du catalogue + sur mesure."""

    def __init__(self, mode: str, parent=None, *, manager=None) -> None:
        super().__init__(parent)
        self.mode = mode
        self.manager = manager if manager is not None else get_default_mode_manager()
        self.box: dict[str, QCheckBox] = {}  # nom canonique → case

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        heading = QLabel(f"{MODE_TITLES[mode]} — applications à fermer")
        heading.setStyleSheet("font-weight: bold; font-size: 15px;")
        layout.addWidget(heading)

        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(2)
        for index, app in enumerate(mode_apps.KNOWN_APPS):
            box = QCheckBox(app.label)
            box.setToolTip(
                "Cochez pour que le mode ferme cette application à son activation."
            )
            box.toggled.connect(
                lambda enabled, mode=mode, label=app.label: self._set_enabled(mode, label, enabled)
            )
            grid.addWidget(box, index // 2, index % 2)
            self.box[mode_apps.normalize_app_name(app.label)] = box
        layout.addLayout(grid)

        self.custom_label = QLabel()
        self.custom_label.setWordWrap(True)
        layout.addWidget(self.custom_label)

        add_row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Autre application (nom exact ou près) — Entrée pour ajouter")
        self.edit.returnPressed.connect(self._add_custom)
        add_button = QPushButton("Ajouter")
        add_button.clicked.connect(self._add_custom)
        add_row.addWidget(self.edit, 1)
        add_row.addWidget(add_button)
        layout.addLayout(add_row)

        self.feedback = QLabel("")
        self.feedback.setWordWrap(True)
        self.feedback.setStyleSheet("color: #9fe3b4;")
        layout.addWidget(self.feedback)
        self.refresh()

    def _set_enabled(self, mode: str, label: str, enabled: bool) -> None:
        try:
            result = self.manager.toggle_mode_app(mode, label, enabled)
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        if not result.get("success"):
            self.feedback.setText(f"Non enregistré : {result.get('error', 'erreur')}")
        # Relire l'état sauvegardé : la case reflète toujours la réalité.
        self.refresh()

    def _add_custom(self) -> None:
        text = self.edit.text().strip()
        if not text:
            return
        try:
            result = self.manager.toggle_mode_app(self.mode, text, True)
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        if result.get("success"):
            self.edit.clear()
            self.feedback.setText(f"« {text} » ajoutée au {MODE_TITLES[self.mode].lower()}.")
        else:
            self.feedback.setText(f"Non enregistré : {result.get('error', 'erreur')}")
        self.refresh()

    def refresh(self) -> None:
        selected = {
            mode_apps.normalize_app_name(entry)
            for entry in self.manager.get_mode_apps(self.mode)
        }
        for key, box in self.box.items():
            box.blockSignals(True)
            box.setChecked(key in selected)
            box.blockSignals(False)
        custom = [
            entry
            for entry in self.manager.get_mode_apps(self.mode)
            if mode_apps.find_app(entry) is None
        ]
        self.custom_label.setText(
            "Sur mesure : " + ", ".join(custom) if custom else ""
        )


class ModeAppsDialog(QDialog):
    def __init__(self, parent=None, *, manager=None) -> None:
        super().__init__(parent)
        if manager is not None:
            self.manager = manager
        else:
            self.manager = get_default_mode_manager()
        self.setWindowTitle("Jarvis — Applications des modes")
        self.resize(640, 620)
        self.setMinimumSize(460, 420)
        self.setStyleSheet(
            """
            QDialog { background: #0c1520; color: #e4f2ff; }
            QLabel { color: #e4f2ff; background: transparent; }
            QLabel#subtitle { color: #9bb2c8; }
            QLabel#title { font-size: 23px; font-weight: bold; }
            QScrollArea { border: none; background: #0c1520; }
            QWidget#content { background: #0c1520; }
            QFrame#section { background: #142332; border: 1px solid #294355;
                             border-radius: 10px; }
            QCheckBox { color: #e4f2ff; spacing: 8px; padding: 6px; }
            QCheckBox::indicator { width: 18px; height: 18px; }
            QLineEdit { background: #0f1c29; color: #e4f2ff;
                        border: 1px solid #294355; border-radius: 6px; padding: 7px; }
            QPushButton { background: #203e50; color: #e4f2ff; padding: 9px 18px;
                          border: 1px solid #417086; border-radius: 6px; }
            QPushButton:hover { background: #2b5265; }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        heading = QLabel("Applications des modes jeu et focus")
        heading.setObjectName("title")
        layout.addWidget(heading)
        subtitle = QLabel(
            "Choisissez quelles applications chaque mode ferme à son activation. "
            "Les deux listes sont indépendantes et enregistrées immédiatement : "
            "elles restent valables au prochain lancement de Jarvis. "
            "Vous pouvez aussi le faire à la voix : « dans le mode jeu, ne ferme pas "
            "Opera GX »."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 4, 8, 4)
        content_layout.setSpacing(14)

        self.sections = {}
        for mode in (MODE_GAME, MODE_FOCUS):
            frame = QFrame()
            frame.setObjectName("section")
            section_layout = QVBoxLayout(frame)
            section_layout.setContentsMargins(16, 14, 16, 14)
            section = _ModeSection(mode, manager=self.manager)
            self.sections[mode] = section
            section_layout.addWidget(section)
            content_layout.addWidget(frame)
        content_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        footer = QHBoxLayout()
        self.summary = QLabel()
        footer.addWidget(self.summary, 1)
        reset_button = QPushButton("Rétablir les valeurs d'origine")
        reset_button.clicked.connect(self._reset_defaults)
        footer.addWidget(reset_button)
        close_button = QPushButton("Fermer")
        close_button.clicked.connect(self.close)
        footer.addWidget(close_button)
        layout.addLayout(footer)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        game_count = len(self.manager.get_mode_apps(MODE_GAME))
        focus_count = len(self.manager.get_mode_apps(MODE_FOCUS))
        self.summary.setText(
            f"Mode jeu : {game_count} application(s) · Mode focus : {focus_count} application(s)"
        )

    def _reset_defaults(self) -> None:
        try:
            self.manager.reset_mode_apps(MODE_GAME)
            self.manager.reset_mode_apps(MODE_FOCUS)
        except Exception as exc:
            self.summary.setText(f"Réinitialisation impossible : {exc}")
            return
        self.sections[MODE_GAME].refresh()
        self.sections[MODE_FOCUS].refresh()
        self._refresh_summary()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self.refresh_all()

    def refresh_all(self) -> None:
        """Relit la configuration sauvegardée (voix, JSON, autres sessions)."""
        for section in self.sections.values():
            section.refresh()
        self._refresh_summary()


_DIALOG: ModeAppsDialog | None = None


def show_mode_apps_dialog(parent=None) -> ModeAppsDialog:
    """Une seule fenêtre partagée entre l'orbe et la zone de notification."""
    global _DIALOG
    if _DIALOG is None or not _DIALOG.isVisible():
        if _DIALOG is not None:
            # Fermée mais pas encore détruite : on la détruit proprement.
            # Le signal destroyed (lié à l'ancienne instance) remettra
            # _DIALOG à None sans toucher à une éventuelle nouvelle instance.
            _DIALOG.deleteLater()
            _DIALOG = None
        _DIALOG = ModeAppsDialog(parent)
        _DIALOG.setAttribute(Qt.WA_DeleteOnClose)
        # À la destruction effective, le singleton est remis à None — sans
        # écraser une nouvelle instance créée entre-temps.
        current = _DIALOG
        _DIALOG.destroyed.connect(lambda _=None, d=current: _forget_dialog(d))
    _DIALOG.refresh_all()
    _DIALOG.show()
    _DIALOG.raise_()
    _DIALOG.activateWindow()
    return _DIALOG


def _forget_dialog(instance=None) -> None:
    """Déréférence le singleton à la destruction de la fenêtre.

    La garde ``is`` évite qu'une destruction différée (deleteLater)
    n'écrase une instance déjà remplacée.
    """
    global _DIALOG
    if instance is None or _DIALOG is instance:
        _DIALOG = None
