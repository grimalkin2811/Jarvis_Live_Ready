"""Fenêtre principale du Dashboard."""

from __future__ import annotations

import copy
import math
import time

from PySide6.QtCore import (
    QByteArray,
    QEasingCurve,
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import DASHBOARD_NAME
from .layout_model import Placement, find_free_slot, new_instance_id, size_class_for_width
from .painting import BackgroundRenderer
from .pages_view import OnePageView, PageView
from .presets import PRESET_LABELS
from .widget_api import WidgetContext
from .widgets.common import StatusDot

PRESENCE_TEXT = {"listening": "À l'écoute", "thinking": "Réfléchit", "speaking": "Parle",
                 "hidden": "En veille", "loading": "Initialisation"}
HEALTH_STATUS = {"operational": "ok", "degraded": "warning", "unavailable": "error", "unknown": "unknown"}


class DashboardRoot(QWidget):
    """Fond peint (couche statique en cache + animation optionnelle)."""

    def __init__(self, theme_manager, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("DashboardRoot")
        self.theme_manager = theme_manager
        self.renderer = BackgroundRenderer()
        self.energy = 0.0
        self._start = time.monotonic()
        self.frames_painted = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)

    def update_animation(self, running: bool) -> None:
        fps = self.theme_manager.theme.background_fps
        if running and fps > 0:
            self.timer.setInterval(int(1000 / fps))
            if not self.timer.isActive():
                self.timer.start()
        else:
            self.timer.stop()
            self.update()

    def theme_changed(self) -> None:
        self.renderer.invalidate()
        self.update()

    def _tick(self) -> None:
        self.energy *= 0.92
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        theme = self.theme_manager.theme
        phase = time.monotonic() - self._start
        self.renderer.paint(painter, self.size(), theme, phase, energy=self.energy, dpr=self.devicePixelRatioF())
        painter.end()
        self.frames_painted += 1


class Emblem(QWidget):
    """Emblème animé du thème (anneaux, viseur tactique, goutte de verre).

    Seule animation continue du niveau « normal » : elle ne repeint que ses
    34×34 pixels (coût négligeable) et s'arrête fenêtre masquée.
    """

    def __init__(self, theme_manager, parent=None) -> None:
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.setFixedSize(34, 34)
        self.energy = 0.0
        self._start = time.monotonic()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update)

    def set_running(self, running: bool) -> None:
        fps = self.theme_manager.theme.emblem_fps
        if running and fps > 0:
            self.timer.setInterval(int(1000 / fps))
            if not self.timer.isActive():
                self.timer.start()
        else:
            self.timer.stop()
            self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        theme = self.theme_manager.theme
        phase = (time.monotonic() - self._start) if self.timer.isActive() else 0.0
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        accent = theme.accent
        shape = theme.spec.shape
        if shape == "rounded":
            p.setPen(QPen(accent, 1.2))
            p.drawEllipse(r.adjusted(6, 6, -6, -6))
            pen = QPen(accent, 2.2)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            start = int((phase * 120.0) % 360 * 16)
            p.drawArc(r, start, 110 * 16)
            p.drawArc(r, start + 180 * 16, 110 * 16)
            p.setBrush(accent)
            p.setPen(Qt.NoPen)
            p.drawEllipse(r.center(), 3.0 + 1.5 * self.energy, 3.0 + 1.5 * self.energy)
        elif shape == "chamfered":
            p.setPen(QPen(accent, 2.0))
            c = r.center()
            p.drawLine(r.topLeft(), c)
            p.drawLine(r.topRight(), c)
            p.drawLine(r.bottomLeft(), r.bottomRight())
            box = r.adjusted(9, 15, -9, -3)
            p.setPen(QPen(theme.accent2, 1.2))
            p.drawRect(box)
            if self.timer.isActive():
                y = box.top() + (phase * 9.0) % max(1.0, box.height())
                p.setPen(QPen(accent, 1.0))
                p.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))
        else:
            breath = 0.5 + 0.5 * math.sin(phase * 2.2) if self.timer.isActive() else 0.6
            glow = QColor(accent)
            glow.setAlpha(int(60 + 80 * breath))
            p.setPen(Qt.NoPen)
            p.setBrush(glow)
            p.drawEllipse(r.center(), r.width() / 2.0, r.height() / 2.0)
            core = QColor(accent)
            core.setAlpha(200)
            p.setBrush(core)
            p.drawEllipse(r.center(), r.width() / 2.0 - 4, r.height() / 2.0 - 4)
            p.setBrush(QColor(255, 255, 255, 90))
            p.drawEllipse(QRectF(r.left() + 8, r.top() + 7, 8, 5))
        p.end()


