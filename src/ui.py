"""Lancement de Jarvis Live Ready avec interface graphique (PySide6).

Deux modes sont disponibles :

* ``desktop`` — overlay halo plein écran, transparent aux clics, qui reflète
  l'état de Jarvis (écoute / parole / veille). C'est le mode « toujours actif ».
* ``ui``     — l'orbe morphing interactif (menus radiaux) de Jarvis.

Dans les deux cas, l'assistant vocal (Gemini Live + wake word « Hey Jarvis »)
tourne dans un thread séparé. Les transitions d'état sont transmises à l'UI
via des signaux Qt, ce qui est thread-safe.
"""

from __future__ import annotations

import sys
import threading
import traceback

from PySide6.QtCore import QObject, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QActionGroup, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

# Imports CHEAP restant au niveau module (le chemin de démarrage UI) :
# config, paths, protocols et les ponts UI sont déjà nécessaires avant
# l'affichage. Les imports lourds (asyncio, audio, gemini_live, memory,
# scheduler, screen_halo_overlay) sont déportés dans le code qui les
# utilise vraiment — voir _run_voice_loop et le branch desktop — pour
# réduire le temps avant « Interface démarrée » (1.3.2).
from .config import load_config
from . import paths, protocols, settings
from UI import appearance_actions
from UI import menu_state
from UI.notification_bridge import NotificationBridge


class PresenceBridge(QObject):
    """Signal émis depuis n'importe quel thread, délivré dans le thread Qt."""

    presence_changed = Signal(str)


class PresenceRouter(QObject):
    """Relie l'état vocal au cadre Desktop.

    En écoute continue, AudioIO ne revient jamais en « hidden » : un
    minuteur local masque alors le cadre après la fenêtre de follow-up,
    pour qu'il ne reste pas affiché indéfiniment.
    """

    def __init__(self, overlay) -> None:
        super().__init__()
        self._overlay = overlay
        self._listen_hide_timer = QTimer(self)
        self._listen_hide_timer.setSingleShot(True)
        self._listen_hide_timer.timeout.connect(self._hide_if_listening)

    def _hide_if_listening(self) -> None:
        if getattr(self._overlay, "_presence_state", "") == "listening":
            self._overlay.hide_overlay()

    @Slot(str)
    def handle_presence(self, state: str) -> None:
        try:
            from .modes import get_default_mode_manager

            if get_default_mode_manager().should_suppress_visuals():
                self._listen_hide_timer.stop()
                self._overlay.hide_overlay()
                return
        except Exception:
            pass
        if state == "listening":
            self._overlay.show_listening()
            # Suivre la fenêtre de conversation AudioIO (8 s) + une courte
            # marge : le cadre disparaît même si l'écoute continue reste active.
            # AudioIO n'est pas importé au niveau module (démarrage 1.3.2).
            delay_ms = 8400
            try:
                from .audio import AudioIO

                delay_ms = int(AudioIO.FOLLOW_UP_SECONDS * 1000) + 400
            except Exception:
                pass
            self._listen_hide_timer.start(delay_ms)
        elif state == "thinking":
            self._listen_hide_timer.stop()
            self._overlay.show_thinking()
        elif state == "speaking":
            self._listen_hide_timer.stop()
            self._overlay.show_speaking()
        else:
            self._listen_hide_timer.stop()
            self._overlay.hide_overlay()


class VoiceEnergyRouter(QObject):
    """Relie le niveau micro (AudioIO) à l'énergie vocale de l'orbe."""

    @Slot(float)
    def handle_level(self, level: float) -> None:
        from UI import jarvis_menu

        jarvis_menu.set_voice_energy(level)


