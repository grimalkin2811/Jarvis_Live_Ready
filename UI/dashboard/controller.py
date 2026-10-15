"""Contrôleur du Dashboard : cycle de vie, points d'entrée, fermeture.

Créé une seule fois par ``src.ui.run_ui`` (léger : aucune fenêtre tant que
l'utilisateur ne l'ouvre pas). Il vit aussi longtemps que Jarvis :

* il reçoit les évènements vocaux RÉELS (présence, outils, transcriptions,
  niveaux) relayés par la boucle vocale — sans jamais toucher au moteur ;
* il fait vivre le hub, donc les notifications, même Dashboard fermé ;
* il expose les points d'entrée : icône de notification, raccourci de
  l'orbe (Ctrl+D), commande vocale (``UI.dashboard_bridge``), ``--dashboard``.

Ouvrir, fermer ou masquer le Dashboard, ou passer de l'orbe au Desktop,
ne redémarre jamais le moteur, ne réinitialise jamais la conversation et
n'interrompt jamais le micro ni les tâches.
"""

from __future__ import annotations

import copy
import logging
import time
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog

from src.runtime_status import RUNTIME

from . import DASHBOARD_NAME
from .config import (
    ConfigStore,
    add_custom_page,
    apply_preset,
    delete_custom_page,
    move_page,
    rename_page,
    save_custom_snapshot,
)
from .hub import DashboardHub
from .layout_model import Page
from .notification_center import LEGACY_TASK_DONE_TITLE
from .presets import BUILTIN_PAGE_INFO, builtin_pages
from .registry import default_registry
from .theme import ThemeManager

log = logging.getLogger("jarvis.dashboard")


def default_state_path() -> str:
    from src import paths

    return str(paths.ui_state_dir() / "dashboard_state.json")


