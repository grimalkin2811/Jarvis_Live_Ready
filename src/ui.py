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

import asyncio
import sys
import threading
import traceback
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication

from .audio import AudioIO
from .config import load_config
from .gemini_live import AuthError, GeminiLive
from .memory import MemoryManager, set_default_memory_manager
from .scheduler import start_default_scheduler
from UI import appearance_actions
from UI import menu_state
from UI.screen_halo_overlay import ScreenHaloOverlay


def _ui_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "UI"


def _install_presets(restore: bool = False) -> None:
    """Ajoute les routines préconfigurées manquantes (silencieux si rien à faire)."""
    try:
        from .routines import install_default_presets

        result = install_default_presets(restore=restore)
        if result.get("success") and result.get("nombre"):
            print(f"[Routines] {result['nombre']} routine(s) preconfiguree(s) ajoutee(s).")
    except Exception as exc:  # pragma: no cover - ne doit jamais bloquer l'UI
        print(f"[Routines] Installation impossible : {exc}")


class PresenceBridge(QObject):
    """Signal émis depuis n'importe quel thread, délivré dans le thread Qt."""

    presence_changed = Signal(str)


class PresenceRouter(QObject):
    def __init__(self, overlay: ScreenHaloOverlay) -> None:
        super().__init__()
        self._overlay = overlay

    @Slot(str)
    def handle_presence(self, state: str) -> None:
        if state == "listening":
            self._overlay.show_listening()
        elif state == "thinking":
            self._overlay.show_thinking()
        elif state == "speaking":
            self._overlay.show_speaking()
        else:
            self._overlay.hide_overlay()


class VoiceEnergyRouter(QObject):
    """Relie le niveau micro (AudioIO) à l'énergie vocale de l'orbe."""

    @Slot(float)
    def handle_level(self, level: float) -> None:
        from UI import jarvis_menu

        jarvis_menu.set_voice_energy(level)


def _on_speaking(audio: AudioIO, presence_hook) -> None:
    """Jarvis commence à parler : on le marque comme 'speaking' dans AudioIO
    (pour suspendre le timeout) et on informe l'UI le cas échéant."""
    audio.begin_speaking()
    if presence_hook is not None:
        presence_hook("speaking")


def _run_voice_loop(
    config,
    presence_hook,
    voice_hook,
    stop_event: threading.Event,
) -> None:
    """Boucle vocale, identique à src.main.main() mais exécutée dans un thread.

    Elle possède sa propre boucle asyncio, comme l'exige Gemini aio.live.
    """
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

        # Routines preconfigurees : ajoutees au premier lancement, jamais ecrasees.
        _install_presets()

        audio = AudioIO(
            mic,
            presence_hook=presence_hook,
            voice_hook=voice_hook,
            mic_enabled=menu_state.LIVE.get_mic_enabled,
            wake_threshold=menu_state.LIVE.get_wake_threshold,
            volume_provider=menu_state.LIVE.get_tts_volume,
            listen_mode_provider=menu_state.LIVE.get_listen_mode,
            barge_in_provider=menu_state.LIVE.get_barge_in,
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
            response_mode_provider=menu_state.response_mode_label_from_live,
            voice_provider=menu_state.LIVE.get_voice_name,
            voice_version_provider=menu_state.LIVE.get_voice_version,
            speech_pace_provider=menu_state.LIVE.get_speech_pace,
            memory_manager=memory_manager,
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


def _build_tray_icon(on_activate, on_quit):
    """Icône de zone de notification : moyen visible et fiable de quitter
    Jarvis, indispensable en mode --desktop où aucune fenêtre ne réagit à
    Échap."""
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
        menu.addSeparator()
        quit_action = menu.addAction("Quitter Jarvis")
        quit_action.triggered.connect(on_quit)
        tray.setContextMenu(menu)
        tray.show()
        return tray
    except Exception:
        return None


def run_ui(mode: str = "desktop") -> int:
    app = QApplication(sys.argv)

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

    appearance_state = appearance_actions.load_state(
        str(_ui_dir() / "appearance_state.json")
    )

    stop_event = threading.Event()
    tray = None

    if mode == "ui":
        from UI import jarvis_menu
        from UI.jarvis_menu import MorphingOrbWidget

        # L'orbe réagit au niveau micro (énergie vocale) et à l'état vocal.
        voice_bridge = VoiceEnergyRouter()
        voice_hook = voice_bridge.handle_level

        window = MorphingOrbWidget()
        window.showFullScreen()

        presence_hook = jarvis_menu.set_presence_state
        voice_thread = _start_voice(config, presence_hook, voice_hook, stop_event)

        def _activate() -> None:
            window.showFullScreen()
            window.raise_()
            window.activateWindow()

        tray = _build_tray_icon(_activate, app.quit)

        def _persist_orb_state() -> None:
            # Quit via la zone de notification : closeEvent n'est pas
            # déclenché automatiquement, on force la sauvegarde.
            try:
                window.close()
            except Exception:
                pass

        app.aboutToQuit.connect(_persist_orb_state)
    else:
        overlay = ScreenHaloOverlay(appearance_state)
        bridge = PresenceBridge()
        router = PresenceRouter(overlay)
        bridge.presence_changed.connect(router.handle_presence)

        voice_hook = None
        voice_thread = _start_voice(config, bridge.presence_changed.emit, voice_hook, stop_event)

        # En mode overlay, il n'y a aucune fenêtre interactive : sans icône
        # de notification, il n'existe aucun moyen propre de quitter.
        app.setQuitOnLastWindowClosed(False)
        tray = _build_tray_icon(overlay.show_idle, app.quit)
        if tray is None:
            print("[Jarvis] Aucune icône de notification disponible : "
                  "utilise Ctrl+C dans cette console pour quitter.")

    def _shutdown() -> None:
        stop_event.set()
        voice_thread.join(timeout=3.0)

    app.aboutToQuit.connect(_shutdown)

    if mode == "ui":
        print("[Jarvis] Interface démarrée. Échap pour quitter, "
              "clic droit sur l'icône de notification pour afficher/quitter.")
    else:
        print("[Jarvis] Overlay démarré. Icône de notification → Quitter "
              "(ou Ctrl+C dans cette console).")

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