class StatusPill(QPushButton):
    def __init__(self, theme_manager, parent=None) -> None:
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.dot = StatusDot(theme_manager, size=10)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 2, 12, 2)
        layout.addWidget(self.dot)
        self.text = QLabel()
        layout.addWidget(self.text)
        self.setMinimumHeight(30)
        self.setFlat(True)
        self.setCursor(Qt.PointingHandCursor)

    compact = False

    def set_state(self, status: str, text: str) -> None:
        self.dot.set_status(status)
        self.setToolTip(text + " — cliquer pour les diagnostics")
        self.text.setText(text.split(" · ")[0] if self.compact else text)
        self.setMinimumWidth(self.text.sizeHint().width() + 48)


class ToastLayer(QWidget):
    def __init__(self, theme_manager, parent=None) -> None:
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.addStretch(1)
        self.history: list[tuple[str, str]] = []

    def show_toast(self, message: str, level: str = "info") -> None:
        self.history.append((message, level))
        self.history = self.history[-50:]
        theme = self.theme_manager.theme
        color = {"success": theme.success, "error": theme.danger, "warning": theme.warning}.get(level, theme.accent)
        toast = QLabel(message)
        toast.setWordWrap(True)
        toast.setFixedWidth(max(220, min(380, self.width() - 8)))
        toast.setStyleSheet(
            f"background: {theme.panel_alt.name()}; border: 1px solid {color.name()}; border-left: 4px solid "
            f"{color.name()}; border-radius: 6px; padding: 8px 12px; color: {theme.text.name()};"
        )
        self._layout.addWidget(toast, 0, Qt.AlignRight)
        while self._layout.count() > 5:
            item = self._layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
        QTimer.singleShot(4200, toast.deleteLater)
        self.raise_()


