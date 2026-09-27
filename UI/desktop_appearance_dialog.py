"""Éditeur d'apparence du Desktop Mode (v1.7.0).

Accessible depuis **Blob Mode → Appearance → « Desktop HUD »** et depuis
l'icône de notification (indispensable : en Desktop Mode il n'y a pas de menu
radial à l'écran).

La fenêtre suit exactement les conventions de ``UI/mode_apps_dialog.py`` :
fenêtre unique partagée, feuille de style sombre, **enregistrement immédiat**
de chaque modification, bouton « Rétablir les valeurs d'origine », et
``refresh_all()`` à chaque affichage.

Ce qu'on peut faire ici :

* activer / désactiver chaque élément (halo, état, transcription, réponse,
  outil, visualiseur, contrôles) ;
* le **déplacer à la souris** dans l'aperçu ;
* choisir sa **taille** ;
* choisir **dans quels états** il apparaît ;
* régler le halo (force, épaisseur), la politique de clic, les animations
  réduites et la taille du texte ;
* tout remettre à zéro.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from src import paths

from . import appearance_actions
from .desktop import config as cfg
from .desktop.design import state_color, profile_for
from .desktop.state import CONFIGURABLE_STATES, STATE_LABELS, DesktopState

# ---------------------------------------------------------------------------
# Application à chaud
# ---------------------------------------------------------------------------

_APPLY_HOOKS: list[Callable[[], None]] = []


def register_apply_hook(hook: Callable[[], None]) -> None:
    """Enregistre un rappel exécuté après chaque modification.

    ``src.ui`` y branche ``InterfaceModeController.refresh_overlay_appearance``
    pour que l'overlay en cours d'exécution se mette à jour immédiatement.
    """
    if hook is not None and hook not in _APPLY_HOOKS:
        _APPLY_HOOKS.append(hook)


def unregister_apply_hook(hook: Callable[[], None]) -> None:
    if hook in _APPLY_HOOKS:
        _APPLY_HOOKS.remove(hook)


def _notify_applied() -> None:
    for hook in list(_APPLY_HOOKS):
        try:
            hook()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Aperçu interactif
# ---------------------------------------------------------------------------


class DesktopPreview(QWidget):
    """Mini-écran : halo de l'état sélectionné + pastilles déplaçables."""

    moved = Signal(str, float, float)

    def __init__(self, state, parent=None) -> None:
        super().__init__(parent)
        self.appearance = state
        self.preview_state = DesktopState.LISTENING
        self.setMinimumHeight(220)
        self.setMouseTracking(True)
        self._dragging: str | None = None
        self._grab_offset = QPointF(0.0, 0.0)
        self.setCursor(Qt.ArrowCursor)
        self.setToolTip("Glissez un élément pour le repositionner.")

    # -- géométrie ----------------------------------------------------------

    def screen_rect(self) -> QRectF:
        margin = 16.0
        available = QRectF(self.rect()).adjusted(margin, margin, -margin, -margin)
        ratio = 16.0 / 10.0
        width = available.width()
        height = width / ratio
        if height > available.height():
            height = available.height()
            width = height * ratio
        return QRectF(
            available.center().x() - width / 2.0,
            available.center().y() - height / 2.0,
            width,
            height,
        )

    def _config(self) -> cfg.DesktopAppearanceConfig:
        return appearance_actions.desktop_config(self.appearance)

    def chip_rect(self, name: str) -> QRectF:
        screen = self.screen_rect()
        slot = self._config().slot(name)
        width = max(48.0, screen.width() * 0.19 * slot.scale)
        height = max(18.0, screen.height() * 0.075 * slot.scale)
        return QRectF(
            screen.left() + slot.x * screen.width() - width / 2.0,
            screen.top() + slot.y * screen.height() - height / 2.0,
            width,
            height,
        )

    def visible_chips(self) -> list[str]:
        config = self._config()
        return [
            name
            for name in cfg.WIDGET_ORDER
            if name != cfg.HALO and config.slot(name).enabled
        ]

    # -- interaction --------------------------------------------------------

    def _chip_at(self, point: QPointF) -> str | None:
        for name in reversed(self.visible_chips()):
            if self.chip_rect(name).contains(point):
                return name
        return None

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        point = event.position() if hasattr(event, "position") else QPointF(event.pos())
        name = self._chip_at(point)
        if name is None:
            return
        self._dragging = name
        rect = self.chip_rect(name)
        self._grab_offset = point - rect.center()
        self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        point = event.position() if hasattr(event, "position") else QPointF(event.pos())
        if self._dragging is None:
            self.setCursor(Qt.OpenHandCursor if self._chip_at(point) else Qt.ArrowCursor)
            return
        screen = self.screen_rect()
        if screen.width() <= 0 or screen.height() <= 0:
            return
        centre = point - self._grab_offset
        x = (centre.x() - screen.left()) / screen.width()
        y = (centre.y() - screen.top()) / screen.height()
        x, y = self._snap(x, y)
        self._config().set_slot(self._dragging, x=x, y=y)
        self.update()

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._dragging is None:
            return
        name, self._dragging = self._dragging, None
        self.setCursor(Qt.OpenHandCursor)
        slot = self._config().slot(name)
        self.moved.emit(name, slot.x, slot.y)

    @staticmethod
    def _snap(x: float, y: float) -> tuple[float, float]:
        """Aimantation douce sur la grille 3×3 : le placement reste net."""
        anchors = (0.06, 0.5, 0.94)
        for anchor in anchors:
            if abs(x - anchor) < 0.035:
                x = anchor
            if abs(y - anchor) < 0.035:
                y = anchor
        return max(0.03, min(0.97, x)), max(0.03, min(0.97, y))

    # -- rendu --------------------------------------------------------------

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing, True)
            screen = self.screen_rect()
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(6, 11, 17))
            painter.drawRoundedRect(screen, 10.0, 10.0)

            config = self._config()
            profile = profile_for(self.preview_state)
            accent = state_color(getattr(self.appearance, "glow_color", QColor(70, 180, 255)), profile)
            if config.slot(cfg.HALO).visible_in(self.preview_state):
                self._paint_halo(painter, screen, accent, profile, config)

            painter.setPen(QPen(QColor(48, 72, 94), 1.0))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(screen, 10.0, 10.0)

            for name in self.visible_chips():
                self._paint_chip(painter, name, accent, config)
        finally:
            painter.end()

    def _paint_halo(self, painter, screen, accent, profile, config) -> None:
        band = min(screen.width(), screen.height()) * 0.16 * config.thickness
        alpha = profile.alpha * config.intensity
        edges = (
            (QRectF(screen.left(), screen.top(), screen.width(), band), 0, 1),
            (QRectF(screen.right() - band, screen.top(), band, screen.height()), 1, 0),
            (QRectF(screen.left(), screen.bottom() - band, screen.width(), band), 0, -1),
            (QRectF(screen.left(), screen.top(), band, screen.height()), -1, 0),
        )
        for index, (rect, dx, dy) in enumerate(edges):
            weight = profile.edges[index]
            if weight <= 0.02:
                continue
            start = QPointF(
                rect.center().x() - dx * rect.width() / 2.0,
                rect.center().y() - dy * rect.height() / 2.0,
            )
            end = QPointF(
                rect.center().x() + dx * rect.width() / 2.0,
                rect.center().y() + dy * rect.height() / 2.0,
            )
            gradient = QLinearGradient(start, end)
            head = QColor(accent)
            head.setAlphaF(max(0.0, min(1.0, alpha * weight)))
            tail = QColor(accent)
            tail.setAlphaF(0.0)
            gradient.setColorAt(0.0, head)
            gradient.setColorAt(1.0, tail)
            painter.setPen(Qt.NoPen)
            painter.setBrush(gradient)
            painter.drawRect(rect)

    def _paint_chip(self, painter, name, accent, config) -> None:
        rect = self.chip_rect(name)
        slot = config.slot(name)
        active = slot.visible_in(self.preview_state)
        painter.setPen(Qt.NoPen)
        body = QColor(16, 26, 37)
        body.setAlphaF(0.95 if active else 0.45)
        painter.setBrush(body)
        painter.drawRoundedRect(rect, 7.0, 7.0)
        border = QColor(accent)
        border.setAlphaF(0.85 if active else 0.25)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(border, 1.4 if active else 1.0, Qt.SolidLine if active else Qt.DashLine))
        painter.drawRoundedRect(rect, 7.0, 7.0)
        label = cfg.WIDGET_LABELS[name][0]
        text = QColor(228, 242, 255)
        text.setAlphaF(0.95 if active else 0.45)
        painter.setPen(text)
        font = painter.font()
        font.setPointSizeF(max(7.0, rect.height() * 0.42))
        painter.setFont(font)
        painter.drawText(rect, Qt.AlignCenter, label)


