import os
import queue
import threading
import time

import numpy as np
import sounddevice as sd

try:
    import openwakeword
    from openwakeword.model import Model
    from openwakeword.utils import download_models
except Exception:
    openwakeword = None
    Model = None
    download_models = None


class AudioIO:
    # =========================================================
    # CONFIGURATION AUDIO
    # =========================================================

    OUTPUT_RATE = 24000
    INPUT_RATE = 16000

    CHANNELS = 1
    SAMPLE_WIDTH = 2

    # 1280 samples à 16 kHz = 80 ms
    INPUT_BLOCKSIZE = 1280

    # Sensibilité de détection de "Hey Jarvis"
    WAKE_THRESHOLD = 0.5

    # Durée pendant laquelle Jarvis reste actif après
    # la fin d'une réponse Gemini
    FOLLOW_UP_SECONDS = 8.0

    # Anti-double-détection
    WAKE_COOLDOWN_SECONDS = 1.0

    def __init__(self, on_input):
        self.on_input = on_input

        self.running = False
        self.awake = False

        # Heure limite de la fenêtre de conversation
        self.follow_up_until = 0.0
        self.last_wake_time = 0.0

        # =====================================================
        # MICRO
        # =====================================================

        self.input_queue = queue.Queue(maxsize=50)
        self.wake_thread = None

        self.ins = None
        self.outs = None

        # =====================================================
        # SORTIE AUDIO
        # =====================================================

        self.q = queue.Queue()
        self.buffer = bytearray()
        self.lock = threading.Lock()

        self.audio_started = False

        # 100 ms de prébuffer
        self.prebuffer_bytes = int(
            self.OUTPUT_RATE
            * self.CHANNELS
            * self.SAMPLE_WIDTH
            * 0.10
        )

        # =====================================================
        # OPENWAKEWORD
        # =====================================================

        print("[Wake Word] Chargement de Hey Jarvis...")

        self.wake_model = None
        if Model is None:
            print("[Wake Word] openwakeword indisponible, détection de wake word désactivée.")
            return

        download_target = None
        if openwakeword is not None:
            download_target = os.path.join(
                os.path.dirname(os.path.abspath(openwakeword.__file__)),
                "resources",
                "models",
            )

        try:
            self.wake_model = Model(
                wakeword_models=["hey_jarvis"],
                inference_framework="tflite",
            )
            print("[Wake Word] Modèle chargé.")
            return
        except Exception as exc:
            print(f"[Wake Word] Chargement initial impossible : {exc}")

        if download_models is not None and download_target is not None:
            try:
                print("[Wake Word] Téléchargement du modèle Hey Jarvis...")
                download_models(["hey_jarvis"], target_directory=download_target)
                self.wake_model = Model(
                    wakeword_models=["hey_jarvis"],
                    inference_framework="tflite",
                )
                print("[Wake Word] Modèle chargé après téléchargement.")
                return
            except Exception as dl_exc:
                print(f"[Wake Word] Téléchargement du modèle impossible : {dl_exc}")

        try:
            self.wake_model = Model(
                wakeword_models=["hey_jarvis"],
                inference_framework="onnx",
            )
            print("[Wake Word] Modèle ONNX chargé.")
        except Exception as onnx_exc:
            print(f"[Wake Word] Aucun modèle wake-word disponible : {onnx_exc}")
            self.wake_model = None

    # =========================================================
    # DÉMARRAGE
    # =========================================================

    def start(self):
        if self.running:
            return

        self.running = True
        self.audio_started = False

        # Sortie audio de Gemini
        self.outs = sd.RawOutputStream(
            samplerate=self.OUTPUT_RATE,
            channels=self.CHANNELS,
            dtype="int16",
            blocksize=1024,
            callback=self._out
        )

        self.outs.start()

        # Micro
        self.ins = sd.RawInputStream(
            samplerate=self.INPUT_RATE,
            channels=self.CHANNELS,
            dtype="int16",
            blocksize=self.INPUT_BLOCKSIZE,
            callback=self._in
        )

        self.ins.start()

        # Thread séparé pour le traitement audio
        self.wake_thread = threading.Thread(
            target=self._audio_worker,
            daemon=True
        )

        self.wake_thread.start()

        print("[Jarvis] En veille.")
        print('[Jarvis] Dites "Hey Jarvis".')

    # =========================================================
    # CALLBACK MICRO
    # =========================================================

    def _in(self, indata, frames, time_info, status):
        """
        Le callback doit être extrêmement rapide.
        On ne fait aucune inférence IA ici.
        """

        if status:
            print("[Micro]", status)

        if not self.running:
            return

        pcm = bytes(indata)

        try:
            self.input_queue.put_nowait(pcm)

        except queue.Full:
            # On évite de bloquer le callback.
            # On supprime le bloc le plus ancien.
            try:
                self.input_queue.get_nowait()
            except queue.Empty:
                pass

            try:
                self.input_queue.put_nowait(pcm)
            except queue.Full:
                pass

    # =========================================================
    # THREAD DE TRAITEMENT AUDIO
    # =========================================================

    def _audio_worker(self):
        """
        En veille :
            Micro -> openWakeWord

        Actif :
            Micro -> Gemini

        Le timeout est vérifié en permanence.

        openWakeWord ne tourne PAS lorsque Jarvis est actif.
        """

        while self.running:

            # IMPORTANT :
            # Vérification du timeout à CHAQUE tour,
            # même lorsque le micro produit des blocs silencieux.
            self._check_timeout()

            try:
                pcm = self.input_queue.get(timeout=0.1)

            except queue.Empty:
                continue

            # =================================================
            # MODE ACTIF
            # =================================================

            if self.awake:

                try:
                    # Pendant une conversation, le wake word
                    # n'est absolument pas analysé.
                    self.on_input(pcm)

                except Exception as e:
                    print("[Micro -> Gemini] Erreur :", e)

                continue

            # =================================================
            # MODE VEILLE
            # =================================================

            if self._detect_wake_word(pcm):
                self._wake()

    # =========================================================
    # DÉTECTION DU WAKE WORD
    # =========================================================

    def _detect_wake_word(self, pcm):

        if self.wake_model is None:
            return False

        now = time.monotonic()

        # Anti-double-détection
        if (
            now - self.last_wake_time
            < self.WAKE_COOLDOWN_SECONDS
        ):
            return False

        try:
            samples = np.frombuffer(
                pcm,
                dtype=np.int16
            )

            scores = self.wake_model.predict(samples)

            score = float(
                scores.get("hey_jarvis", 0.0)
            )

            # Pour afficher tous les scores :
            #
            # print(
            #     f"\r[Wake Word] Score : {score:.4f}",
            #     end="",
            #     flush=True
            # )

            if score >= self.WAKE_THRESHOLD:

                print(
                    f'\n[Wake Word] "Hey Jarvis" détecté '
                    f"(score={score:.3f})"
                )

                return True

        except Exception as e:
            print("[Wake Word] Erreur :", e)

        return False

    # =========================================================
    # RÉVEIL DE JARVIS
    # =========================================================

    def _wake(self):

        now = time.monotonic()

        self.awake = True
        self.last_wake_time = now

        # Sécurité : retour en veille si Gemini ne répond pas
        self.follow_up_until = (
            now + self.FOLLOW_UP_SECONDS
        )

        try:
            self.wake_model.reset()
        except Exception:
            pass

        # On retire les vieux blocs qui ont été capturés
        # avant le réveil.
        while True:
            try:
                self.input_queue.get_nowait()
            except queue.Empty:
                break

        print("[Jarvis] Réveillé. Je vous écoute.")

    # =========================================================
    # FIN DE RÉPONSE GEMINI
    # =========================================================

    def extend_listening(self):
        """
        Cette fonction est appelée lorsque Gemini termine
        réellement son tour.

        Les 8 secondes commencent après sa réponse.
        """

        if not self.awake:
            return

        self.follow_up_until = (
            time.monotonic()
            + self.FOLLOW_UP_SECONDS
        )

        print(
            f"[Jarvis] Conversation active pour encore "
            f"{self.FOLLOW_UP_SECONDS:.0f} secondes."
        )

    # =========================================================
    # TIMEOUT / RETOUR EN VEILLE
    # =========================================================

    def _check_timeout(self):

        if not self.awake:
            return

        if time.monotonic() >= self.follow_up_until:

            self.awake = False

            try:
                self.wake_model.reset()
            except Exception:
                pass

            print(
                '[Jarvis] Retour en veille. '
                'Dites "Hey Jarvis".'
            )

    # =========================================================
    # SORTIE AUDIO
    # =========================================================

    def _out(self, outdata, frames, time_info, status):

        if status:
            print("[Audio]", status)

        needed = len(outdata)

        with self.lock:

            # Remplir le buffer avec les chunks Gemini
            while len(self.buffer) < needed:

                try:
                    chunk = self.q.get_nowait()
                    self.buffer.extend(chunk)

                except queue.Empty:
                    break

            # Prébuffer avant de commencer la lecture
            if not self.audio_started:

                if len(self.buffer) < self.prebuffer_bytes:
                    outdata[:] = b"\x00" * needed
                    return

                self.audio_started = True

            # Pas assez de données audio
            if len(self.buffer) < needed:

                available = len(self.buffer)

                outdata[:available] = (
                    self.buffer[:available]
                )

                outdata[available:] = (
                    b"\x00"
                    * (needed - available)
                )

                self.buffer.clear()

            # Données suffisantes
            else:

                outdata[:] = self.buffer[:needed]

                del self.buffer[:needed]

    # =========================================================
    # AUDIO REÇU DE GEMINI
    # =========================================================

    def play(self, pcm):

        if not pcm or not self.running:
            return

        with self.lock:
            self.q.put(bytes(pcm))

    # =========================================================
    # INTERRUPTION DE GEMINI
    # =========================================================

    def clear_output(self):

        with self.lock:

            self.buffer.clear()

            while True:

                try:
                    self.q.get_nowait()
                except queue.Empty:
                    break

        self.audio_started = False
        self.follow_up_until = time.monotonic() + self.FOLLOW_UP_SECONDS

    # =========================================================
    # ARRÊT
    # =========================================================

    def stop(self):

        self.running = False
        self.audio_started = False

        # Micro
        if self.ins:

            try:
                self.ins.stop()
                self.ins.close()
            except Exception:
                pass

            self.ins = None

        # Sortie audio
        if self.outs:

            try:
                self.outs.stop()
                self.outs.close()
            except Exception:
                pass

            self.outs = None

        # Thread
        if self.wake_thread:

            self.wake_thread.join(timeout=1.0)
            self.wake_thread = None

        # Nettoyage sortie
        self.clear_output()

        # Nettoyage entrée
        while True:

            try:
                self.input_queue.get_nowait()
            except queue.Empty:
                break

        self.awake = False

        try:
            self.wake_model.reset()
        except Exception:
            pass