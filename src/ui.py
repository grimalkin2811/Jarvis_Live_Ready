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

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QApplication

from .audio import AudioIO
from .config import load_config
from .gemini_live import GeminiLive
from .memory import MemoryManager, set_default_memory_manager
from UI import appearance_actions
from UI import menu_state
from UI.screen_halo_overlay import ScreenHaloOverlay


def _ui_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "UI"


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

    async def _main():
        nonlocal gemini, audio
        memory_manager = MemoryManager(
            config.memory_database_path,
            enabled=config.memory_enabled,
            max_results=config.memory_max_results,
            min_importance=config.memory_min_importance,
        )
        set_default_memory_manager(memory_manager)

        audio = AudioIO(
            mic,
            presence_hook=presence_hook,
            voice_hook=voice_hook,
            mic_enabled=menu_state.LIVE.get_mic_enabled,
            wake_threshold=menu_state.LIVE.get_wake_threshold,
        )
        gemini = GeminiLive(
            config.api_key,
            config.model,
            config.user,
            on_audio=audio.play,
            on_turn_complete=audio.extend_listening,
            on_interrupted=audio.clear_output,
            on_speaking=lambda: presence_hook("speaking") if presence_hook else None,
            response_mode_provider=menu_state.response_mode_label_from_live,
            memory_manager=memory_manager,
        )

        print(f"Jarvis Live - Bonjour {config.user}")
        audio.start()
        print("Pret. Parle dans le micro.")

        while not stop_event.is_set():
            try:
                await gemini.connect()
                await gemini.receive_loop()
            except asyncio.CancelledError:
                break
            except Exception:
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

    try:
        loop.run_until_complete(_main())
    finally:
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


def run_ui(mode: str = "desktop") -> int:
    config = load_config()

    app = QApplication(sys.argv)

    appearance_state = appearance_actions.load_state(
        str(_ui_dir() / "appearance_state.json")
    )

    stop_event = threading.Event()

    if mode == "ui":
        from UI.jarvis_menu import MorphingOrbWidget

        # L'orbe réagit au niveau micro (énergie vocale).
        voice_bridge = VoiceEnergyRouter()
        voice_hook = voice_bridge.handle_level

        window = MorphingOrbWidget()
        window.showFullScreen()

        # Pas d'overlay halo en mode ui : l'orbe joue ce rôle.
        voice_thread = _start_voice(config, None, voice_hook, stop_event)
    else:
        overlay = ScreenHaloOverlay(appearance_state)
        bridge = PresenceBridge()
        router = PresenceRouter(overlay)
        bridge.presence_changed.connect(router.handle_presence)

        voice_hook = None
        voice_thread = _start_voice(config, bridge.presence_changed.emit, voice_hook, stop_event)

    def _shutdown() -> None:
        stop_event.set()
        voice_thread.join(timeout=3.0)

    app.aboutToQuit.connect(_shutdown)

    print("[Jarvis] Interface démarrée. Échap pour quitter (mode ui).")

    try:
        return app.exec()
    finally:
        _shutdown()


def main() -> int:
    return run_ui()