class InterfaceModeController(QObject):
    """Bascule à chaud entre les deux interfaces historiques de Jarvis.

    Le contrôleur ne possède pas de copie persistante du mode : ``current_mode``
    est la valeur normalisée chargée depuis ``settings`` au démarrage, et chaque
    transition écrit d'abord cette même source de vérité. Les widgets Blob et
    Desktop restent ceux utilisés avant la v1.5.3 ; ils sont seulement créés à
    la demande puis affichés/masqués de façon cohérente.
    """

    mode_changed = Signal(str)
    mode_requested = Signal(str)
    presence_changed = Signal(str)

    def __init__(
        self,
        appearance_state,
        visibility: "SessionVisibility",
        *,
        initial_mode: str | None = None,
        config_path=None,
    ) -> None:
        super().__init__()
        self._appearance_state = appearance_state
        self._visibility = visibility
        self._config_path = config_path
        self._mode = settings.normalize_interface_mode(
            initial_mode if initial_mode is not None else settings.get_interface_mode(config_path)
        )
        self._presence_state = "hidden"
        self._voice_energy = 0.0
        self._blob = None
        self._overlay = None
        self._presence_router = None
        self._protocol_presenter = None
        self.presence_changed.connect(self._route_presence)
        self.mode_requested.connect(self.apply_persisted_mode)

    @property
    def blob(self):
        return self._blob

    @property
    def overlay(self):
        return self._overlay

    def current_mode(self) -> str:
        return self._mode

    def _ensure_blob(self):
        if self._blob is None:
            from UI import jarvis_menu
            from UI.jarvis_menu import MorphingOrbWidget

            self._blob = MorphingOrbWidget(
                interface_mode_getter=self.current_mode,
                interface_mode_setter=self.set_mode,
            )
            jarvis_menu.set_voice_energy(self._voice_energy)
            jarvis_menu.set_presence_state(self._presence_state)
            if self._protocol_presenter is not None:
                self._blob._protocol_presenter = self._protocol_presenter
        return self._blob

    def _ensure_overlay(self):
        if self._overlay is None:
            from UI.screen_halo_overlay import ScreenHaloOverlay

            self._overlay = ScreenHaloOverlay(self._appearance_state)
            self._presence_router = PresenceRouter(self._overlay)
        return self._overlay

    def set_protocol_presenter(self, presenter) -> None:
        self._protocol_presenter = presenter
        if self._blob is not None:
            self._blob._protocol_presenter = presenter

    def start(self) -> str:
        """Crée et affiche uniquement l'interface persistée au démarrage."""
        self._apply_mode(self._mode)
        return self._mode

    def set_mode(self, mode: str) -> str:
        """Persiste puis applique immédiatement Blob Mode ou Desktop Mode."""
        normalized = settings.set_interface_mode(mode, self._config_path)
        if normalized == self._mode:
            # Une sélection répétée réactive proprement l'interface courante.
            self.activate_current()
            return normalized
        self._mode = normalized
        self._apply_mode(normalized)
        self.mode_changed.emit(normalized)
        return normalized

    def request_mode(self, mode: str) -> None:
        """Entrée thread-safe du pont vocal (la valeur est déjà persistée)."""
        self.mode_requested.emit(settings.normalize_interface_mode(mode))

    @Slot(str)
    def apply_persisted_mode(self, mode: str) -> None:
        normalized = settings.normalize_interface_mode(mode)
        if normalized == self._mode:
            self.activate_current()
            return
        self._mode = normalized
        self._apply_mode(normalized)
        self.mode_changed.emit(normalized)

    def _apply_mode(self, mode: str) -> None:
        if mode == settings.BLOB_MODE:
            if self._presence_router is not None:
                self._presence_router._listen_hide_timer.stop()
            if self._overlay is not None:
                self._overlay.hide_overlay()
            blob = self._ensure_blob()
            blob.timer.start(16)
            # Choisir Blob Mode est une demande explicite : le Blob redevient
            # l'interface principale même s'il avait été masqué dans la session.
            blob.show_blob()
            self._visibility.show()
            self._route_presence(self._presence_state)
            return

        # Desktop Mode réutilise exclusivement le cadre existant. Le timer du
        # Blob est suspendu : aucune commande en attente ne peut le réafficher
        # derrière le dos du mode actif.
        if self._blob is not None:
            self._blob.timer.stop()
            self._blob._reset_menu_visuals()
            self._blob.hide()
            try:
                from UI import visibility_bridge

                visibility_bridge.VISIBILITY.report_state(
                    ui_attached=False,
                    blob_visible=False,
                    menu_open=False,
                    menu=None,
                )
            except Exception:
                pass
        overlay = self._ensure_overlay()
        # Recharge les choix Appearance éventuellement modifiés dans le Blob.
        overlay.appearance_state = appearance_actions.load_state(
            str(paths.appearance_state_file())
        )
        self._visibility.hide()
        self._route_presence(self._presence_state)

    @Slot(str)
    def _route_presence(self, state: str) -> None:
        self._presence_state = str(state or "hidden")
        if self._mode == settings.DESKTOP_MODE:
            self._ensure_overlay()
            assert self._presence_router is not None
            self._presence_router.handle_presence(self._presence_state)
            return
        from UI import jarvis_menu

        jarvis_menu.set_presence_state(self._presence_state)

    def handle_presence(self, state: str) -> None:
        """Entrée thread-safe fournie à AudioIO."""
        self.presence_changed.emit(str(state or "hidden"))

    def handle_voice_energy(self, level: float) -> None:
        self._voice_energy = float(level)
        if self._blob is not None:
            from UI import jarvis_menu

            jarvis_menu.set_voice_energy(self._voice_energy)

    def activate_current(self) -> None:
        if self._mode == settings.BLOB_MODE:
            blob = self._ensure_blob()
            blob.show_blob()
            self._visibility.show()
            return
        overlay = self._ensure_overlay()
        try:
            from .modes import get_default_mode_manager

            if get_default_mode_manager().should_suppress_visuals():
                overlay.hide_overlay()
                self._visibility.hide()
                return
        except Exception:
            pass
        overlay.show_idle()
        self._visibility.show()

    def hide_current(self) -> None:
        if self._mode == settings.BLOB_MODE:
            if self._blob is not None:
                self._blob.hide_blob()
        elif self._overlay is not None:
            self._overlay.hide_overlay()
        self._visibility.hide()

    def close(self) -> None:
        if self._presence_router is not None:
            self._presence_router._listen_hide_timer.stop()
        if self._blob is not None:
            self._blob.timer.stop()
            self._blob.close()
        if self._overlay is not None:
            self._overlay.close()


