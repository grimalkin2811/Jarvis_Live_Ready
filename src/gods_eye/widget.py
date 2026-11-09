"""Hôte Qt natif du module God's Eye View (QWebEngineView).

Le contrôleur (``controller.py``) reste Qt-free ; ce module-ci ne fait que
traduire entre les deux mondes :

* création de la fenêtre sur le **thread UI** (``request_open`` est sûr depuis
  n'importe quel thread : la création passe par un signal queued) ;
* transport : ``page.runJavaScript("window.jarvisWorld.handle(<json>)", cb)`` ;
* fermeture propre : ``closeEvent`` → destruction de la page →
  ``controller.notify_destroyed()``.

Si QtWebEngine ou le build web sont absents, ``request_open`` renvoie False et
les outils ``world_*`` répondent ``world_unavailable`` (jamais de crash, jamais
de lancement externe silencieux).
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from .controller import get_default_controller

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
WEB_DIR = REPO_ROOT / "web" / "gods_eye_view"
DIST_INDEX = WEB_DIR / "dist" / "index.html"


def web_build_available() -> bool:
    return DIST_INDEX.is_file()


def _load_webengine():
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
        from PySide6.QtWebEngineCore import QWebEnginePage  # noqa: F401
        return True
    except Exception:
        return False


def webengine_available() -> bool:
    return _load_webengine()


def dist_index_url():
    from PySide6.QtCore import QUrl

    return QUrl.fromLocalFile(str(DIST_INDEX))


class _PageTransport:
    """Transport synchrone (vus du contrôleur) reposant sur runJavaScript."""

    def __init__(self) -> None:
        self._handler = None
        self._page = None
        self._window = None
        self._ready = False

    # -- API Controller -------------------------------------------------- #
    def set_response_handler(self, handler) -> None:
        self._handler = handler

    def send(self, payload: str) -> bool:
        page = self._page
        if page is None:
            return False
        try:
            # payload est du JSON : expression JS valide (objet littéral).
            page.runJavaScript(f"window.jarvisWorld.handle({payload})", self._deliver)
            return True
        except Exception:
            log.exception("God's Eye View : runJavaScript a échoué")
            return False

    def close(self) -> None:
        self._page = None
        self._handler = None
        self._ready = False

    # -- Câblage Qt ------------------------------------------------------ #
    def attach(self, window, page) -> None:
        from PySide6.QtCore import Qt

        self._window = window
        self._page = page
        page.setBackgroundColor(Qt.GlobalColor.black)
        page.loadFinished.connect(self._on_load_finished)
        page.renderProcessTerminated.connect(self._on_render_process_terminated)

    def _on_load_finished(self, ok: bool) -> None:
        page = self._page
        if page is None:
            return
        if not ok:
            log.error("God's Eye View : chargement de la page échoué")
            return
        page.runJavaScript(
            "window.jarvisWorld && window.jarvisWorld.ready ? 'ready' : 'missing'",
            self._on_ready_probe,
        )

    def _on_ready_probe(self, result) -> None:
        if result == "ready":
            self._ready = True
            get_default_controller().notify_ready()
            log.info("God's Eye View : module prêt")
        else:
            log.error("God's Eye View : window.jarvisWorld absent — build web cassé ?")

    def _on_render_process_terminated(self, termination_status, exit_code) -> None:
        log.error("God's Eye View : processus de rendu terminé (%s, code %s)",
                  termination_status, exit_code)
        controller = get_default_controller()
        # Détruit proprement côté contrôleur ; la fenêtre affichera l'erreur.
        controller.notify_destroyed()
        if self._window is not None:
            self._window.set_banner(
                "Le moteur d'affichage s'est interrompu. Fermez la fenêtre "
                "(God's Eye View) et rouvrez le module."
            )

    def _deliver(self, result) -> None:
        handler = self._handler
        if handler is not None:
            handler(result)



def _create_window_class():
    from PySide6.QtCore import Qt
    from PySide6.QtWebEngineCore import QWebEnginePage
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWidgets import (
        QLabel,
        QMainWindow,
    )

    class GodsEyeViewWindow(QMainWindow):
        def __init__(self, controller):
            super().__init__()
            self._controller = controller
            self.setWindowTitle("God's Eye View — Jarvis")
            self.setMinimumSize(1024, 640)
            self.resize(1280, 720)

            self._banner = QLabel("", self)
            self._banner.setStyleSheet(
                "background:#2a0f14;color:#fda4af;padding:6px 10px;"
                "font-family:'Cascadia Mono',Consolas,monospace;font-size:11px;"
            )
            self._banner.hide()

            self._view = QWebEngineView(self)
            self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            page = QWebEnginePage(self._view)
            self._view.setPage(page)
            self.setCentralWidget(self._view)

            self._transport = _PageTransport()
            self._transport.attach(self, page)
            self._controller.attach_transport(self._transport)
            self._install_host_channel(page)
            page.setUrl(dist_index_url())

        def set_banner(self, text: str) -> None:
            self._banner.setText(text)
            self._banner.show()

        def _install_host_channel(self, page) -> None:
            try:
                from PySide6.QtCore import QObject, Slot as _Slot
                from PySide6.QtWebChannel import QWebChannel
            except Exception:
                return  # channel optionnel : Échap reste sans effet

            window = self

            class HostObject(QObject):
                @_Slot()
                def closeRequested(self) -> None:
                    window.close()

            self._host_channel = QWebChannel(self._view)
            self._host_channel.registerObject("jarvisHost", HostObject())
            page.setWebChannel(self._host_channel)

        def closeEvent(self, event) -> None:
            # Destruction propre : plus aucun callback ne touche la page.
            self._transport.close()
            self._controller.notify_destroyed()
            super().closeEvent(event)

    return GodsEyeViewWindow


class _OpenBridge:
    """Singleton vivant sur le thread UI — reçoit les demandes d'ouverture."""

    def __init__(self, app) -> None:
        from PySide6.QtCore import QObject, Signal, Slot

        class _Bridge(QObject):
            openRequested = Signal()

            def __init__(self, outer):
                super().__init__()
                self._outer = outer
                self.window = None
                self.waiters: list[tuple[threading.Event, dict]] = []

            @Slot()
            def _open(self) -> None:
                try:
                    if self.window is None:
                        window_cls = _create_window_class()
                        controller = get_default_controller()
                        self.window = window_cls(controller)
                        self.window.destroyed.connect(self._on_window_destroyed)
                    self.window.show()
                    self.window.raise_()
                    self.window.activateWindow()
                    ok = True
                except Exception:
                    log.exception("God's Eye View : création de la fenêtre impossible")
                    self.window = None
                    ok = False
                for done, holder in list(self.waiters):
                    holder["ok"] = ok
                    done.set()
                self.waiters.clear()

            def _on_window_destroyed(self, *_args) -> None:
                self.window = None

        self._bridge = _Bridge(self)
        self._bridge.moveToThread(app.thread())
        self._bridge.openRequested.connect(self._bridge._open)
        self._lock = threading.Lock()

    def submit(self, timeout: float) -> bool:
        from PySide6.QtCore import QThread, QCoreApplication

        done = threading.Event()
        holder: dict = {"ok": False}
        with self._lock:
            self._bridge.waiters.append((done, holder))
            current = QThread.currentThread()
            app_thread = QCoreApplication.instance().thread()
            if current is app_thread:
                self._bridge._open()
                return bool(holder["ok"])
            self._bridge.openRequested.emit()
        if not done.wait(timeout):
            with self._lock:
                try:
                    self._bridge.waiters.remove((done, holder))
                except ValueError:
                    pass
            return False
        return bool(holder["ok"])


