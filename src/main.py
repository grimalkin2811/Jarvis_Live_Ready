"""Point d'entrée de Jarvis Live Ready.

Modes disponibles :

* (par défaut)  : assistant vocal headless (console), comme avant.
* ``--ui``      : orbe morphing interactif (menus radiaux) + assistant vocal.
* ``--desktop`` : overlay halo plein écran + assistant vocal.
* ``--smoke-test`` : test rapide sans audio/Gemini (pour CI packaging).
* ``--version`` : affiche la version.

Exemples :
    python -m src.main
    python -m src.main --ui
    python -m src.main --desktop
    python -m src.main --smoke-test
"""

import argparse
import asyncio
import sys

# Les imports lourds (audio, Gemini) sont faits dans run_headless : ainsi
# « python -m src.main --protocol wake_up » joue la séquence cinématique
# même sans micro, sans clé API et sans sounddevice installé.

from . import first_run, paths, settings
from . import logging_setup
from .version import get_version

# Pont vers les réglages du menu radial. Il est optionnel : sans PySide6
# (mode console minimal), Jarvis fonctionne avec les valeurs par défaut.
try:
    from UI.menu_state import LIVE as MENU_LIVE
    from UI.menu_state import response_mode_label_from_live as MENU_RESPONSE_MODE
except Exception:  # pragma: no cover - PySide6 absent
    MENU_LIVE = None
    MENU_RESPONSE_MODE = None


def _load_menu_bridge() -> None:
    """Charge les réglages du menu radial (UI/menu_state.json) pour que la
    voix, le volume, le micro, etc. restent cohérents entre le mode console
    et le mode orbe. Échoue silencieusement en cas d'absence."""
    try:
        from UI import menu_state as ms

        ms.load_state(str(paths.menu_state_file()))
    except Exception:
        pass


def _run_smoke_test() -> int:
    """Test rapide pour CI : vérifie que l'exécutable démarre et que les
    dépendances critiques sont présentes.

    Ne nécessite pas de micro, haut-parleur, clé API ou config.
    """
    print(f"=== JARVIS SMOKE TEST ===")
    print(f"Version: {get_version()}")
    print(f"Python: {sys.version}")
    print(f"Frozen: {getattr(sys, 'frozen', False)}")
    if getattr(sys, "frozen", False):
        print(f"Executable: {sys.executable}")
        print(f"_MEIPASS: {getattr(sys, '_MEIPASS', 'N/A')}")

    # Vérifie la structure PyInstaller si frozen
    if getattr(sys, "frozen", False):
        from pathlib import Path

        exe_dir = Path(sys.executable).resolve().parent
        print(f"Exe dir: {exe_dir}")

        # Vérifie les fichiers critiques
        critical = [
            exe_dir / "_internal" / "python311.dll",
            exe_dir / "_internal" / "base_library.zip",
        ]
        all_ok = True
        for path in critical:
            exists = path.exists()
            status = "OK" if exists else "MISSING"
            print(f"  {path.name}: {status} ({path})")
            if not exists:
                all_ok = False

        # Détecte l'aplatissement
        flattened = [
            exe_dir / "python311.dll",
            exe_dir / "base_library.zip",
        ]
        for path in flattened:
            if path.exists():
                print(f"  FLATTENED DETECTED: {path} should be in _internal/")
                all_ok = False

        if not all_ok:
            print("SMOKE TEST FAILED: Structure PyInstaller invalide")
            return 1

    # Teste les imports critiques (sans les instancier)
    print("Testing critical imports...")
    imports_to_test = [
        ("src.version", "version"),
        ("src.paths", "paths"),
        ("src.packaging_validation", "packaging_validation"),
        ("src.protocols", "protocols"),
    ]

    for module_name, short in imports_to_test:
        try:
            __import__(module_name)
            print(f"  {short}: OK")
        except Exception as exc:
            print(f"  {short}: FAIL - {exc}")
            return 1

    # Teste les imports optionnels (ne fait pas échouer, mais log)
    optional_imports = [
        "PySide6",
        "numpy",
        "google.genai",
        "openwakeword",
        "sounddevice",
    ]
    print("Testing optional imports (bundled in PyInstaller)...")
    for mod in optional_imports:
        try:
            __import__(mod)
            print(f"  {mod}: OK")
        except Exception as exc:
            print(f"  {mod}: MISSING ({exc}) - may be expected in dev")

    print("SMOKE TEST PASSED")
    return 0