def _on_speaking(audio, presence_hook) -> None:
    """Jarvis commence à parler : on le marque comme 'speaking' dans AudioIO
    (pour suspendre le timeout) et on informe l'UI le cas échéant."""
    audio.begin_speaking()
    if presence_hook is not None:
        presence_hook("speaking")


def _on_thinking(presence_hook) -> None:
    """Jarvis traite (outil / réflexion) : le Blob et le cadre Desktop suivent."""
    if presence_hook is not None:
        presence_hook("thinking")


def _run_voice_loop(
    config,
    presence_hook,
    voice_hook,
    stop_event: threading.Event,
) -> None:
    """Boucle vocale, identique à src.main.main() mais exécutée dans un thread.

    Elle possède sa propre boucle asyncio, comme l'exige Gemini aio.live.

    Les imports du backend vocal sont faits ICI (et non au niveau module) :
    ils pèsent ~450 ms (sounddevice, google genai, sqlite…) et ne sont
    nécessaires qu'une fois l'UI affichée. En cas d'échec, l'UI reste
    utilisable et le message indique que la voix est indisponible.
    """
    import asyncio

    try:
        from .audio import AudioIO
        from .gemini_live import AuthError, GeminiLive
        from .memory import MemoryManager, set_default_memory_manager
        from .scheduler import start_default_scheduler
    except Exception as exc:
        print(f"[Jarvis] Backend vocal indisponible : {exc}")
        return

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    gemini = None
    audio = None

    def mic(pcm):
        if gemini is not None and gemini.can_send():
            asyncio.run_coroutine_threadsafe(gemini.send_audio(pcm), loop)

    def on_barge_in():
        """L'utilisateur a coupé la parole à Jarvis : Gemini doit s'arrêter."""
        if gemini is not None:
            gemini.request_interrupt()

    def stop_speaking():
        """Interruption manuelle (bouton du menu radial / touche S)."""
        if audio is not None:
            audio.stop_speaking()

    async def _main():
        nonlocal gemini, audio
        # Tant que le modèle wake word n'est pas chargé, l'UI affiche un état
        # « initialisation » : l'utilisateur sait qu'il ne doit pas parler
        # dans le vide.
        if presence_hook is not None:
            presence_hook("loading")

        memory_manager = MemoryManager(
            config.memory_database_path,
            enabled=config.memory_enabled,
            max_results=config.memory_max_results,
            min_importance=config.memory_min_importance,
        )
        set_default_memory_manager(memory_manager)

        # Rappels persistants + routines planifiees (thread de fond).
        try:
            start_default_scheduler()
        except Exception as exc:
            print(f"[Scheduler] Demarrage impossible : {exc}")

        audio = AudioIO(
            mic,
            presence_hook=presence_hook,
            voice_hook=voice_hook,
            mic_enabled=menu_state.LIVE.get_mic_enabled,
            wake_threshold=menu_state.LIVE.get_wake_threshold,
            volume_provider=menu_state.LIVE.get_tts_volume,
            listen_mode_provider=menu_state.LIVE.get_listen_mode,
            barge_in_provider=menu_state.LIVE.get_barge_in,
            post_response_provider=menu_state.LIVE.get_post_response_listen,
            on_barge_in=on_barge_in,
        )
        # Le menu radial (thread Qt) peut désormais couper la réponse en
        # cours ; le pont est retiré à l'arrêt pour ne pas garder de
        # référence morte.
        menu_state.LIVE.set_stop_speaking_handler(stop_speaking)
        gemini = GeminiLive(
            config.api_key,
            config.model,
            config.user,
            on_audio=audio.play,
            on_turn_complete=audio.extend_listening,
            on_interrupted=audio.clear_output,
            on_speaking=lambda: _on_speaking(audio, presence_hook),
            on_thinking=lambda: _on_thinking(presence_hook),
            memory_manager=memory_manager,
            **menu_state.voice_backend_kwargs(),
        )

        print(f"Jarvis Live - Bonjour {config.user}")
        audio.start()
        print("Pret. Parle dans le micro.")

        while not stop_event.is_set():
            gemini.reconnect_requested = False
            try:
                await gemini.connect()
                await gemini.receive_loop()
            except asyncio.CancelledError:
                break
            except AuthError as exc:
                print(f"\n[Jarvis] {exc}")
                print("[Jarvis] Impossible de continuer sans une clé valide. Arrêt.")
                break
            except Exception:
                if gemini.reconnect_requested:
                    # Fermeture volontaire (changement de voix) : on
                    # reconnexionne immédiatement, en silence.
                    pass
                else:
                    print("\n[Jarvis] Connexion perdue ou erreur:")
                    traceback.print_exc()
                    audio.awake = False
                    if presence_hook is not None:
                        presence_hook("hidden")
                    try:
                        audio.wake_model.reset()
                    except Exception:
                        pass
                    print("[Jarvis] Reconnexion dans 5 secondes...")
                    await asyncio.sleep(5)
            else:
                await asyncio.sleep(0.1)
            finally:
                try:
                    await gemini.close()
                except Exception:
                    pass
            if gemini.reconnect_requested and not stop_event.is_set():
                continue

    try:
        loop.run_until_complete(_main())
    finally:
        try:
            menu_state.LIVE.set_stop_speaking_handler(None)
        except Exception:
            pass
        try:
            from .scheduler import get_default_scheduler

            get_default_scheduler().stop()
        except Exception:
            pass
        if audio is not None:
            audio.stop()
        loop.close()


