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

    # Avance audio maximale tolérée dans la file de sortie (en secondes).
    # Au-delà, les blocs les plus anciens sont retirés : la voix de Jarvis
    # reste ainsi synchronisée même si le réseau envoie par rafales.
    MAX_QUEUED_SECONDS = 5.0

    def __init__(self, on_input, presence_hook=None, voice_hook=None,
                 mic_enabled=None, wake_threshold=None,
                 volume_provider=None, listen_mode_provider=None):
        self.on_input = on_input

        # Hooks optionnels pour l'UI (voir src/ui.py).
        # presence_hook(state) : "loading" | "listening" | "hidden"
        # voice_hook(level)     : 0.0 .. 1.0 (niveau d'entrée micro)
        # mic_enabled()         : bool — micro coupé/rétabli depuis le menu.
        # wake_threshold()      : float — sensibilité du wake word depuis le menu.
        # volume_provider()     : int 0..100 — volume de la voix Jarvis.
        # listen_mode_provider(): bool — écoute continue (sans wake word).
        self.presence_hook = presence_hook
        self.voice_hook = voice_hook
        self.mic_enabled = mic_enabled
        self.wake_threshold = wake_threshold
        self.volume_provider = volume_provider
        self.listen_mode_provider = listen_mode_provider
        self._last_voice_emit = 0.0

        self.running = False
        self.awake = False

        # True tant que Jarvis est en train de parler (réponse Gemini en cours).
        # Pendant ce temps, le timeout de conversation ne doit jamais se
        # déclencher : on ne retourne en veille que lorsque la réponse est finie.
        self.speaking = False
        self._speaking_lock = threading.Lock()

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

        # Nombre d'octets actuellement en file (pour plafonner la latence).
        self._queued_bytes = 0
        self._max_queued_bytes = int(
            self.OUTPUT_RATE
            * self.CHANNELS
            * self.SAMPLE_WIDTH
            * self.MAX_QUEUED_SECONDS
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

        # L'état « initialisation » de l'UI prend fin ici : Jarvis est prêt,
        # en veille jusqu'au prochain « Hey Jarvis » (ou en écoute continue).
        self._emit_presence("hidden")

        if self._listen_mode_active():
            print("[Jarvis] Écoute continue active. Parle directement.")
        else:
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

        # Niveau d'entrée (RMS) pour l'énergie vocale de l'UI.
        # Calcul léger, effectué à chaque bloc de 80 ms.
        if self.voice_hook is not None and self._is_mic_enabled():
            try:
                samples = np.frombuffer(indata, dtype=np.int16)
                rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
                level = min(1.0, rms / 3000.0)
                self._emit_voice(level)
            except Exception:
                pass

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

            # Micro coupé : on ne forward rien et on n'écoute pas le wake word.
            if not self._is_mic_enabled():
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

            self._handle_idle_block(pcm)

    def _handle_idle_block(self, pcm) -> None:
        """En veille : réveil automatique (écoute continue) ou wake word."""
        # Écoute continue activée depuis le menu : Jarvis se réveille
        # tout seul et reste actif sans exiger « Hey Jarvis ».
        if self._listen_mode_active():
            self._wake()
            return

        if self._detect_wake_word(pcm):
            self._wake()

    # =========================================================
    # DÉTECTION DU WAKE WORD
    # =========================================================

    def _is_mic_enabled(self) -> bool:
        if self.mic_enabled is None:
            return True
        try:
            return bool(self.mic_enabled())
        except Exception:
            return True

    def _listen_mode_active(self) -> bool:
        """Écoute continue demandée depuis le menu (pas de wake word)."""
        if self.listen_mode_provider is None:
            return False
        try:
            return bool(self.listen_mode_provider())
        except Exception:
            return False

    def _detect_wake_word(self, pcm):

        if self.wake_model is None:
            return False

        now = time.monotonic()
        threshold = self.WAKE_THRESHOLD
        if self.wake_threshold is not None:
            try:
                threshold = float(self.wake_threshold())
            except Exception:
                pass

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

            if score >= threshold:

                print(
                    f'\n[Wake Word] "Hey Jarvis" détecté '
                    f"(score={score:.3f})"
                )

                return True

        except Exception as e:
            print("[Wake Word] Erreur :", e)

        return False

    # =========================================================
    # HOOKS UI (présence / énergie vocale)
    # =========================================================

    def _emit_presence(self, state):
        if self.presence_hook is not None:
            try:
                self.presence_hook(state)
            except Exception:
                pass

    def _emit_voice(self, level):
        if self.voice_hook is None:
            return
        now = time.monotonic()
        # Throttle à ~20 Hz pour ne pas saturer le pont Qt.
        if now - self._last_voice_emit < 0.05:
            return
        self._last_voice_emit = now
        try:
            self.voice_hook(float(level))
        except Exception:
            pass

    # =========================================================
    # RÉVEIL DE JARVIS
    # =========================================================

    def _wake(self):

        now = time.monotonic()

        self.awake = True
        self._set_speaking(False)
        self.last_wake_time = now
        self._emit_presence("listening")

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

        # Jarvis a fini de parler : le timeout redevient actif.
        self._set_speaking(False)

        # Si un retour en veille a déjà eu lieu (connexion perdue, mic coupé,
        # interruption de l'utilisateur après la fin du tour), on ne relance
        # pas la fenêtre de conversation.
        if not self.awake:
            return

        self.follow_up_until = (
            time.monotonic()
            + self.FOLLOW_UP_SECONDS
        )
        self._emit_presence("listening")

        print(
            f"[Jarvis] Conversation active pour encore "
            f"{self.FOLLOW_UP_SECONDS:.0f} secondes."
        )

    def begin_speaking(self):
        """Jarvis commence à parler (début d'une réponse Gemini)."""
        self._set_speaking(True)

    def _set_speaking(self, speaking: bool) -> None:
        with self._speaking_lock:
            self.speaking = bool(speaking)

    # =========================================================
    # TIMEOUT / RETOUR EN VEILLE
    # =========================================================

    def _check_timeout(self):

        if not self.awake:
            return

        # Pendant que Jarvis parle, la fenêtre de conversation est suspendue :
        # on ne retourne en veille qu'une fois sa réponse terminée.
        with self._speaking_lock:
            if self.speaking:
                return

        if time.monotonic() >= self.follow_up_until:

            # Écoute continue : la fenêtre de conversation se renouvelle
            # indéfiniment tant que le mode reste actif.
            if self._listen_mode_active():
                self.follow_up_until = (
                    time.monotonic()
                    + self.FOLLOW_UP_SECONDS
                )
                return

            self.awake = False
            self._emit_presence("hidden")

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
                    self._queued_bytes -= len(chunk)
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

                self._queued_bytes -= available
                self.buffer.clear()

            # Données suffisantes
            else:

                outdata[:] = self.buffer[:needed]

                self._queued_bytes -= needed
                del self.buffer[:needed]

        if self._queued_bytes < 0:
            self._queued_bytes = 0

    # =========================================================
    # AUDIO REÇU DE GEMINI
    # =========================================================

    def _output_volume(self) -> float:
        """Volume de sortie (0.0 .. 1.0), lu en temps réel depuis le menu."""
        if self.volume_provider is None:
            return 1.0
        try:
            vol = int(self.volume_provider())
        except Exception:
            return 1.0
        vol = max(0, min(100, vol))
        # Courbe perceptuelle : 50 % du curseur ≈ un tiers du gain réel.
        return (vol / 100.0) ** 1.6

    def _apply_gain(self, pcm: bytes, gain: float) -> bytes:
        try:
            samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
            samples = np.clip(samples * gain, -32768, 32767)
            return samples.astype(np.int16).tobytes()
        except Exception:
            return pcm

    def play(self, pcm):

        if not pcm or not self.running:
            return

        gain = self._output_volume()
        data = bytes(pcm)
        if gain < 0.995:
            data = self._apply_gain(data, gain)

        with self.lock:
            # Plafonner l'avance audio : si la file dépasse quelques secondes
            # (réseau en rafales, lecture suspendue), on retire les blocs les
            # plus anciens pour que la voix reste synchronisée.
            while (
                self._queued_bytes + len(data) > self._max_queued_bytes
                and not self.q.empty()
            ):
                try:
                    dropped = self.q.get_nowait()
                    self._queued_bytes -= len(dropped)
                except queue.Empty:
                    break
            self.q.put(data)
            self._queued_bytes += len(data)

    # =========================================================
    # INTERRUPTION DE GEMINI
    # =========================================================

    def clear_output(self):

        with self.lock:

            self.buffer.clear()
            self._queued_bytes = 0

            while True:

                try:
                    self.q.get_nowait()
                except queue.Empty:
                    break

        self.audio_started = False
        # L'utilisateur a interrompu Jarvis : il ne parle plus.
        self._set_speaking(False)
        self.follow_up_until = time.monotonic() + self.FOLLOW_UP_SECONDS
        self._emit_presence("listening")

    # =========================================================
    # ARRÊT
    # =========================================================

    def stop(self):

        self.running = False
        self.audio_started = False
        self._emit_presence("hidden")

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
        self._set_speaking(False)

        try:
            self.wake_model.reset()
        except Exception:
            pass