_bridge_lock = threading.Lock()
_open_bridge: _OpenBridge | None = None


def _get_bridge(app) -> _OpenBridge:
    global _open_bridge
    with _bridge_lock:
        if _open_bridge is None:
            _open_bridge = _OpenBridge(app)
        return _open_bridge


def request_open(timeout: float = 6.0) -> bool:
    """Ouvre (ou montre) le module. Sûr depuis n'importe quel thread."""
    try:
        from PySide6.QtWidgets import QApplication
    except Exception:
        log.warning("God's Eye View : PySide6 indisponible — module non ouvrable")
        return False

    app = QApplication.instance()
    if app is None:
        log.warning("God's Eye View : aucune QApplication — module indisponible")
        return False
    if not web_build_available():
        log.warning(
            "God's Eye View : %s absent — lancez « npm install && npm run build » dans %s",
            DIST_INDEX, WEB_DIR,
        )
        return False
    if not webengine_available():
        log.warning("God's Eye View : PySide6.QtWebEngine* indisponible (non installé ?)")
        return False
    controller = get_default_controller()
    if controller.lifecycle in ("loading", "ready") and _open_bridge is not None:
        return True
    return _get_bridge(app).submit(timeout=timeout)


def active_window():
    global _open_bridge
    return getattr(getattr(_open_bridge, "_bridge", None), "window", None)