def _start_voice(config, presence_hook, voice_hook, stop_event) -> threading.Thread:
    thread = threading.Thread(
        target=_run_voice_loop,
        args=(config, presence_hook, voice_hook, stop_event),
        daemon=True,
    )
    thread.start()
    return thread


class SessionVisibility:
    """État Masquer/Afficher de la session. Jamais persisté.

    Un Quit pendant que Jarvis est masqué ne doit pas laisser un drapeau
    « caché » qui bloquerait le prochain lancement.
    """

    def __init__(self) -> None:
        self.hidden = False

    def hide(self) -> None:
        self.hidden = True

    def show(self) -> None:
        self.hidden = False

    def reset(self) -> None:
        self.hidden = False


def _build_tray_icon(
    on_activate,
    on_quit,
    on_hide=None,
    visibility: SessionVisibility | None = None,
    *,
    mode_getter=None,
    on_mode_change=None,
):
    """Icône de zone de notification : moyen visible et fiable de quitter
    Jarvis, indispensable en mode --desktop où aucune fenêtre ne réagit à
    Échap.

    Cas A : Masquer → l'UI disparaît (processus vivant).
    Cas B : Quitter depuis le tray, même masqué → le processus se termine.
    Cas C : l'état masqué n'est pas persisté ; un relance réaffiche l'UI.
    """
    try:
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon

        if not QSystemTrayIcon.isSystemTrayAvailable():
            return None

        pixmap = QPixmap(64, 64)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setBrush(QColor(70, 180, 255))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(10, 10, 44, 44)
        painter.setBrush(QColor(225, 250, 255))
        painter.drawEllipse(24, 24, 16, 16)
        painter.end()

        tray = QSystemTrayIcon(QIcon(pixmap))
        tray.setToolTip("Jarvis — assistant vocal")
        menu = QMenu()
        show_action = menu.addAction("Afficher Jarvis")
        show_action.triggered.connect(on_activate)
        hide_action = menu.addAction("Masquer Jarvis")
        if on_hide is not None:
            hide_action.triggered.connect(on_hide)
        else:
            hide_action.setEnabled(False)

        # Accès rapide disponible dans les DEUX modes, y compris Desktop où
        # le menu radial est volontairement caché.
        interface_menu = menu.addMenu("Interface")
        mode_group = QActionGroup(interface_menu)
        mode_group.setExclusive(True)
        mode_actions = {}
        for label, value in (("Blob Mode", settings.BLOB_MODE),
                             ("Desktop Mode", settings.DESKTOP_MODE)):
            action = interface_menu.addAction(label)
            action.setCheckable(True)
            mode_group.addAction(action)
            mode_actions[value] = action

            def _select_mode(_checked=False, selected=value) -> None:
                if on_mode_change is not None:
                    on_mode_change(selected)
                current = mode_getter() if mode_getter is not None else selected
                for candidate, candidate_action in mode_actions.items():
                    candidate_action.setChecked(candidate == current)

            action.triggered.connect(_select_mode)

        current_mode = (
            settings.normalize_interface_mode(mode_getter())
            if mode_getter is not None
            else settings.get_interface_mode()
        )
        for candidate, action in mode_actions.items():
            action.setChecked(candidate == current_mode)

        def _on_tray_activated(reason) -> None:
            trigger = getattr(QSystemTrayIcon, "ActivationReason", None)
            trigger_value = getattr(trigger, "Trigger", None) if trigger is not None else None
            if trigger_value is None:
                trigger_value = getattr(QSystemTrayIcon, "Trigger", 1)
            if reason != trigger_value:
                return
            if visibility is not None and visibility.hidden:
                on_activate()
            elif on_hide is not None:
                on_hide()
            else:
                on_activate()

        tray.activated.connect(_on_tray_activated)
        from UI.routines_dialog import show_routines_dialog

        def _show_routines_if_allowed() -> None:
            try:
                from .modes import get_default_mode_manager

                if get_default_mode_manager().should_suppress_visuals():
                    return
            except Exception:
                pass
            show_routines_dialog()

        routines_action = menu.addAction("Routines…")
        routines_action.triggered.connect(_show_routines_if_allowed)

        def _start_protocol_if_allowed(protocol_id: str) -> None:
            try:
                from .modes import get_default_mode_manager

                if get_default_mode_manager().should_suppress_visuals():
                    return
            except Exception:
                pass
            protocols.start_protocol(protocol_id)

        # Protocoles : la fonction cachée reste accessible à la souris pour
        # qui ne connaît pas les codes secrets, sauf en mode jeu où tout
        # affichage au-dessus du jeu est supprimé.
        protocol_menu = menu.addMenu("Protocoles")
        for protocol in protocols.PROTOCOLS:
            action = protocol_menu.addAction(protocol.name)
            action.triggered.connect(
                lambda _checked=False, pid=protocol.protocol_id:
                _start_protocol_if_allowed(pid)
            )
        menu.addSeparator()
        quit_action = menu.addAction("Quitter Jarvis")
        quit_action.triggered.connect(on_quit)
        tray.setContextMenu(menu)
        # Qt ne prend pas possession du QMenu : le garder vivant garantit
        # l'accès aux interrupteurs aussi en mode overlay, sans fenêtre.
        tray._jarvis_menu = menu
        tray._interface_mode_actions = mode_actions
        tray.show()
        return tray
    except Exception:
        return None