class DashboardController(QObject):
    layout_changed = Signal()
    visibility_changed = Signal(bool)

    _bridge_command = Signal(str, object)
    _presence_relay = Signal(str)
    _voice_relay = Signal(str, object)

    def __init__(
        self,
        *,
        interface_controller=None,
        tray=None,
        notification_bridge=None,
        quit_callback: Callable[[], None] | None = None,
        state_path: str | None = None,
        runtime=RUNTIME,
        hub: DashboardHub | None = None,
        registry=None,
        dialog_executor: Callable[[QDialog], Any] | None = None,
        open_path: Callable[[str], bool] | None = None,
        reduced_motion_probe=None,
        native_sender: Callable[[str, str], Any] | None = None,
        attach_bridge: bool = True,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.store = ConfigStore(state_path if state_path is not None else default_state_path())
        kwargs = {"reduced_motion_probe": reduced_motion_probe} if reduced_motion_probe is not None else {}
        self.theme_manager = ThemeManager(self.store, self, **kwargs)
        self._registry = registry
        self.hub = hub or DashboardHub(self.store, parent=self, runtime=runtime)
        self.interface_controller = interface_controller
        self.tray = tray
        self.notification_bridge = notification_bridge
        self.quit_callback = quit_callback
        self.dialog_executor = dialog_executor
        self._open_path = open_path
        self.window = None
        self._quitting = False
        self._closed_reductions: dict[str, bool] = {}
        self._attach_bridge = attach_bridge
        self._last_energy_emit = 0.0
        self.open_count = 0

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(600)
        self._save_timer.timeout.connect(self.store.save)
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(800)
        self._geometry_timer.timeout.connect(self._save_geometry)
        self.store.add_listener(self._on_store_changed)

        self._bridge_command.connect(self._on_bridge_command, Qt.QueuedConnection)
        self._presence_relay.connect(self.hub.handle_presence, Qt.QueuedConnection)
        self._voice_relay.connect(self.hub.handle_voice_event, Qt.QueuedConnection)

        sender = native_sender
        if sender is None:
            def sender(title: str, message: str) -> None:
                from src import notifications

                notifications.publish(title, message, silent=True)
        self.hub.notifications.set_native_sender(sender)
        self.hub.notifications.set_foreground_probe(self._window_in_foreground)
        if notification_bridge is not None and hasattr(notification_bridge, "add_filter"):
            notification_bridge.add_filter(self._native_filter)
        if interface_controller is not None and hasattr(interface_controller, "mode_changed"):
            interface_controller.mode_changed.connect(self.hub.handle_interface_mode)
        if attach_bridge:
            from UI.dashboard_bridge import DASHBOARD

            DASHBOARD.set_handler(self._bridge_command.emit)
            DASHBOARD.report_state(visible=False, page=None)
        self.hub.start()
        self.hub.handle_interface_mode(self.current_interface_mode())
        self._report_load_status()
        self._schedule_update_check()

    @property
    def registry(self):
        """Registre des widgets, construit à la première ouverture (import des
        modules de widgets différé : le démarrage de Jarvis n'en paie pas le coût)."""
        if self._registry is None:
            self._registry = default_registry()
        return self._registry

    # ------------------------------------------------------------------
    # Persistance
    # ------------------------------------------------------------------

    def _on_store_changed(self, section: str) -> None:
        self._save_timer.start()
        if section in ("close",) and self.window is not None:
            self.window._update_status()

    def flush(self) -> bool:
        self._save_timer.stop()
        return self.store.save()

    def schedule_geometry_save(self) -> None:
        if self.window is not None and self.window.isVisible():
            self._geometry_timer.start()

    def _save_geometry(self) -> None:
        if self.window is not None:
            self.window.save_geometry()

    def _report_load_status(self) -> None:
        status = self.store.load_status
        notes = "; ".join(self.store.load_notes[:3])
        if status in ("recovered", "unreadable"):
            self.hub.notifications.notify(
                "intervention", "Préférences du Dashboard réinitialisées",
                notes or "Le fichier de préférences était illisible : valeurs par défaut utilisées.",
                level="warning", key="config:recovered",
            )
        if notes:
            self.hub.logs.record_event(f"Préférences : {status} — {notes}",
                                       level=logging.WARNING if status != "loaded" else logging.INFO,
                                       source="dashboard", category="ui")

    # ------------------------------------------------------------------
    # Entrées thread-safe (boucle vocale → Qt)
    # ------------------------------------------------------------------

    def handle_presence(self, state: str) -> None:
        self._presence_relay.emit(str(state or "hidden"))

    def handle_desktop_event(self, event: str, payload: dict | None = None) -> None:
        self._voice_relay.emit(str(event or ""), dict(payload or {}))

    def handle_voice_energy(self, level: float) -> None:
        now = time.monotonic()
        if now - self._last_energy_emit < 0.05:
            return
        self._last_energy_emit = now
        try:
            value = float(level)
        except (TypeError, ValueError):
            return
        self._voice_relay.emit("level", {"value": value, "source": "input"})

    @Slot(str, object)
    def _on_bridge_command(self, command: str, payload: dict) -> None:
        if command == "open":
            self.open((payload or {}).get("page"))
        elif command == "close":
            self.hide_window()
        elif command == "toggle":
            self.toggle()

    # ------------------------------------------------------------------
    # Fenêtre
    # ------------------------------------------------------------------

    def ensure_window(self):
        if self.window is None:
            from .window import DashboardWindow

            self.window = DashboardWindow(self)
            self.window.closing.connect(self.handle_close_event)
        return self.window

    def is_open(self) -> bool:
        return self.window is not None and self.window.isVisible()

    def open(self, page: str | None = None) -> bool:
        window = self.ensure_window()
        self._restore_closed_reductions()
        if page:
            from UI.dashboard_bridge import resolve_page

            target = resolve_page(page) or page
            if self.store.config.layout.page(target) is None:
                for candidate in self.store.config.layout.pages:
                    if candidate.title.lower() == str(page).strip().lower():
                        target = candidate.id
                        break
            window.open_page(target, animate=False)
        if not window.isVisible():
            window.show_restored()
        else:
            window.raise_()
            window.activateWindow()
        self.open_count += 1
        self._report_visibility(True)
        return True

    def hide_window(self) -> None:
        if self.window is not None and self.window.isVisible():
            self.window.save_geometry()
            self.window.hide()
        self._report_visibility(False)

    def toggle(self) -> None:
        if self.is_open():
            self.hide_window()
        else:
            self.open()

    def _report_visibility(self, visible: bool) -> None:
        try:
            from UI.dashboard_bridge import DASHBOARD

            DASHBOARD.report_state(visible=visible, page=self.window.current_page() if self.window else None)
        except Exception:
            pass
        self.visibility_changed.emit(visible)

    def _window_in_foreground(self) -> bool:
        window = self.window
        return bool(window is not None and window.isVisible() and window.isActiveWindow()
                    and not window.isMinimized())

    def _execute(self, dialog: QDialog):
        if self.dialog_executor is not None:
            return self.dialog_executor(dialog)
        return dialog.exec() == QDialog.Accepted

    @Slot(object)
    def handle_close_event(self, event) -> None:
        """Fermeture de la fenêtre selon le mode choisi (jamais un arrêt implicite)."""
        if self._quitting:
            event.accept()
            return
        event.ignore()
        mode = self.store.config.close.mode
        if mode == "shutdown":
            self.request_quit(from_close=True)
            return
        self.hide_window()
        if mode == "interactive":
            self._apply_closed_reductions()
        if not self.store.config.close.close_hint_shown:
            self.store.config.close.close_hint_shown = True
            self.store.changed("close")
            self._tray_message(f"{DASHBOARD_NAME} fermé", "Jarvis continue de fonctionner. Rouvrez le "
                               f"{DASHBOARD_NAME} depuis l'icône de notification ou avec Ctrl+D dans l'orbe.")

    def _tray_message(self, title: str, message: str) -> None:
        tray = self.tray
        try:
            if tray is not None:
                tray.showMessage(title, message)
        except Exception:
            pass

    def _apply_closed_reductions(self) -> None:
        options = self.store.config.close.interactive
        self._closed_reductions = {}
        if options.get("suspend_monitoring"):
            self.hub.resources.suspend(True)
            self._closed_reductions["monitoring"] = True
        if options.get("suspend_activity"):
            self.hub.logs.paused = True
            self._closed_reductions["activity"] = True
        if options.get("release_window") and self.window is not None:
            window, self.window = self.window, None
            window.closing.disconnect(self.handle_close_event)
            for canvas in window.all_canvases():
                canvas.set_active(False)
                canvas.dispose()
            window.deleteLater()
            self._closed_reductions["window"] = True
        self.hub.logs.record_event("Dashboard fermé (mode interactif) : services réduits "
                                   + (", ".join(self._closed_reductions) or "aucun"),
                                   source="dashboard", category="ui")

    def _restore_closed_reductions(self) -> None:
        if self._closed_reductions.pop("monitoring", False):
            self.hub.resources.suspend(False)
            self.hub.apply_monitoring_prefs()
        if self._closed_reductions.pop("activity", False):
            self.hub.logs.paused = False
        self._closed_reductions.clear()

    def closed_reductions(self) -> dict[str, bool]:
        return dict(self._closed_reductions)

    def active_task_titles(self) -> list[str]:
        from .tasks_service import ACTIVE_STATUSES, status_of

        return [t.title for t in self.hub.tasks.list_tasks() if status_of(t) in ACTIVE_STATUSES]

    def request_quit(self, from_close: bool = False) -> str:
        """Arrêt complet demandé : explication + confirmation (renforcée si tâches)."""
        from .dialogs import ShutdownDialog

        dialog = ShutdownDialog(self.active_task_titles(), self.window)
        result = self._execute(dialog)
        choice = dialog.choice if not isinstance(result, str) else result
        if choice == "quit":
            self.hub.logs.record_event("Arrêt complet demandé depuis le Dashboard", source="dashboard", category="ui")
            if self.quit_callback is not None:
                self.quit_callback()
        elif choice == "hide":
            self.hide_window()
        return choice

    # ------------------------------------------------------------------
    # Interfaces (orbe / Desktop)
    # ------------------------------------------------------------------

    def current_interface_mode(self) -> str:
        controller = self.interface_controller
        if controller is not None and hasattr(controller, "current_mode"):
            try:
                return str(controller.current_mode())
            except Exception:
                return ""
        try:
            from src import settings

            return settings.get_interface_mode()
        except Exception:
            return ""

    def set_interface_mode(self, mode: str) -> str:
        controller = self.interface_controller
        if controller is not None and hasattr(controller, "set_mode"):
            selected = controller.set_mode(mode)
        else:
            from UI.interface_mode_bridge import INTERFACE_MODE

            selected = INTERFACE_MODE.request(mode)
        self.hub.handle_interface_mode(selected)
        if self.window is not None:
            self.window._update_status()
        return selected

    # ------------------------------------------------------------------
    # Sécurité / services
    # ------------------------------------------------------------------

    def confirm(self, action_id: str, summary: str, details: str = "") -> bool:
        from .dialogs import ConfirmDialog

        security = self.hub.security
        level = security.effective_level(action_id)
        if level < 2:
            return True
        dialog = ConfirmDialog(level, security.policy(action_id).label, summary, details, self.window)
        return bool(self._execute(dialog))

    def open_path(self, path: str) -> bool:
        if self._open_path is not None:
            return bool(self._open_path(path))
        try:
            return bool(QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        except Exception:
            return False

    def attach_notification_bridge(self, bridge) -> None:
        """Branche le relais natif existant (anti-doublon sur la fin de tâche)."""
        if self.notification_bridge is not None and hasattr(self.notification_bridge, "remove_filter"):
            self.notification_bridge.remove_filter(self._native_filter)
        self.notification_bridge = bridge
        if bridge is not None and hasattr(bridge, "add_filter"):
            bridge.add_filter(self._native_filter)

    def _native_filter(self, title: str, message: str) -> bool:
        """Filtre du relais natif historique : la fin de tâche est gérée ici."""
        return title != LEGACY_TASK_DONE_TITLE

    # ------------------------------------------------------------------
    # Mise en page
    # ------------------------------------------------------------------

    def set_view_mode(self, mode: str) -> bool:
        """OnePage ou pages multiples (persisté) ; mêmes widgets et services."""
        if mode not in ("multipage", "onepage"):
            return False
        layout = self.store.config.layout
        if layout.view_mode == mode:
            return False
        layout.view_mode = mode
        self.store.changed("layout")
        self.layout_changed.emit()
        return True

    def apply_preset(self, preset: str) -> tuple[bool, str]:
        ok, message = apply_preset(self.store.config, preset)
        if ok:
            self.store.changed("layout")
            self.layout_changed.emit()
        return ok, message

    def save_custom_layout(self) -> None:
        save_custom_snapshot(self.store.config)
        self.store.changed("layout")

    def restore_pages(self, raw_pages: list[dict], preset: str) -> None:
        taken: set[str] = set()
        pages = [p for p in (Page.from_dict(raw, taken_ids=taken) for raw in raw_pages) if p is not None]
        for page in pages:
            page.builtin = page.id in BUILTIN_PAGE_INFO
        self.store.config.layout.pages = pages
        self.store.config.layout.preset = preset
        self.store.changed("layout")
        self.layout_changed.emit()

    def reset_page(self, page_id: str, preset: str) -> None:
        taken = self.store.config.layout.instance_ids()
        fresh = {p.id: p for p in builtin_pages(preset, taken)}
        page = self.store.config.layout.page(page_id)
        if page is None or page_id not in fresh:
            return
        page.widgets = fresh[page_id].widgets
        self.save_custom_layout()
        self.layout_changed.emit()

    def add_page(self, title: str):
        page, message = add_custom_page(self.store.config, title)
        if page is not None:
            self.store.changed("layout")
            self.layout_changed.emit()
        return page, message

    def rename_page(self, page_id: str, title: str):
        ok, message = rename_page(self.store.config, page_id, title)
        if ok:
            self.store.changed("layout")
            self.layout_changed.emit()
        return ok, message

    def delete_page(self, page_id: str):
        ok, message = delete_custom_page(self.store.config, page_id)
        if ok:
            self.store.changed("layout")
            self.layout_changed.emit()
        return ok, message

    def move_page(self, page_id: str, delta: int) -> bool:
        if move_page(self.store.config, page_id, delta):
            self.store.changed("layout")
            self.layout_changed.emit()
            return True
        return False

    # ------------------------------------------------------------------
    # Mises à jour (mécanisme existant : src.updater)
    # ------------------------------------------------------------------

    def _schedule_update_check(self) -> None:
        general = self.store.config.general
        if not general.auto_update_check:
            return
        last = general.last_update_check or 0.0
        if time.time() - last < 24 * 3600:
            return
        QTimer.singleShot(45_000, lambda: self.check_for_update(manual=False))

    def check_for_update(self, manual: bool = False) -> None:
        def _check():
            from src import updater
            from src.version import __version__

            return updater.build_update_plan(current=__version__)

        def _done(plan: dict) -> None:
            general = self.store.config.general
            general.last_update_check = time.time()
            self.store.changed("general")
            latest = str(plan.get("latest") or "")
            if plan.get("update_available"):
                if general.last_notified_version != latest:
                    general.last_notified_version = latest
                    self.hub.notifications.notify(
                        "update", "Mise à jour disponible",
                        f"Jarvis {latest} est publié (version actuelle {plan.get('current')}). Utilise le launcher "
                        "pour l'installer.", level="info", key=f"update:{latest}",
                    )
                if manual and self.window is not None:
                    self.window.toast(f"Mise à jour disponible : {latest}.", "info")
            elif manual and self.window is not None:
                self.window.toast(f"Jarvis est à jour ({plan.get('current')}).", "success")

        def _failed(exc: BaseException) -> None:
            self.hub.logs.record_event(f"Vérification des mises à jour impossible : {exc}", level=logging.WARNING,
                                       source="mises à jour", category="general")
            if manual and self.window is not None:
                self.window.toast(f"Vérification impossible : {exc}", "warning")

        self.hub.jobs.submit(_check, on_done=_done, on_error=_failed)

    # ------------------------------------------------------------------
    # Mesures / arrêt
    # ------------------------------------------------------------------

    def performance_snapshot(self) -> dict[str, Any]:
        stats = self.window.widget_stats() if self.window is not None else {
            "widgets": 0, "active_widgets": 0, "active_timers": 0, "background_animation": False}
        stats["pending_jobs"] = self.hub.jobs.pending()
        stats["resource_thread"] = self.hub.resources.running()
        stats["resource_consumers"] = self.hub.resource_consumers()
        stats["window_exists"] = self.window is not None
        try:
            import psutil

            stats["rss"] = int(psutil.Process().memory_info().rss)
        except Exception:
            stats["rss"] = 0
        return stats

    def maybe_open_on_launch(self) -> bool:
        if not self.store.config.general.open_on_launch:
            return False
        try:
            from src.modes import get_default_mode_manager

            if get_default_mode_manager().should_suppress_visuals():
                return False
        except Exception:
            pass
        return self.open()

    def shutdown(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        if self.window is not None:
            try:
                self.window.save_geometry()
                for canvas in self.window.all_canvases():
                    canvas.set_active(False)
                    canvas.dispose()
                self.window.close()
            except Exception:
                pass
        self.flush()
        if self._attach_bridge:
            try:
                from UI.dashboard_bridge import DASHBOARD

                DASHBOARD.set_handler(None)
            except Exception:
                pass
        if self.notification_bridge is not None and hasattr(self.notification_bridge, "remove_filter"):
            self.notification_bridge.remove_filter(self._native_filter)
        self.hub.shutdown()


def snapshot_pages(store: ConfigStore) -> list[dict]:
    return copy.deepcopy([p.to_dict() for p in store.config.layout.pages])