class DashboardWindow(QMainWindow):
    """Fenêtre redimensionnable : navigation, pages, OnePage, édition."""

    closing = Signal(object)  # QCloseEvent

    def __init__(self, controller, parent=None) -> None:
        super().__init__(parent)
        self.controller = controller
        self.store = controller.store
        self.theme_manager = controller.theme_manager
        self.hub = controller.hub
        self.registry = controller.registry
        self.setWindowTitle(f"Jarvis — {DASHBOARD_NAME}")
        self.setMinimumSize(QSize(560, 420))
        self.resize(1360, 860)
        self.edit_mode = False
        self._edit_backup = None
        self._page_views: dict[str, PageView] = {}
        self._onepage: OnePageView | None = None
        self._current_page = self.store.config.window.last_page or "accueil"
        self._size_class = ""
        self._pre_fullscreen_state = "normal"

        self.root = DashboardRoot(self.theme_manager)
        self.setCentralWidget(self.root)
        outer = QVBoxLayout(self.root)
        outer.setContentsMargins(14, 10, 14, 12)
        outer.setSpacing(10)
        outer.addWidget(self._build_header())
        self.edit_bar = self._build_edit_bar()
        self.edit_bar.hide()
        outer.addWidget(self.edit_bar)
        body = QHBoxLayout()
        body.setSpacing(12)
        self.sidebar = self._build_sidebar()
        body.addWidget(self.sidebar)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        outer.addLayout(body, 1)
        self.toasts = ToastLayer(self.theme_manager, self.root)

        self._install_shortcuts()
        self.theme_manager.changed.connect(self._on_theme)
        # Méthode liée (et non lambda) : le hub survit à la fenêtre (mode de
        # fermeture interactif, fenêtre libérée) ; PySide6 ne déconnecte
        # automatiquement que les slots dont le récepteur est un QObject.
        self.hub.engine_changed.connect(self._on_engine_changed)
        self.hub.presence_changed.connect(self._on_presence)
        self.hub.level_changed.connect(self._on_level)
        self.hub.notifications_changed.connect(self._update_bell)
        self.hub.notification_added.connect(self._on_notification)
        self.controller.layout_changed.connect(self.rebuild_pages)
        self._apply_stylesheet()
        self.rebuild_pages()
        self._update_status()
        self._update_bell()
        self._restore_geometry()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("header")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(10)
        self.emblem = Emblem(self.theme_manager)
        self.title = QLabel()
        self.title.setProperty("role", "title")
        self.page_title = QLabel()
        self.page_title.setProperty("role", "dim")
        layout.addWidget(self.emblem)
        layout.addWidget(self.title)
        layout.addWidget(self.page_title)
        layout.addStretch(1)
        self.status_pill = StatusPill(self.theme_manager)
        self.status_pill.setToolTip("État global de Jarvis — cliquer pour les diagnostics")
        self.status_pill.clicked.connect(lambda: self.open_page("systeme"))
        layout.addWidget(self.status_pill)
        layout.addStretch(1)
        self.view_button = QToolButton()
        self.view_button.setCheckable(True)
        self.view_button.setToolTip("Basculer OnePage / pages multiples (Ctrl+O)")
        self.view_button.clicked.connect(self._toggle_view)
        self.edit_button = QToolButton()
        self.edit_button.setText("✎ Éditer")
        self.edit_button.setCheckable(True)
        self.edit_button.setToolTip("Mode édition (Ctrl+E)")
        self.edit_button.clicked.connect(lambda checked: self.set_edit_mode(checked))
        self.bell = QToolButton()
        self.bell.setToolTip("Notifications du Dashboard")
        self.bell.clicked.connect(self._show_notifications_menu)
        self.full_button = QToolButton()
        self.full_button.setText("⤢")
        self.full_button.setToolTip("Plein écran (F11)")
        self.full_button.clicked.connect(self.toggle_fullscreen)
        self.settings_button = QToolButton()
        self.settings_button.setText("⚙")
        self.settings_button.setToolTip("Paramètres")
        self.settings_button.clicked.connect(lambda: self.open_page("parametres"))
        for widget in (self.view_button, self.edit_button, self.bell, self.full_button, self.settings_button):
            layout.addWidget(widget)
        return header

    def _build_sidebar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("sidebar")
        bar.setFixedWidth(196)
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.nav_layout = QVBoxLayout()
        self.nav_layout.setSpacing(3)
        layout.addLayout(self.nav_layout)
        layout.addStretch(1)
        self.nav_group = QButtonGroup(self)
        self.nav_group.setExclusive(True)
        self.nav_buttons: dict[str, QPushButton] = {}
        self.interfaces_caption = QLabel("Interfaces")
        self.interfaces_caption.setProperty("role", "dim")
        layout.addWidget(self.interfaces_caption)
        self.orb_button = QPushButton("●  Orbe")
        self.orb_button.setProperty("role", "nav")
        self.orb_button.setToolTip("Passer en mode orbe (la voix et les tâches continuent)")
        self.orb_button.clicked.connect(lambda: self.controller.set_interface_mode("blob"))
        self.desktop_button = QPushButton("◫  Desktop")
        self.desktop_button.setProperty("role", "nav")
        self.desktop_button.setToolTip("Passer en mode Desktop (la voix et les tâches continuent)")
        self.desktop_button.clicked.connect(lambda: self.controller.set_interface_mode("desktop"))
        layout.addWidget(self.orb_button)
        layout.addWidget(self.desktop_button)
        self.close_mode_label = QLabel()
        self.close_mode_label.setProperty("role", "dim")
        self.close_mode_label.setWordWrap(True)
        layout.addWidget(self.close_mode_label)
        return bar

    def _build_edit_bar(self) -> QWidget:
        bar = QFrame()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 0, 4, 0)
        info = QLabel("Mode édition : glisse l'en-tête d'un widget pour le déplacer, le coin pour le redimensionner.")
        info.setProperty("role", "dim")
        add = QPushButton("+ Ajouter un widget")
        add.setProperty("role", "primary")
        add.clicked.connect(self._add_widget)
        self.hidden_button = QPushButton("Widgets masqués")
        self.hidden_button.clicked.connect(self._show_hidden_menu)
        reset = QPushButton("Réinitialiser la page")
        reset.clicked.connect(self._reset_page)
        cancel = QPushButton("Annuler")
        cancel.clicked.connect(lambda: self.set_edit_mode(False, save=False))
        save = QPushButton("Enregistrer")
        save.setProperty("role", "primary")
        save.clicked.connect(lambda: self.set_edit_mode(False, save=True))
        layout.addWidget(info, 1)
        for widget in (add, self.hidden_button, reset, cancel, save):
            layout.addWidget(widget)
        return bar

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence(Qt.Key_F11), self, activated=self.toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl+E"), self, activated=lambda: self.set_edit_mode(not self.edit_mode))
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self._toggle_view)
        QShortcut(QKeySequence(Qt.Key_Escape), self, activated=self._escape)
        QShortcut(QKeySequence("Ctrl+W"), self, activated=self.close)
        for index in range(9):
            QShortcut(QKeySequence(f"Ctrl+{index + 1}"), self, activated=lambda i=index: self._open_index(i))

    # ------------------------------------------------------------------
    # Thème / état
    # ------------------------------------------------------------------

    def _apply_stylesheet(self) -> None:
        from .theme import stylesheet

        theme = self.theme_manager.theme
        self.setStyleSheet(stylesheet(theme))
        self.title.setText(theme.title(f"Jarvis · {DASHBOARD_NAME}"))
        self.root.theme_changed()
        self.root.update_animation(self.isVisible())
        self.emblem.set_running(self.isVisible())

    def _on_theme(self, _theme) -> None:
        self._apply_stylesheet()
        for canvas in self.all_canvases():
            canvas.apply_theme()
        self._update_status()

    def _on_engine_changed(self, _snapshot) -> None:
        self._update_status()

    def _update_status(self) -> None:
        snap = self.hub.engine()
        health = snap.get("health", "unknown")
        presence = PRESENCE_TEXT.get(self.hub.presence(), self.hub.presence())
        self.status_pill.set_state(HEALTH_STATUS.get(health, "unknown"),
                                   f"{snap.get('health_label', 'Inconnu')} · {snap.get('engine_label', '')} · {presence}")
        mode = self.controller.current_interface_mode()
        self.orb_button.setEnabled(mode != "blob")
        self.desktop_button.setEnabled(mode != "desktop")
        close_mode = self.store.config.close.mode
        self.close_mode_label.setText({"persistent": "Fermeture : Jarvis continue",
                                       "interactive": "Fermeture : Jarvis continue (services réduits)",
                                       "shutdown": "Fermeture : quitte Jarvis"}.get(close_mode, ""))

    def _on_presence(self, state: str) -> None:
        if state == "speaking":
            self.root.energy = max(self.root.energy, 0.6)
        self.emblem.energy = 1.0 if state == "speaking" else 0.0
        self._update_status()

    def _on_level(self, value: float, source: str) -> None:
        if self.theme_manager.theme.animation_level == "pousse":
            self.root.energy = max(self.root.energy, min(1.0, value * 1.4))

    def _update_bell(self) -> None:
        unread = self.hub.notifications.unread_count()
        self.bell.setText(f"✉ {unread}" if unread else "✉")

    def _show_notifications_menu(self) -> None:
        menu = QMenu(self)
        items = list(reversed(self.hub.notifications.items()))[:8]
        for item in items:
            stamp = time.strftime("%H:%M", time.localtime(item.created_at))
            action = menu.addAction(f"{'●' if not item.read else '○'} {stamp}  {item.title} — {item.message[:70]}")
            action.setEnabled(False)
        if not items:
            menu.addAction("Aucune notification").setEnabled(False)
        menu.addSeparator()
        menu.addAction("Tout marquer comme lu").triggered.connect(self.hub.notifications.mark_all_read)
        target = next((p.id for p in self.store.config.layout.pages
                       if any(w.type == "system.notifications" for w in p.widgets)), "systeme")
        menu.addAction("Voir toutes les notifications").triggered.connect(lambda: self.open_page(target))
        menu.exec(self.bell.mapToGlobal(QPoint(0, self.bell.height())))

    def _on_notification(self, notification) -> None:
        if notification is not None and notification.in_dashboard and self.isVisible():
            self.toast(f"{notification.title} — {notification.message}", notification.level)

    def toast(self, message: str, level: str = "info") -> None:
        self.toasts.show_toast(message, level)
        self._place_toasts()

    def _place_toasts(self) -> None:
        width = min(420, self.root.width() - 40)
        self.toasts.setGeometry(self.root.width() - width - 24, 80, width, self.root.height() - 110)
        self.toasts.raise_()

    # ------------------------------------------------------------------
    # Pages
    # ------------------------------------------------------------------

    def context_factory(self, page_id: str):
        def _make(placement: Placement) -> WidgetContext:
            return WidgetContext(
                hub=self.hub,
                theme_manager=self.theme_manager,
                instance_id=placement.id,
                page_id=page_id,
                confirm=self.controller.confirm,
                toast=self.toast,
                open_page=self.open_page,
                open_path=self.controller.open_path,
                controller=self.controller,
            )
        return _make

    def all_canvases(self):
        canvases = []
        for view in self._page_views.values():
            canvases.extend(view.canvases())
        if self._onepage is not None:
            canvases.extend(self._onepage.canvases())
        return canvases

    def rebuild_pages(self) -> None:
        """Reconstruit navigation et vues (changement de pages / de préréglage)."""
        for view in list(self._page_views.values()):
            view.set_active(False)
            view.dispose()
            self.stack.removeWidget(view)
            view.deleteLater()
        self._page_views.clear()
        if self._onepage is not None:
            self._onepage.set_active(False)
            self._onepage.dispose()
            self.stack.removeWidget(self._onepage)
            self._onepage.deleteLater()
            self._onepage = None
        for button in list(self.nav_buttons.values()):
            self.nav_group.removeButton(button)
            self.nav_layout.removeWidget(button)
            button.hide()
            button.setParent(None)
            button.deleteLater()
        self.nav_buttons.clear()
        layout = self.store.config.layout
        pages = [p for p in layout.pages if not p.extras.get("_hidden_overflow")]
        for index, page in enumerate(pages):
            button = QPushButton(f"{page.icon}  {page.title}")
            button.setProperty("role", "nav")
            button.setCheckable(True)
            button.setToolTip(f"{page.title} (Ctrl+{index + 1})" if index < 9 else page.title)
            button.clicked.connect(lambda _=False, pid=page.id: self.open_page(pid))
            self.nav_layout.addWidget(button)
            self.nav_group.addButton(button)
            self.nav_buttons[page.id] = button
        onepage = layout.view_mode == "onepage"
        self.view_button.setChecked(onepage)
        self.view_button.setText("▤ OnePage" if not onepage else "▦ Pages")
        self.sidebar.setVisible(True)
        if layout.page(self._current_page) is None:
            self._current_page = "accueil"
        if onepage:
            self._onepage = OnePageView(pages, self.registry, self.theme_manager, self._onepage_context(), self.stack)
            self.stack.addWidget(self._onepage)
            self.stack.setCurrentWidget(self._onepage)
            if self.edit_mode:
                for canvas in self._onepage.canvases():
                    canvas.set_edit_mode(True)
                    self._wire_canvas(canvas)
            else:
                for canvas in self._onepage.canvases():
                    self._wire_canvas(canvas)
            self._onepage.set_active(self.isVisible())
            self.page_title.setText("OnePage")
        else:
            self.open_page(self._current_page, animate=False)
        self._apply_size_class(force=True)

    def _onepage_context(self):
        def _make(placement: Placement) -> WidgetContext:
            page_id = next((p.id for p in self.store.config.layout.pages if p.find(placement.id)), "")
            return self.context_factory(page_id)(placement)
        return _make

    def _wire_canvas(self, canvas) -> None:
        canvas.layout_edited.connect(self._on_layout_edited)
        canvas.settings_requested.connect(self._edit_settings)
        canvas.hide_requested.connect(lambda p, c=canvas: self._hide_widget(c, p))
        canvas.remove_requested.connect(lambda p, c=canvas: self._remove_widget(c, p))

    def _view_for(self, page_id: str) -> PageView | None:
        view = self._page_views.get(page_id)
        if view is None:
            page = self.store.config.layout.page(page_id)
            if page is None:
                return None
            view = PageView(page, self.registry, self.theme_manager, self.context_factory(page_id), self.stack)
            self._wire_canvas(view.canvas)
            view.canvas.set_edit_mode(self.edit_mode)
            self.stack.addWidget(view)
            self._page_views[page_id] = view
        return view

    def current_page(self) -> str:
        return self._current_page

    def open_page(self, page_id: str, animate: bool = True) -> bool:
        layout = self.store.config.layout
        page = layout.page(page_id)
        if page is None:
            return False
        self._current_page = page_id
        if self.store.config.window.last_page != page_id:
            self.store.config.window.last_page = page_id
            self.store.changed("window")
        if self._onepage is not None:
            self._onepage.scroll_to_page(page_id)
            return True
        view = self._view_for(page_id)
        previous = self.stack.currentWidget()
        for other in self._page_views.values():
            if other is not view:
                other.set_active(False)
        self.stack.setCurrentWidget(view)
        view.set_active(self.isVisible())
        button = self.nav_buttons.get(page_id)
        if button is not None:
            button.setChecked(True)
        self.page_title.setText(f"/ {page.title}")
        if animate and previous is not view:
            self._animate_in(view)
        return True

    def _open_index(self, index: int) -> None:
        pages = [p for p in self.store.config.layout.pages if not p.extras.get("_hidden_overflow")]
        if 0 <= index < len(pages):
            self.open_page(pages[index].id)

    def _animate_in(self, widget: QWidget) -> None:
        theme = self.theme_manager.theme
        duration = theme.transition_ms
        if duration <= 0:
            return
        effect = QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", widget)
        fade.setDuration(duration)
        fade.setStartValue(0.0)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        fade.finished.connect(lambda: widget.setGraphicsEffect(None))
        fade.start(QPropertyAnimation.DeleteWhenStopped)
        style = theme.spec.transition
        if style in ("slide", "rise"):
            start = widget.pos() + (QPoint(42, 0) if style == "slide" else QPoint(0, 18))
            move = QPropertyAnimation(widget, b"pos", widget)
            move.setDuration(duration)
            move.setStartValue(start)
            move.setEndValue(widget.pos())
            move.setEasingCurve(QEasingCurve.OutCubic)
            move.start(QPropertyAnimation.DeleteWhenStopped)

    def _toggle_view(self) -> None:
        mode = "multipage" if self.store.config.layout.view_mode == "onepage" else "onepage"
        self.controller.set_view_mode(mode)

    def onepage_order(self) -> list[str]:
        return self._onepage.section_order() if self._onepage is not None else []

    # ------------------------------------------------------------------
    # Mode édition
    # ------------------------------------------------------------------

    def set_edit_mode(self, enabled: bool, save: bool = True) -> None:
        enabled = bool(enabled)
        if enabled == self.edit_mode:
            self.edit_button.setChecked(enabled)
            return
        if enabled:
            self._edit_backup = copy.deepcopy([p.to_dict() for p in self.store.config.layout.pages])
            self._edit_preset = self.store.config.layout.preset
        else:
            if save:
                self.controller.save_custom_layout()
                self.toast("Disposition enregistrée (préréglage « Personnalisée »).", "success")
            elif self._edit_backup is not None:
                self.controller.restore_pages(self._edit_backup, self._edit_preset)
                self.toast("Modifications annulées.", "info")
            self._edit_backup = None
        self.edit_mode = enabled
        self.edit_button.setChecked(enabled)
        self.edit_bar.setVisible(enabled)
        for canvas in self.all_canvases():
            canvas.set_edit_mode(enabled)

    def _current_canvas(self):
        if self._onepage is not None:
            canvases = self._onepage.canvases()
            for canvas in canvases:
                if canvas.page.id == self._current_page:
                    return canvas
            return canvases[0] if canvases else None
        view = self._page_views.get(self._current_page)
        return view.canvas if view else None

    def _on_layout_edited(self) -> None:
        if not self.edit_mode:
            self.controller.save_custom_layout()

    def add_widget(self, type_id: str, page_id: str | None = None) -> Placement | None:
        page = self.store.config.layout.page(page_id or self._current_page)
        spec = self.registry.spec(type_id)
        if page is None or spec is None or not self.registry.instances_allowed(type_id, page):
            return None
        w, h = spec.default_size
        x, y = find_free_slot(page.widgets, w, h)
        placement = Placement(new_instance_id(self.store.config.layout.instance_ids()), type_id, x, y, w, h,
                              False, spec.validate_settings({}))
        page.widgets.append(placement)
        for canvas in self.all_canvases():
            if canvas.page is page:
                canvas.rebuild()
        if not self.edit_mode:
            self.controller.save_custom_layout()
        return placement

    def _add_widget(self) -> None:
        from .dialogs import WidgetLibraryDialog

        page = self.store.config.layout.page(self._current_page)
        if page is None:
            return
        dialog = WidgetLibraryDialog(self.registry, page, self)
        if dialog.exec() and dialog.selected:
            placement = self.add_widget(dialog.selected)
            if placement is None:
                self.toast("Ce widget ne peut pas être ajouté à cette page.", "warning")

    def _hide_widget(self, canvas, placement: Placement) -> None:
        placement.hidden = True
        canvas.rebuild()
        self.toast("Widget masqué (rien n'est supprimé) — « Widgets masqués » pour le réafficher.", "info")
        if not self.edit_mode:
            self.controller.save_custom_layout()

    def _remove_widget(self, canvas, placement: Placement) -> None:
        canvas.page.widgets = [p for p in canvas.page.widgets if p.id != placement.id]
        canvas.rebuild()
        self.toast("Widget retiré de la page (aucune donnée supprimée).", "info")
        if not self.edit_mode:
            self.controller.save_custom_layout()

    def _show_hidden_menu(self) -> None:
        menu = QMenu(self)
        for canvas in self.all_canvases():
            for placement in canvas.page.widgets:
                if placement.hidden:
                    spec = self.registry.spec(placement.type)
                    action = menu.addAction(f"{canvas.page.title} — {spec.title if spec else placement.type}")
                    action.triggered.connect(lambda _=False, c=canvas, p=placement: self._unhide(c, p))
        if not menu.actions():
            menu.addAction("Aucun widget masqué").setEnabled(False)
        menu.exec(self.hidden_button.mapToGlobal(QPoint(0, self.hidden_button.height())))

    def _unhide(self, canvas, placement: Placement) -> None:
        placement.hidden = False
        x, y = find_free_slot([p for p in canvas.page.widgets if p.id != placement.id], placement.w, placement.h)
        placement.x, placement.y = x, y
        canvas.rebuild()

    def _reset_page(self) -> None:
        page = self.store.config.layout.page(self._current_page)
        if page is None or not page.builtin:
            self.toast("Seules les pages intégrées ont une disposition par défaut.", "warning")
            return
        preset = self.store.config.layout.preset
        if preset == "personnalisee":
            preset = "cockpit"
        if not self.controller.confirm("layout.reset", f"Réinitialiser « {page.title} » ?",
                                       f"Disposition du préréglage « {PRESET_LABELS.get(preset)} » ; aucune donnée "
                                       "supprimée."):
            return
        self.controller.reset_page(page.id, preset)

    def _edit_settings(self, placement: Placement) -> None:
        from .dialogs import WidgetSettingsDialog

        spec = self.registry.spec(placement.type)
        if spec is None:
            return
        dialog = WidgetSettingsDialog(spec, placement.settings, self)
        if dialog.exec():
            placement.settings = spec.validate_settings(dialog.values)
            for canvas in self.all_canvases():
                frame = canvas.frames.get(placement.id)
                if frame is not None and frame.widget is not None:
                    frame.widget.apply_settings(placement.settings)
            if not self.edit_mode:
                self.store.changed("layout")
            self.toast("Réglages du widget appliqués.", "success")

    # ------------------------------------------------------------------
    # Fenêtre
    # ------------------------------------------------------------------

    def _escape(self) -> None:
        if self.isFullScreen():
            self.toggle_fullscreen()
        elif self.edit_mode:
            self.set_edit_mode(False, save=False)

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            if self._pre_fullscreen_state == "maximized":
                self.showMaximized()
            else:
                self.showNormal()
            self.store.config.window.state = self._pre_fullscreen_state
        else:
            self._pre_fullscreen_state = "maximized" if self.isMaximized() else "normal"
            self.showFullScreen()
            self.store.config.window.state = "fullscreen"
        self.store.changed("window")

    def _restore_geometry(self) -> None:
        prefs = self.store.config.window
        if prefs.geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(prefs.geometry.encode("ascii")))
            except Exception:
                pass

    def save_geometry(self) -> None:
        prefs = self.store.config.window
        if not self.isFullScreen():
            prefs.geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
            prefs.state = "maximized" if self.isMaximized() else "normal"
        self.store.changed("window")

    def show_restored(self) -> None:
        state = self.store.config.window.state
        if state == "fullscreen":
            self._pre_fullscreen_state = "normal"
            self.showFullScreen()
        elif state == "maximized":
            self.showMaximized()
        else:
            self.show()
        self.raise_()
        self.activateWindow()

    def _apply_size_class(self, force: bool = False) -> None:
        size_class = size_class_for_width(self.width())
        if size_class == self._size_class and not force:
            return
        self._size_class = size_class
        narrow = size_class == "narrow"
        self.sidebar.setFixedWidth(64 if narrow else 196)
        for page_id, button in self.nav_buttons.items():
            page = self.store.config.layout.page(page_id)
            if page is not None:
                button.setText(page.icon if narrow else f"{page.icon}  {page.title}")
        self.orb_button.setText("●" if narrow else "●  Orbe")
        self.desktop_button.setText("◫" if narrow else "◫  Desktop")
        self.close_mode_label.setVisible(not narrow)
        self.interfaces_caption.setVisible(not narrow)
        self.page_title.setVisible(not narrow)
        self.title.setVisible(not narrow)
        self.edit_button.setText("✎" if narrow else "✎ Éditer")
        onepage = self.store.config.layout.view_mode == "onepage"
        if narrow:
            self.view_button.setText("▦" if onepage else "▤")
        else:
            self.view_button.setText("▦ Pages" if onepage else "▤ OnePage")
        self.status_pill.compact = narrow
        self._update_status()

    def size_class(self) -> str:
        return self._size_class

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_size_class()
        self._place_toasts()
        self.controller.schedule_geometry_save()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._set_views_active(True)
        self.root.update_animation(True)
        self.emblem.set_running(True)
        self._apply_size_class(force=True)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._set_views_active(False)
        self.root.update_animation(False)
        self.emblem.set_running(False)

    def _set_views_active(self, active: bool) -> None:
        if self._onepage is not None:
            self._onepage.set_active(active)
            return
        view = self._page_views.get(self._current_page)
        for page_id, other in self._page_views.items():
            other.set_active(active and other is view)

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == event.Type.WindowStateChange:
            minimized = bool(self.windowState() & Qt.WindowMinimized)
            self._set_views_active(not minimized and self.isVisible())
            self.root.update_animation(not minimized and self.isVisible())
            self.emblem.set_running(not minimized and self.isVisible())

    def closeEvent(self, event) -> None:  # noqa: N802
        self.closing.emit(event)

    # ------------------------------------------------------------------
    # Mesures
    # ------------------------------------------------------------------

    def widget_stats(self) -> dict:
        widgets = 0
        active = 0
        timers = 0
        for canvas in self.all_canvases():
            for frame in canvas.frames.values():
                if frame.widget is None:
                    continue
                widgets += 1
                if frame.widget.is_active:
                    active += 1
                timers += frame.widget.active_timer_count()
        if self.root.timer.isActive():
            timers += 1
        if self.emblem.timer.isActive():
            timers += 1
        return {"widgets": widgets, "active_widgets": active, "active_timers": timers,
                "background_animation": self.root.timer.isActive(),
                "emblem_animation": self.emblem.timer.isActive()}
