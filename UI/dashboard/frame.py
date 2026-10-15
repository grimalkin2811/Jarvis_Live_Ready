"""Cadre d'un widget : en-tête, états, menu d'actions, poignées d'édition."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .painting import paint_panel
from .widget_api import (
    STATE_ERROR,
    STATE_LOADING,
    STATE_READY,
    STATE_UNAVAILABLE,
    DashboardWidget,
    WidgetSpec,
)

HEADER_HEIGHT = 34
GRIP = 16

STATE_TEXT = {
    STATE_LOADING: "Chargement…",
    STATE_ERROR: "Erreur",
    STATE_UNAVAILABLE: "Indisponible",
}


class _StateOverlay(QWidget):
    retry = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.addStretch(1)
        self.title = QLabel()
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setProperty("role", "title")
        self.message = QLabel()
        self.message.setAlignment(Qt.AlignCenter)
        self.message.setWordWrap(True)
        self.message.setProperty("role", "dim")
        self.button = QPushButton("Réessayer")
        self.button.clicked.connect(self.retry.emit)
        layout.addWidget(self.title)
        layout.addWidget(self.message)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.button)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        self.setAttribute(Qt.WA_StyledBackground, False)

    def show_state(self, state: str, message: str) -> None:
        self.title.setText(STATE_TEXT.get(state, state))
        role = {"error": "error", "unavailable": "warning"}.get(state, "dim")
        self.title.setProperty("role", role)
        self.title.style().unpolish(self.title)
        self.title.style().polish(self.title)
        self.message.setText(message or "")
        self.message.setVisible(bool(message))
        self.button.setVisible(state == STATE_ERROR)


class WidgetFrame(QWidget):
    """Conteneur visuel d'une instance de widget sur la grille."""

    move_started = Signal(object, QPoint)
    move_dragged = Signal(object, QPoint)
    move_finished = Signal(object, QPoint)
    resize_started = Signal(object, QPoint)
    resize_dragged = Signal(object, QPoint)
    resize_finished = Signal(object, QPoint)
    settings_requested = Signal(object)
    hide_requested = Signal(object)
    remove_requested = Signal(object)

    def __init__(self, placement, spec: WidgetSpec | None, widget: DashboardWidget | None, theme_manager,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.placement = placement
        self.spec = spec
        self.widget = widget
        self.theme_manager = theme_manager
        self.edit_mode = False
        self._highlight = 0.0
        self._drag_mode: str | None = None
        self._pulse_anim: QVariantAnimation | None = None
        self._panel_cache = None
        self._panel_cache_key = None
        #: Nombre de rendus COMPLETS du cadre (mesure de performance).
        self.paint_count = 0
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)

        outer = QVBoxLayout(self)
        theme = theme_manager.theme
        pad = theme.padding
        outer.setContentsMargins(pad, 6, pad, pad)
        outer.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(4 if theme.spec.shape != "rounded" else 24, 0, 0, 0)
        header.setSpacing(6)
        self.icon = QLabel(spec.icon if spec else "?")
        self.icon.setProperty("role", "dim")
        from .widgets.common import ElidedLabel

        self.title = ElidedLabel()
        self.title.setProperty("role", "title")
        self.subtitle = ElidedLabel()
        self.subtitle.setProperty("role", "dim")
        self.subtitle.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        header.addWidget(self.icon)
        header.addWidget(self.title, 0)
        header.addWidget(self.subtitle, 1)
        self.edit_buttons: list[QToolButton] = []
        for text, tip, signal in (("⚙", "Réglages du widget", self.settings_requested),
                                  ("◌", "Masquer (aucune donnée supprimée)", self.hide_requested),
                                  ("✕", "Retirer de la page (aucune donnée supprimée)", self.remove_requested)):
            button = QToolButton()
            button.setText(text)
            button.setToolTip(tip)
            button.setAutoRaise(True)
            button.setProperty("role", "frame")
            button.clicked.connect(lambda _=False, s=signal: s.emit(self))
            button.setVisible(False)
            header.addWidget(button)
            self.edit_buttons.append(button)
        self.menu_button = QToolButton()
        self.menu_button.setText("⋯")
        self.menu_button.setToolTip("Actions")
        self.menu_button.setAutoRaise(True)
        self.menu_button.setProperty("role", "frame")
        self.menu_button.clicked.connect(self._show_menu)
        header.addWidget(self.menu_button)
        self._header_widget = QWidget()
        self._header_widget.setFixedHeight(HEADER_HEIGHT - 6)
        self._header_widget.setLayout(header)
        outer.addWidget(self._header_widget)

        self.body = QWidget()
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        if widget is not None:
            body_layout.addWidget(widget)
            widget.state_changed.connect(self._on_state)
            widget.title_changed.connect(lambda text: self.subtitle.setText(text))
            widget.pulse.connect(self.pulse)
        else:
            missing = QLabel(
                f"Widget indisponible : « {placement.type} ».\n"
                "Il a peut-être été retiré de cette version. La disposition est conservée."
            )
            missing.setWordWrap(True)
            missing.setAlignment(Qt.AlignCenter)
            missing.setProperty("role", "dim")
            body_layout.addWidget(missing)
        outer.addWidget(self.body, 1)
        self.overlay = _StateOverlay(self.body)
        self.overlay.retry.connect(self._retry)
        self.overlay.hide()
        self._apply_title()
        if widget is not None:
            self._on_state(widget.state, widget.state_message)

    # -- présentation ---------------------------------------------------------------

    def _apply_title(self) -> None:
        theme = self.theme_manager.theme
        text = self.spec.title if self.spec else "Widget inconnu"
        font = self.title.font()
        font.setLetterSpacing(QFont.AbsoluteSpacing, float(theme.spec.title_spacing))
        self.title.setFont(font)
        self.title.setText(theme.title(text))
        self.title.updateGeometry()

    def apply_theme(self) -> None:
        theme = self.theme_manager.theme
        layout = self.layout()
        layout.setContentsMargins(theme.padding, 6, theme.padding, theme.padding)
        self._header_widget.layout().setContentsMargins(4 if theme.spec.shape != "rounded" else 24, 0, 0, 0)
        self._apply_title()
        if self.widget is not None:
            self.widget.on_theme_changed(theme)
        self.update()

    def set_edit_mode(self, enabled: bool) -> None:
        self.edit_mode = bool(enabled)
        for button in self.edit_buttons:
            button.setVisible(self.edit_mode)
        self._header_widget.setCursor(Qt.OpenHandCursor if self.edit_mode else Qt.ArrowCursor)
        self.update()

    def _on_state(self, state: str, message: str) -> None:
        overlay_states = getattr(self.widget, "overlay_states", (STATE_LOADING, STATE_ERROR, STATE_UNAVAILABLE))
        if state != STATE_READY and state in overlay_states:
            self.overlay.show_state(state, message)
            self.overlay.setGeometry(self.body.rect())
            self.overlay.show()
            self.overlay.raise_()
            if self.widget is not None:
                self.widget.setVisible(False)
        else:
            self.overlay.hide()
            if self.widget is not None:
                self.widget.setVisible(True)

    def _retry(self) -> None:
        if self.widget is not None:
            self.widget.refresh()

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        if self.overlay.isVisible():
            self.overlay.setGeometry(self.body.rect())

    def pulse(self) -> None:
        theme = self.theme_manager.theme
        if theme.transition_ms <= 0:
            return
        if self._pulse_anim is None:
            self._pulse_anim = QVariantAnimation(self)
            self._pulse_anim.valueChanged.connect(self._set_highlight)
        self._pulse_anim.stop()
        self._pulse_anim.setStartValue(1.0)
        self._pulse_anim.setEndValue(0.0)
        self._pulse_anim.setDuration(900 if theme.animation_level == "normal" else 1400)
        self._pulse_anim.start()

    def _set_highlight(self, value) -> None:
        self._highlight = float(value)
        self.update()

    def _panel_pixmap(self, theme):
        """Cadre peint mis en cache : un fond animé ou une mise à jour voisine
        ne redessine jamais dégradés et lueurs, seulement une copie de pixmap."""
        dpr = self.devicePixelRatioF()
        key = (self.width(), self.height(), dpr, theme.spec.id, theme.accent.name(), round(theme.glow, 3),
               theme.panel.alpha(), self.edit_mode, round(self._highlight, 2))
        if self._panel_cache is not None and self._panel_cache_key == key:
            return self._panel_cache
        pixmap = QPixmap(max(1, int(self.width() * dpr)), max(1, int(self.height() * dpr)))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        paint_panel(painter, QRectF(0, 0, self.width(), self.height()), theme, highlight=self._highlight,
                    header_height=HEADER_HEIGHT, edit_mode=self.edit_mode)
        painter.end()
        self._panel_cache, self._panel_cache_key = pixmap, key
        self.paint_count += 1
        return pixmap

    def paintEvent(self, event) -> None:  # noqa: N802 - API Qt
        painter = QPainter(self)
        theme = self.theme_manager.theme
        rect = QRectF(self.rect())
        painter.drawPixmap(0, 0, self._panel_pixmap(theme))
        if self.edit_mode:
            painter.setRenderHint(QPainter.Antialiasing, True)
            accent = QColor(theme.accent)
            painter.setPen(Qt.NoPen)
            painter.setBrush(accent)
            r = rect.adjusted(0, 0, -4, -4)
            painter.drawPolygon(QPolygonF([
                QPointF(r.right(), r.bottom() - GRIP),
                QPointF(r.right(), r.bottom()),
                QPointF(r.right() - GRIP, r.bottom()),
            ]))
            painter.setPen(QPen(QColor(0, 0, 0, 120), 1.0))
        painter.end()

    # -- menu d'actions ---------------------------------------------------------------

    def _show_menu(self) -> None:
        menu = QMenu(self)
        if self.widget is not None:
            for action in self.widget.actions():
                item = menu.addAction(action.label)
                item.setEnabled(action.enabled)
                if not action.enabled and action.reason:
                    item.setToolTip(action.reason)
                item.triggered.connect(lambda _=False, h=action.handler: h())
            if menu.actions():
                menu.addSeparator()
            menu.addAction("Actualiser").triggered.connect(self.widget.refresh)
        menu.addAction("Réglages du widget…").triggered.connect(lambda: self.settings_requested.emit(self))
        menu.addAction("Masquer").triggered.connect(lambda: self.hide_requested.emit(self))
        menu.addAction("Retirer de la page").triggered.connect(lambda: self.remove_requested.emit(self))
        menu.exec(self.menu_button.mapToGlobal(QPoint(0, self.menu_button.height())))

    # -- glisser / redimensionner (mode édition) -------------------------------------

    def _in_grip(self, pos: QPoint) -> bool:
        return pos.x() >= self.width() - GRIP - 6 and pos.y() >= self.height() - GRIP - 6

    def mousePressEvent(self, event) -> None:  # noqa: N802 - API Qt
        if not self.edit_mode or event.button() != Qt.LeftButton:
            return super().mousePressEvent(event)
        pos = event.position().toPoint()
        global_pos = event.globalPosition().toPoint()
        if self._in_grip(pos):
            self._drag_mode = "resize"
            self.resize_started.emit(self, global_pos)
        elif pos.y() <= HEADER_HEIGHT:
            self._drag_mode = "move"
            self.setCursor(Qt.ClosedHandCursor)
            self.move_started.emit(self, global_pos)
        else:
            return super().mousePressEvent(event)
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - API Qt
        if self.edit_mode and self._drag_mode is None:
            self.setCursor(Qt.SizeFDiagCursor if self._in_grip(event.position().toPoint()) else Qt.ArrowCursor)
        if self._drag_mode == "move":
            self.move_dragged.emit(self, event.globalPosition().toPoint())
            event.accept()
            return
        if self._drag_mode == "resize":
            self.resize_dragged.emit(self, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - API Qt
        mode, self._drag_mode = self._drag_mode, None
        self.unsetCursor()
        if mode == "move":
            self.move_finished.emit(self, event.globalPosition().toPoint())
            event.accept()
            return
        if mode == "resize":
            self.resize_finished.emit(self, event.globalPosition().toPoint())
            event.accept()
            return
        super().mouseReleaseEvent(event)