# ---------------------------------------------------------------------------
# Ligne de réglage d'un élément
# ---------------------------------------------------------------------------


class _WidgetRow(QWidget):
    """Une ligne du tableau : activation, taille, visibilité par état."""

    changed = Signal()

    def __init__(self, name: str, config_getter, parent=None) -> None:
        super().__init__(parent)
        self.name = name
        self._config = config_getter
        label, hint = cfg.WIDGET_LABELS[name]

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(2)

        self.enabled = QCheckBox(label)
        self.enabled.setToolTip(hint)
        self.enabled.toggled.connect(self._on_enabled)
        layout.addWidget(self.enabled, 0, 0)

        self.scale = QSlider(Qt.Horizontal)
        self.scale.setRange(60, 200)
        self.scale.setToolTip("Taille de l'élément (%).")
        self.scale.setFixedWidth(120)
        self.scale.valueChanged.connect(self._on_scale)
        layout.addWidget(self.scale, 0, 1)

        self.scale_label = QLabel("100 %")
        self.scale_label.setObjectName("subtitle")
        layout.addWidget(self.scale_label, 0, 2)

        self.state_boxes: dict[str, QCheckBox] = {}
        states_row = QHBoxLayout()
        states_row.setContentsMargins(22, 0, 0, 0)
        states_row.setSpacing(10)
        for state in CONFIGURABLE_STATES:
            box = QCheckBox(STATE_LABELS[state])
            box.setToolTip(f"Afficher « {label} » pendant l'état « {STATE_LABELS[state]} ».")
            box.toggled.connect(
                lambda checked, s=state: self._on_state(s, checked)
            )
            states_row.addWidget(box)
            self.state_boxes[state] = box
        states_row.addStretch(1)
        layout.addLayout(states_row, 1, 0, 1, 3)

        if name == cfg.HALO:
            # Le halo est la présence elle-même : sa position n'a pas de sens
            # (il occupe tout le périmètre).
            self.scale.setEnabled(False)
            self.scale_label.setText("—")

    # -- lecture / écriture -------------------------------------------------

    def refresh(self) -> None:
        slot = self._config().slot(self.name)
        for widget in (self.enabled, self.scale, *self.state_boxes.values()):
            widget.blockSignals(True)
        self.enabled.setChecked(slot.enabled)
        self.scale.setValue(int(round(slot.scale * 100)))
        if self.name != cfg.HALO:
            self.scale_label.setText(f"{int(round(slot.scale * 100))} %")
        for state, box in self.state_boxes.items():
            box.setChecked(state in slot.states)
            box.setEnabled(slot.enabled)
        for widget in (self.enabled, self.scale, *self.state_boxes.values()):
            widget.blockSignals(False)
        self.scale.setEnabled(slot.enabled and self.name != cfg.HALO)

    def _on_enabled(self, checked: bool) -> None:
        self._config().set_slot(self.name, enabled=bool(checked))
        self.refresh()
        self.changed.emit()

    def _on_scale(self, value: int) -> None:
        self._config().set_slot(self.name, scale=value / 100.0)
        self.scale_label.setText(f"{value} %")
        self.changed.emit()

    def _on_state(self, state: str, checked: bool) -> None:
        self._config().set_state_visibility(self.name, state, bool(checked))
        self.changed.emit()