def run_ui(mode: str | None = None) -> int:
    # Réutilise un QApplication déjà créé (assistant de premier lancement).
    app = QApplication.instance() or QApplication(sys.argv)

    # Compatibilité des anciens appels run_ui("ui") / run_ui("desktop") :
    # l'argument explicite met à jour la même valeur que le launcher.
    if mode is not None:
        try:
            settings.set_interface_mode(mode)
        except ValueError:
            print(f"[Jarvis] Mode d'interface inconnu : {mode!r}")
            return 2

    try:
        config = load_config()
    except Exception as exc:
        print(f"[Jarvis] {exc}")
        try:
            from PySide6.QtWidgets import QMessageBox

            box = QMessageBox()
            box.setIcon(QMessageBox.Critical)
            box.setWindowTitle("Jarvis — configuration manquante")
            box.setText(str(exc))
            box.setInformativeText(
                "Lance setup.bat pour créer le fichier .env "
                "(nom, clé Gemini API)."
            )
            box.exec()
        except Exception:
            pass
        return 1

    # Voix / volume / micro / mode de réponse : même source pour Blob et Desktop.
    try:
        menu_state.load_runtime_preferences(
            str(paths.menu_state_file()),
            str(paths.system_state_file()),
        )
    except Exception:
        pass

    appearance_state = appearance_actions.load_state(
        str(paths.appearance_state_file())
    )

    stop_event = threading.Event()
    tray = None
    visibility = SessionVisibility()
    # Hide ne doit jamais quitter : le tray est le moyen de Quitter.
    app.setQuitOnLastWindowClosed(False)

    def _quit() -> None:
        visibility.reset()
        app.quit()

    controller = InterfaceModeController(
        appearance_state,
        visibility,
        initial_mode=config.interface_mode,
    )
    active_mode = controller.start()
    from UI.interface_mode_bridge import INTERFACE_MODE

    INTERFACE_MODE.set_handler(controller.request_mode)

    tray = _build_tray_icon(
        controller.activate_current,
        _quit,
        on_hide=controller.hide_current,
        visibility=visibility,
        mode_getter=controller.current_mode,
        on_mode_change=controller.set_mode,
    )
    if tray is None and active_mode == settings.DESKTOP_MODE:
        print("[Jarvis] Aucune icône de notification disponible : "
              "utilise Ctrl+C dans cette console pour quitter.")

    if tray is not None:
        def _sync_tray_mode(selected: str) -> None:
            for candidate, action in tray._interface_mode_actions.items():
                action.setChecked(candidate == selected)

        controller.mode_changed.connect(_sync_tray_mode)

    # Overlay des Protocoles : il écoute src.protocols quel que soit le mode,
    # pour que « Jarvis, réveille-toi » déclenche la séquence cinématique
    # aussi bien depuis l'orbe que depuis l'overlay halo.
    protocol_presenter = None
    try:
        from UI.boot_sequence import ProtocolPresenter

        protocol_presenter = ProtocolPresenter()
        controller.set_protocol_presenter(protocol_presenter)
    except Exception as exc:
        print(f"[Protocole] Overlay indisponible : {exc}")

    # Brancher les notifications AVANT de démarrer le planificateur vocal :
    # les rappels échus au lancement sont visibles même si l'orbe est en veille.
    notification_bridge = NotificationBridge(tray)
    voice_thread = _start_voice(
        config,
        controller.handle_presence,
        controller.handle_voice_energy,
        stop_event,
    )
    shutdown_done = False

    def _shutdown() -> None:
        nonlocal shutdown_done
        if shutdown_done:
            return
        shutdown_done = True
        stop_event.set()
        voice_thread.join(timeout=3.0)
        notification_bridge.close()
        INTERFACE_MODE.set_handler(None)
        controller.close()
        if protocol_presenter is not None:
            try:
                protocol_presenter.close()
            except Exception:
                pass

    app.aboutToQuit.connect(_shutdown)

    if active_mode == settings.BLOB_MODE:
        print("[Jarvis] Blob Mode démarré. Échap pour quitter, "
              "clic droit sur l'icône de notification pour changer de mode.")
    else:
        print("[Jarvis] Desktop Mode démarré. Icône de notification → Interface "
              "pour passer au Blob (ou Ctrl+C dans cette console).")

    try:
        return app.exec()
    finally:
        _shutdown()
        if tray is not None:
            try:
                tray.hide()
            except Exception:
                pass


def main() -> int:
    return run_ui()
