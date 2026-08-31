import asyncio

from .audio import AudioIO
from .config import load_config
from .gemini_live import GeminiLive


async def main():
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
        audio = AudioIO(mic)
        gemini = GeminiLive(
            config.api_key,
            config.model,
            config.user,
            on_audio=audio.play,
            on_turn_complete=audio.extend_listening,
            on_interrupted=audio.clear_output,
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


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
