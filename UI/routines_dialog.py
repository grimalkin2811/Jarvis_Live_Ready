"""Catalogue défilant : toutes les routines, un interrupteur par routine."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.routines import get_default_routine_manager
from src.scheduler import get_default_scheduler


class RoutinesDialog(QDialog):
    routines_changed = Signal()

    def __init__(self, parent=None, *, manager=None, scheduler=None) -> None:
        super().__init__(parent)
        self.manager = manager if manager is not None else get_default_routine_manager()
        self.scheduler = scheduler if scheduler is not None else get_default_scheduler()
        self.setWindowTitle("Jarvis — Routines")
        self.resize(720, 660)
        self.setMinimumSize(480, 380)
        self.setStyleSheet("""
            QDialog { background: #0c1520; color: #e4f2ff; }
            QLabel { color: #e4f2ff; background: transparent; }
            QLabel#subtitle, QLabel#schedule { color: #9bb2c8; }
            QLabel#title { font-size: 23px; font-weight: bold; }
            QScrollArea { border: none; background: #0c1520; }
            QWidget#routineList { background: #0c1520; }
            QFrame#routineCard { background: #142332; border: 1px solid #294355;
                                  border-radius: 10px; }
            QCheckBox { color: #e4f2ff; spacing: 8px; padding: 8px; }
            QCheckBox::indicator { width: 20px; height: 20px; }
            QPushButton { background: #203e50; color: #e4f2ff; padding: 9px 18px;
                          border: 1px solid #417086; border-radius: 6px; }
            QPushButton:hover { background: #2b5265; }
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        heading = QLabel("Vos routines, prêtes à l'emploi")
        heading.setObjectName("title")
        heading.setWordWrap(True)
        layout.addWidget(heading)
        subtitle = QLabel(
            "Activez ce qui vous est utile, Jarvis s'occupe du reste. "
            "Les horaires suivent l'heure locale du PC. Jarvis doit rester ouvert ; "
            "aucun compte ou réglage supplémentaire n'est nécessaire."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #ffcb8d;")
        layout.addWidget(self.warning)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.content = QWidget()
        self.content.setObjectName("routineList")
        self.cards = QVBoxLayout(self.content)
        self.cards.setContentsMargins(0, 4, 8, 4)
        self.cards.setSpacing(10)
        self.cards.addStretch()
        self.scroll.setWidget(self.content)
        layout.addWidget(self.scroll, 1)
        self.feedback = QLabel("Les choix sont sauvegardés automatiquement.")
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)
        footer = QHBoxLayout()
        footer.addStretch()
        close = QPushButton("Fermer")
        close.clicked.connect(self.close)
        footer.addWidget(close)
        layout.addLayout(footer)

        self.rows: dict[str, dict] = {}
        self._signature = None
        self._mtime = None
        self.refresh(force=True)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)

    def _make_row(self, routine: dict) -> None:
        name = routine["name"]
        card = QFrame()
        card.setObjectName("routineCard")
        box = QVBoxLayout(card)
        box.setContentsMargins(16, 12, 16, 12)
        header = QHBoxLayout()
        title = QLabel(name)
        title.setTextFormat(Qt.PlainText)
        title.setStyleSheet("font-weight: bold; font-size: 15px;")
        title.setWordWrap(True)
        header.addWidget(title, 1)
        toggle = QCheckBox("Activée")
        toggle.setAccessibleName(f"Activer ou désactiver {name}")
        toggle.setToolTip(
            "Enregistré immédiatement. L'activation autorise les prochains déclenchements."
        )
        header.addWidget(toggle)
        box.addLayout(header)
        description = QLabel()
        description.setTextFormat(Qt.PlainText)
        description.setWordWrap(True)
        box.addWidget(description)
        schedule = QLabel()
        schedule.setObjectName("schedule")
        schedule.setTextFormat(Qt.PlainText)
        schedule.setWordWrap(True)
        box.addWidget(schedule)
        self.rows[name] = {
            "card": card,
            "toggle": toggle,
            "description": description,
            "schedule": schedule,
        }
        toggle.toggled.connect(
            lambda enabled, name=name: self._set_enabled(name, enabled)
        )
        self.cards.insertWidget(self.cards.count() - 1, card)

    def refresh(self, force=False) -> None:
        if not force and not self.isVisible():
            return
        mtime = self.manager.mtime()
        ready = (
            self.scheduler.enabled
            and self.scheduler.available
            and self.scheduler.running
        )
        warning = (
            ""
            if ready
            else (
                "Le planificateur n'est pas en cours d'exécution. Les choix sont conservés, "
                "mais les horaires ne se déclencheront qu'avec Jarvis et son planificateur actifs."
            )
        )
        if not self.manager.enabled:
            warning = (
                "Les routines sont désactivées globalement (JARVIS_ROUTINES_ENABLED=0)."
            )
        self.warning.setText(warning)
        self.warning.setVisible(bool(warning))
        if not force and mtime == self._mtime and self.manager.enabled:
            return
        self._mtime = mtime
        result = self.manager.list_routines()
        if not result.get("success"):
            self.feedback.setText(
                result.get("error", "Lecture des routines impossible.")
            )
        elif self.manager.last_error:
            self.feedback.setText(self.manager.last_error)
        routines = result.get("routines", [])
        # Presets d'abord ; les routines personnelles restent aussi accessibles,
        # même au-delà des six raccourcis disponibles dans le menu radial.
        routines.sort(key=lambda item: not bool(item.get("preset_id")))
        signature = tuple(item["name"] for item in routines)
        if signature != self._signature:
            for row in self.rows.values():
                self.cards.removeWidget(row["card"])
                row["card"].deleteLater()
            self.rows.clear()
            for routine in routines:
                self._make_row(routine)
            self._signature = signature
        for routine in routines:
            row = self.rows[routine["name"]]
            toggle = row["toggle"]
            toggle.blockSignals(True)
            toggle.setChecked(routine["enabled"])
            toggle.setText("Activée" if routine["enabled"] else "Désactivée")
            toggle.blockSignals(False)
            row["description"].setText(routine["description"] or "Routine personnelle")
            planning = routine["planification"]
            if planning == "aucune":
                planning = "À la demande, par la voix ou le menu radial"
            row["schedule"].setText(planning)
        enabled = sum(item["enabled"] for item in routines)
        presets = sum(bool(item.get("preset_id")) for item in routines)
        self.summary.setText(
            f"{presets} préconfigurées · {enabled} / {len(routines)} activées"
        )

    def _set_enabled(self, name: str, enabled: bool) -> None:
        try:
            result = self.manager.update_routine(name, enabled=enabled)
        except Exception as exc:
            result = {"success": False, "error": str(exc)}
        if result.get("success"):
            state = (
                "activée — prochains déclenchements autorisés"
                if enabled
                else "désactivée"
            )
            self.feedback.setText(f"{name} : {state}.")
            self.routines_changed.emit()
        else:
            self.feedback.setText(
                f"Modification non enregistrée : {result.get('error', 'erreur inconnue')}"
            )
        # Relire l'état enregistré, y compris si l'écriture a échoué : ne pas
        # laisser un interrupteur vert qui ne correspond pas à la réalité.
        self.refresh(force=True)


_DIALOG: RoutinesDialog | None = None


def show_routines_dialog(parent=None) -> RoutinesDialog:
    """Un seul catalogue partagé entre l'orbe et la zone de notification."""
    global _DIALOG
    if _DIALOG is None:
        _DIALOG = RoutinesDialog(parent)
        _DIALOG.setAttribute(Qt.WA_DeleteOnClose)
        _DIALOG.destroyed.connect(_forget_dialog)
    _DIALOG.refresh(force=True)
    _DIALOG.show()
    _DIALOG.raise_()
    _DIALOG.activateWindow()
    return _DIALOG


def _forget_dialog(*_args) -> None:
    global _DIALOG
    _DIALOG = None
