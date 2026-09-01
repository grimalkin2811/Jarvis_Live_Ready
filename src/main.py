"""Point d'entrée de Jarvis Live Ready.

Modes disponibles :

* (par défaut)  : assistant vocal headless (console), comme avant.
* ``--ui``      : orbe morphing interactif (menus radiaux) + assistant vocal.
* ``--desktop`` : overlay halo plein écran + assistant vocal.

Exemples :
    python -m src.main
    python -m src.main --ui
    python -m src.main --desktop
"""

import argparse
import asyncio
import sys

from .audio import AudioIO
from .config import load_config
from .gemini_live import GeminiLive
from .memory import MemoryManager, set_default_memory_manager


async def run_headless():
    try:
        config = load_config()
    except Exception as exc:
        raise RuntimeError("La configuration Jarvis est manquante. Relance setup.bat.") from exc

    loop = asyncio.get_running_loop()
    gemini = None
    audio = None
    task = None

    def mic(pcm):
        if gemini is not None and gemini.can_send():
            asyncio.run_coroutine_threadsafe(gemini.send_audio(pcm), loop)

    try:
        memory_manager = MemoryManager(
            config.memory_database_path,
            enabled=config.memory_enabled,
            max_results=config.memory_max_results,
            min_importance=config.memory_min_importance,
        )
        set_default_memory_manager(memory_manager)

        audio = AudioIO(mic)
        gemini = GeminiLive(
            config.api_key,
            config.model,
            config.user,
            on_audio=audio.play,
            on_turn_complete=audio.extend_listening,
            on_interrupted=audio.clear_output,
            on_speaking=audio.begin_speaking,
            memory_manager=memory_manager,
        )

        print(f"Jarvis Live - Bonjour {config.user}")
        audio.start()
        print("Pret. Parle dans le micro. Ctrl+C pour arreter.")

        while True:
            try:
                await gemini.connect()
                await gemini.receive_loop()
            except (asyncio.CancelledError, KeyboardInterrupt):
                break
            except Exception as exc:
                import traceback
                print(f"\n[Jarvis] Connexion perdue ou erreur:")
                traceback.print_exc()
                audio.awake = False
                try:
                    audio.wake_model.reset()
                except Exception:
                    pass
                print("[Jarvis] Reconnexion dans 5 secondes...")
                await asyncio.sleep(5)
            else:
                # Reconnexion immédiate et silencieuse après une fermeture normale
                # pour préserver l'état éveillé et la fenêtre de 8 secondes de l'utilisateur.
                await asyncio.sleep(0.1)
            finally:
                try:
                    await gemini.close()
                except Exception:
                    pass
    finally:
        if audio is not None:
            audio.stop()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Jarvis Live Ready - assistant vocal Gemini Live."
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--ui",
        action="store_true",
        help="Lance l'orbe morphing interactif + l'assistant vocal.",
    )
    group.add_argument(
        "--desktop",
        action="store_true",
        help="Lance l'overlay halo plein écran + l'assistant vocal.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.ui or args.desktop:
        from .ui import run_ui

        mode = "ui" if args.ui else "desktop"
        return run_ui(mode)

    try:
        asyncio.run(run_headless())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