# ---------------------------------------------------------------------------
# Fenêtre
# ---------------------------------------------------------------------------


class DesktopAppearanceDialog(QDialog):
    """Fenêtre de personnalisation du Desktop Mode."""

    def __init__(self, parent=None, *, state=None, path=None) -> None:
        super().__init__(parent)
        self._path = str(path) if path else str(paths.appearance_state_file())
        self.appearance = state if state is not None else appearance_actions.load_state(self._path)

        self.setWindowTitle("Jarvis — Apparence du Desktop Mode")
        self.resize(760, 760)
        self.setMinimumSize(560, 520)
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
            QCheckBox { color: #e4f2ff; spacing: 8px; padding: 4px; }
            QCheckBox::indicator { width: 16px; height: 16px; }
            QComboBox { background: #0f1c29; color: #e4f2ff; border: 1px solid #294355;
                        border-radius: 6px; padding: 6px; }
            QSlider::groove:horizontal { height: 4px; background: #294355; border-radius: 2px; }
            QSlider::handle:horizontal { background: #6fc4ff; width: 14px;
                                         margin: -6px 0; border-radius: 7px; }
            QPushButton { background: #203e50; color: #e4f2ff; padding: 9px 18px;
                          border: 1px solid #417086; border-radius: 6px; }
            QPushButton:hover { background: #2b5265; }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        heading = QLabel("Apparence du Desktop Mode")
        heading.setObjectName("title")
        layout.addWidget(heading)

        subtitle = QLabel(
            "Choisissez ce que Jarvis affiche sur votre bureau, où, et dans quels états. "
            "Tout est enregistré immédiatement et appliqué à l'overlay en cours. "
            "Par défaut, rien n'intercepte vos clics."
        )
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        # -- aperçu ---------------------------------------------------------
        preview_frame = QFrame()
        preview_frame.setObjectName("section")
        preview_layout = QVBoxLayout(preview_frame)
        preview_layout.setContentsMargins(16, 12, 16, 12)

        header = QHBoxLayout()
        header.addWidget(QLabel("Aperçu — glissez les éléments pour les déplacer"))
        header.addStretch(1)
        header.addWidget(QLabel("État :"))
        self.state_combo = QComboBox()
        for state in CONFIGURABLE_STATES:
            self.state_combo.addItem(STATE_LABELS[state], state)
        self.state_combo.currentIndexChanged.connect(self._on_preview_state)
        header.addWidget(self.state_combo)
        preview_layout.addLayout(header)

        self.preview = DesktopPreview(self.appearance)
        self.preview.moved.connect(lambda *_: self._save())
        preview_layout.addWidget(self.preview, 1)
        layout.addWidget(preview_frame, 1)

        # -- éléments ---------------------------------------------------------
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName("content")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 4, 8, 4)
        content_layout.setSpacing(12)

        elements = QFrame()
        elements.setObjectName("section")
        elements_layout = QVBoxLayout(elements)
        elements_layout.setContentsMargins(16, 14, 16, 14)
        elements_layout.setSpacing(6)
        elements_layout.addWidget(QLabel("Éléments affichés"))
        self.rows: dict[str, _WidgetRow] = {}
        for name in cfg.WIDGET_ORDER:
            row = _WidgetRow(name, self._config)
            row.changed.connect(self._save)
            elements_layout.addWidget(row)
            self.rows[name] = row
        content_layout.addWidget(elements)

        # -- réglages globaux --------------------------------------------------
        globals_frame = QFrame()
        globals_frame.setObjectName("section")
        globals_layout = QGridLayout(globals_frame)
        globals_layout.setContentsMargins(16, 14, 16, 14)
        globals_layout.setHorizontalSpacing(12)
        globals_layout.setVerticalSpacing(8)
        globals_layout.addWidget(QLabel("Réglages généraux"), 0, 0, 1, 3)

        globals_layout.addWidget(QLabel("Clics de la souris"), 1, 0)
        self.interaction = QComboBox()
        for mode in cfg.INTERACTION_MODES:
            self.interaction.addItem(cfg.INTERACTION_LABELS[mode], mode)
        self.interaction.setToolTip(
            "« Toujours traversable » : rien ne capte la souris.\n"
            "« Widgets interactifs uniquement » (défaut) : seuls les contrôles captent.\n"
            "« Overlay interactif » : tout l'écran capte — à réserver aux cas particuliers."
        )
        self.interaction.currentIndexChanged.connect(self._on_interaction)
        globals_layout.addWidget(self.interaction, 1, 1, 1, 2)

        self.intensity = self._slider(globals_layout, 2, "Force du halo", 30, 160)
        self.thickness = self._slider(globals_layout, 3, "Épaisseur du halo", 50, 180)
        self.text_scale = self._slider(globals_layout, 4, "Taille du texte", 80, 180)

        self.reduced_motion = QCheckBox("Animations réduites (accessibilité)")
        self.reduced_motion.setToolTip(
            "Conserve tous les états mais réduit fortement les mouvements."
        )
        self.reduced_motion.toggled.connect(self._on_reduced_motion)
        globals_layout.addWidget(self.reduced_motion, 5, 0, 1, 3)

        self.audio_reactive = QCheckBox("Réagir au niveau audio (voix et réponse)")
        self.audio_reactive.toggled.connect(self._on_audio_reactive)
        globals_layout.addWidget(self.audio_reactive, 6, 0, 1, 3)

        content_layout.addWidget(globals_frame)
        content_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 2)

        # -- pied de page ------------------------------------------------------
        self.summary = QLabel("")
        self.summary.setObjectName("subtitle")
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)

        footer = QHBoxLayout()
        reset_button = QPushButton("Rétablir les valeurs d'origine")
        reset_button.clicked.connect(self._reset_defaults)
        close_button = QPushButton("Fermer")
        close_button.clicked.connect(self.close)
        footer.addWidget(reset_button)
        footer.addStretch(1)
        footer.addWidget(close_button)
        layout.addLayout(footer)

        self.refresh_all()

    # -- utilitaires ---------------------------------------------------------

    def _slider(self, layout: QGridLayout, row: int, label: str, low: int, high: int) -> QSlider:
        layout.addWidget(QLabel(label), row, 0)
        slider = QSlider(Qt.Horizontal)
        slider.setRange(low, high)
        slider.valueChanged.connect(self._on_global_slider)
        layout.addWidget(slider, row, 1)
        value_label = QLabel("100 %")
        value_label.setObjectName("subtitle")
        layout.addWidget(value_label, row, 2)
        slider.setProperty("value_label", value_label)
        return slider

    def _config(self) -> cfg.DesktopAppearanceConfig:
        return appearance_actions.desktop_config(self.appearance)

    # -- callbacks -----------------------------------------------------------

    def _on_preview_state(self, index: int) -> None:
        state = self.state_combo.itemData(index)
        if state:
            self.preview.preview_state = state
            self.preview.update()

    def _on_interaction(self, index: int) -> None:
        value = self.interaction.itemData(index)
        if value:
            self._config().interaction = cfg.normalize_interaction(value)
            self._save()

    def _on_reduced_motion(self, checked: bool) -> None:
        self._config().reduced_motion = bool(checked)
        self._save()

    def _on_audio_reactive(self, checked: bool) -> None:
        self._config().audio_reactive = bool(checked)
        self._save()

    def _on_global_slider(self, _value: int) -> None:
        config = self._config()
        config.intensity = self.intensity.value() / 100.0
        config.thickness = self.thickness.value() / 100.0
        config.text_scale = self.text_scale.value() / 100.0
        for slider in (self.intensity, self.thickness, self.text_scale):
            label = slider.property("value_label")
            if label is not None:
                label.setText(f"{slider.value()} %")
        self._save()

    def _reset_defaults(self) -> None:
        self._config().reset()
        self.refresh_all()
        self._save()

    # -- persistance ---------------------------------------------------------

    def _save(self) -> None:
        appearance_actions.save_state(self.appearance, self._path)
        self._refresh_summary()
        self.preview.update()
        _notify_applied()

    def _refresh_summary(self) -> None:
        config = self._config()
        enabled = [cfg.WIDGET_LABELS[name][0] for name in config.enabled_widgets()]
        interaction = cfg.INTERACTION_LABELS[config.interaction]
        clicks = "aucun clic intercepté" if not config.has_interactive_widgets() else "contrôles cliquables"
        self.summary.setText(
            f"Affiché : {', '.join(enabled) if enabled else 'rien'} · "
            f"Souris : {interaction} ({clicks})"
        )

    # -- rafraîchissement ----------------------------------------------------

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.refresh_all()

    def refresh_all(self) -> None:
        """Relit la configuration enregistrée et remet tous les contrôles à jour."""
        config = self._config()
        for row in self.rows.values():
            row.refresh()
        for widget in (self.interaction, self.reduced_motion, self.audio_reactive,
                       self.intensity, self.thickness, self.text_scale):
            widget.blockSignals(True)
        index = self.interaction.findData(config.interaction)
        if index >= 0:
            self.interaction.setCurrentIndex(index)
        self.reduced_motion.setChecked(config.reduced_motion)
        self.audio_reactive.setChecked(config.audio_reactive)
        self.intensity.setValue(int(round(config.intensity * 100)))
        self.thickness.setValue(int(round(config.thickness * 100)))
        self.text_scale.setValue(int(round(config.text_scale * 100)))
        for slider in (self.intensity, self.thickness, self.text_scale):
            label = slider.property("value_label")
            if label is not None:
                label.setText(f"{slider.value()} %")
        for widget in (self.interaction, self.reduced_motion, self.audio_reactive,
                       self.intensity, self.thickness, self.text_scale):
            widget.blockSignals(False)
        self._refresh_summary()
        self.preview.update()


_DIALOG: DesktopAppearanceDialog | None = None


def show_desktop_appearance_dialog(parent=None, *, state=None, path=None) -> DesktopAppearanceDialog:
    """Une seule fenêtre partagée entre l'orbe et la zone de notification."""
    global _DIALOG
    if _DIALOG is None or not _DIALOG.isVisible():
        if _DIALOG is not None:
            _DIALOG.deleteLater()
            _DIALOG = None
        _DIALOG = DesktopAppearanceDialog(parent, state=state, path=path)
        _DIALOG.setAttribute(Qt.WA_DeleteOnClose)
        current = _DIALOG
        _DIALOG.destroyed.connect(lambda _=None, d=current: _forget_dialog(d))
    _DIALOG.refresh_all()
    _DIALOG.show()
    _DIALOG.raise_()
    _DIALOG.activateWindow()
    return _DIALOG


def _forget_dialog(instance=None) -> None:
    """Déréférence le singleton à la destruction de la fenêtre."""
    global _DIALOG
    if instance is None or _DIALOG is instance:
        _DIALOG = None