async def run_headless():
    from .audio import AudioIO
    from .config import load_config
    from .gemini_live import AuthError, GeminiLive
    from .memory import MemoryManager, set_default_memory_manager
    from .scheduler import start_default_scheduler

    try:
        config = load_config()
    except Exception as exc:
        # En distribution, on lance l'assistant de première configuration
        # plutôt que de faire planter l'application.
        try:
            cfg = first_run.run_wizard(gui=False)
        except Exception:
            cfg = None
        if cfg is None:
            raise RuntimeError(
                "La configuration Jarvis est manquante ou incomplète. "
                "Relance Jarvis et complète l'assistant de configuration."
            ) from exc
        config = load_config()

    _load_menu_bridge()

    loop = asyncio.get_running_loop()
    gemini = None
    audio = None
    scheduler = None

    def mic(pcm):
        if gemini is not None and gemini.can_send():
            asyncio.run_coroutine_threadsafe(gemini.send_audio(pcm), loop)

    def on_barge_in():
        """L'utilisateur a coupé la parole à Jarvis : Gemini doit s'arrêter."""
        if gemini is not None:
            gemini.request_interrupt()

    try:
        memory_manager = MemoryManager(
            config.memory_database_path,
            enabled=config.memory_enabled,
            max_results=config.memory_max_results,
            min_importance=config.memory_min_importance,
        )
        set_default_memory_manager(memory_manager)

        # Rappels persistants + routines planifiees (thread de fond).
        try:
            scheduler = start_default_scheduler()
        except Exception as exc:
            print(f"[Scheduler] Demarrage impossible : {exc}")

        audio = AudioIO(
            mic,
            volume_provider=MENU_LIVE.get_tts_volume if MENU_LIVE else None,
            listen_mode_provider=MENU_LIVE.get_listen_mode if MENU_LIVE else None,
            mic_enabled=MENU_LIVE.get_mic_enabled if MENU_LIVE else None,
            wake_threshold=MENU_LIVE.get_wake_threshold if MENU_LIVE else None,
            barge_in_provider=MENU_LIVE.get_barge_in if MENU_LIVE else None,
            on_barge_in=on_barge_in,
        )
        gemini = GeminiLive(
            config.api_key,
            config.model,
            config.user,
            on_audio=audio.play,
            on_turn_complete=audio.extend_listening,
            on_interrupted=audio.clear_output,
            on_speaking=audio.begin_speaking,
            response_mode_provider=MENU_RESPONSE_MODE,
            voice_provider=MENU_LIVE.get_voice_name if MENU_LIVE else None,
            voice_version_provider=MENU_LIVE.get_voice_version if MENU_LIVE else None,
            speech_pace_provider=MENU_LIVE.get_speech_pace if MENU_LIVE else None,
            memory_manager=memory_manager,
        )

        print(f"Jarvis Live - Bonjour {config.user}")
        audio.start()
        print("Pret. Parle dans le micro. Ctrl+C pour arreter.")
        print('[Jarvis] Dis "stop" pendant une reponse pour l\'interrompre.')

        while True:
            gemini.reconnect_requested = False
            try:
                await gemini.connect()
                await gemini.receive_loop()
            except (asyncio.CancelledError, KeyboardInterrupt):
                break
            except AuthError as exc:
                print(f"\n[Jarvis] {exc}")
                print("[Jarvis] Impossible de continuer sans une clé valide. Arrêt.")
                break
            except Exception:
                import traceback
                if gemini.reconnect_requested:
                    pass
                else:
                    print("\n[Jarvis] Connexion perdue ou erreur:")
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
        if scheduler is not None:
            try:
                scheduler.stop()
            except Exception:
                pass
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
    group.add_argument(
        "--protocol",
        nargs="?",
        const="wake_up",
        metavar="NOM",
        help="Joue un protocole cinématique dans la console puis quitte "
             "(wake_up, diagnostic, focus, stand_down).",
    )
    group.add_argument(
        "--smoke-test",
        action="store_true",
        help="Test rapide de l'exécutable (pour CI, sans audio/API).",
    )
    group.add_argument(
        "--version",
        action="store_true",
        help="Affiche la version et quitte.",
    )
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    # Version simple
    if getattr(args, "version", False):
        print(get_version())
        return 0

    # Smoke test pour CI
    if getattr(args, "smoke_test", False):
        return _run_smoke_test()

    # Journalisation : un exécutable distribué ne dépend pas d'une console.
    is_gui = args.ui or args.desktop
    try:
        logging_setup.setup_logging(console=not is_gui)
    except Exception:
        pass

    log = logging_setup.get_logger("main")
    log.info("Jarvis %s démarre (mode=%s)", get_version(), "ui" if args.ui else "desktop" if args.desktop else "console")

    # Protocole seul : la séquence cinématique en mode console, sans micro
    # ni clé API. C'est la démonstration la plus rapide de la fonction.
    if getattr(args, "protocol", None):
        from . import protocols

        target = protocols.find_protocol(args.protocol)
        if target is None:
            names = ", ".join(item["id"] for item in protocols.list_protocols())
            print(f"[Jarvis] Protocole inconnu : {args.protocol}. Disponibles : {names}")
            return 2
        try:
            protocols.play_protocol(target, protocols.render_console())
        except KeyboardInterrupt:
            print("\n[Jarvis] Protocole interrompu.")
        return 0

    if args.ui or args.desktop:
        from .ui import run_ui

        mode = "ui" if args.ui else "desktop"

        # Premier lancement (absence de config.json) : assistant de
        # configuration avant d'ouvrir l'orbe.
        if settings.first_run_needed():
            from PySide6.QtWidgets import QApplication

            app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841 — garde une référence vivante
            cfg = first_run.run_wizard(gui=True)
            if cfg is None:
                print("[Jarvis] Configuration annulée.")
                return 1
        return run_ui(mode)

    try:
        asyncio.run(run_headless())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
