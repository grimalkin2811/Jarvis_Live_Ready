import collections
import os
import queue
import sys
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from . import paths

try:
    import openwakeword
    from openwakeword.model import Model
    from openwakeword.utils import download_models
except Exception:
    openwakeword = None
    Model = None
    download_models = None


def _openwakeword_model_name() -> str:
    """Nom canonique du wake word 'Hey Jarvis'."""
    return "hey_jarvis"


def _openwakeword_candidate_dirs() -> list[str]:
    """Répertoires où chercher le modèle OpenWakeWord, du plus spécifique au
    plus général. Pour une application distribuée, il ne faut PAS supposer que
    ``.venv/Lib/site-packages`` existe sur la machine de l'utilisateur."""
    dirs: list[str] = []
    # 1. Dossier utilisateur (modèles téléchargés au premier lancement).
    dirs.append(str(paths.openwakeword_models_dir()))
    # 2. Ressources embarquées dans le bundle PyInstaller (_MEIPASS).
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        meipass = str(sys._MEIPASS)
        # a. `resources/openwakeword` (dossier de ressources fourni par le build).
        dirs.append(os.path.join(meipass, "resources", "openwakeword"))
        dirs.append(os.path.join(meipass, "resources", "models"))
        # b. Emplacement d'origine du paquet openwakeword (si collecté tel quel).
        dirs.append(os.path.join(meipass, "openwakeword", "resources", "models"))
    # 3. Dossier de ressources du paquet installé (site-packages en dev).
    if openwakeword is not None:
        dirs.append(
            os.path.join(os.path.dirname(os.path.abspath(openwakeword.__file__)), "resources", "models")
        )
    return dirs


def _find_openwakeword_model(extension: str | None = None) -> str | None:
    """Retourne le chemin absolu du modèle 'hey_jarvis' s'il existe."""
    if openwakeword is None:
        return None
    expected = None
    try:
        info = openwakeword.MODELS.get(_openwakeword_model_name())
        if info:
            expected = Path(info.get("model_path", "")).name
    except Exception:
        expected = f"{_openwakeword_model_name()}_v0.1"
    for directory in _openwakeword_candidate_dirs():
        candidates = [expected] if expected else []
        if extension:
            candidates.append(f"{_openwakeword_model_name()}_v0.1{extension}")
        for name in candidates:
            if not name:
                continue
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate):
                return candidate
        # Recherche libre : tout fichier contenant le nom du wake word.
        try:
            for entry in os.listdir(directory):
                lower = entry.lower()
                if _openwakeword_model_name() in lower and (
                    extension is None or lower.endswith(extension)
                ):
                    return os.path.join(directory, entry)
        except OSError:
            continue
    return None


def _download_openwakeword_model() -> str | None:
    """Télécharge le modèle 'hey_jarvis' dans le dossier utilisateur.

    Retourne le chemin du modèle téléchargé, ou ``None`` en cas d'échec.
    """
    if download_models is None:
        return None
    target = paths.openwakeword_models_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
        download_models([_openwakeword_model_name()], target_directory=str(target))
        return _find_openwakeword_model(".tflite") or _find_openwakeword_model(".onnx")
    except Exception as exc:
        print(f"[Wake Word] Téléchargement du modèle impossible : {exc}")
        return None


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

    # =========================================================
    # INTERRUPTION VOCALE (« stop » pendant une réponse)
    # =========================================================

    # Niveau RMS minimal (échelle int16) au-dessus duquel un bloc micro est
    # considéré comme de la parole utilisateur et non du bruit de fond.
    BARGE_IN_MIN_RMS = 900.0

    # Le bloc doit aussi dépasser ce multiple du niveau ambiant appris
    # pendant que Jarvis parle (voix de Jarvis renvoyée par les enceintes).
    # C'est le garde-fou anti « Jarvis s'interrompt lui-même ».
    BARGE_IN_FACTOR = 2.6

    # Nombre de blocs consécutifs (80 ms chacun) requis : évite de couper
    # la réponse sur un claquement de porte ou un clic de souris.
    BARGE_IN_BLOCKS = 3

    # Délai laissé au détecteur pour apprendre le niveau de l'écho avant
    # d'autoriser une interruption (et pour ignorer la fin de la phrase de
    # l'utilisateur qui déborde sur le début de la réponse).
    BARGE_IN_GRACE_SECONDS = 0.6

    # Après une interruption, on n'en déclenche pas une autre tout de suite.
    BARGE_IN_COOLDOWN_SECONDS = 1.5

    # Blocs micro conservés pendant que Jarvis parle : ils sont renvoyés à
    # Gemini au moment de l'interruption pour qu'il entende le mot complet
    # (« stop ») et pas seulement sa fin.
    BARGE_IN_PREBUFFER_BLOCKS = 8

    def __init__(self, on_input, presence_hook=None, voice_hook=None,
                 mic_enabled=None, wake_threshold=None,
                 volume_provider=None, listen_mode_provider=None,
                 barge_in_provider=None, on_barge_in=None):
        self.on_input = on_input

        # Hooks optionnels pour l'UI (voir src/ui.py).
        # presence_hook(state) : "loading" | "listening" | "hidden"
        # voice_hook(level)     : 0.0 .. 1.0 (niveau d'entrée micro)
        # mic_enabled()         : bool — micro coupé/rétabli depuis le menu.
        # wake_threshold()      : float — sensibilité du wake word depuis le menu.
        # volume_provider()     : int 0..100 — volume de la voix Jarvis.
        # listen_mode_provider(): bool — écoute continue (sans wake word).
        # barge_in_provider()   : bool — autorise l'interruption vocale.
        # on_barge_in()         : appelé quand l'utilisateur coupe la parole
        #                         à Jarvis (« stop ») ou clique sur Stop.
        self.presence_hook = presence_hook
        self.voice_hook = voice_hook
        self.mic_enabled = mic_enabled
        self.wake_threshold = wake_threshold
        self.volume_provider = volume_provider
        self.listen_mode_provider = listen_mode_provider
        self.barge_in_provider = barge_in_provider
        self.on_barge_in = on_barge_in
        self._last_voice_emit = 0.0

        self.running = False
        self.awake = False

        # True tant que Jarvis est en train de parler (réponse Gemini en cours).
        # Pendant ce temps, le timeout de conversation ne doit jamais se
        # déclencher : on ne retourne en veille que lorsque la réponse est finie.
        self.speaking = False
        self._speaking_lock = threading.Lock()

        # =====================================================
        # DÉTECTION D'INTERRUPTION (« stop » pendant la réponse)
        # =====================================================

        # Instant où Jarvis a commencé sa réponse (période de grâce).
        self._speaking_since = 0.0
        # Niveau ambiant appris pendant que Jarvis parle (écho des enceintes).
        self._barge_floor = 0.0
        # Blocs consécutifs au-dessus du seuil.
        self._barge_hits = 0
        # Dernière interruption déclenchée (anti-rebond).
        self._last_barge_in = 0.0
        # Blocs micro capturés pendant la parole de Jarvis : rejoués vers
        # Gemini au moment de l'interruption.
        self._barge_prebuffer = collections.deque(
            maxlen=self.BARGE_IN_PREBUFFER_BLOCKS
        )
        self._barge_lock = threading.Lock()

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

        model_path = _find_openwakeword_model(".tflite") or _find_openwakeword_model(".onnx")

        def _load(framework: str, path: str | None) -> bool:
            try:
                kwargs = {"wakeword_models": [path] if path else [_openwakeword_model_name()]}
                self.wake_model = Model(inference_framework=framework, **kwargs)
                print(f"[Wake Word] Modèle chargé ({framework}).")
                return True
            except Exception as exc:
                print(f"[Wake Word] Chargement ({framework}) impossible : {exc}")
                return False

        # 1. Chemin explicite trouvé (bundle, dossier utilisateur, site-packages).
        if model_path and _load("tflite", model_path):
            return
        if model_path and _load("onnx", model_path):
            return

        # 2. Chargement par nom (openwakeword résout seul, si le modèle est présent
        #    dans son répertoire par défaut).
        if _load("tflite", None):
            return

        # 3. Téléchargement contrôlé vers le dossier utilisateur (premier lancement).
        downloaded = _download_openwakeword_model()
        if downloaded and _load("tflite", downloaded):
            return
        if downloaded and _load("onnx", downloaded):
            return

        # 4. État dégradé : aucune détection de wake word, Jarvis fonctionne
        #    quand même (mode « écoute continue » ou réveil manuel).
        print("[Wake Word] Aucun modèle wake-word disponible après téléchargement.")
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

                self._handle_awake_block(pcm)

                continue

            # =================================================
            # MODE VEILLE
            # =================================================

            self._handle_idle_block(pcm)

    def _handle_awake_block(self, pcm) -> None:
        """Conversation en cours : micro -> Gemini, avec détection du
        « stop » lorsque Jarvis est en train de parler."""
        # L'utilisateur coupe la parole à Jarvis (« stop ») : on arrête
        # immédiatement la lecture et on laisse passer sa phrase vers Gemini.
        if self._detect_barge_in(pcm):
            self._trigger_barge_in(source="voix")
        else:
            self._remember_recent_block(pcm)

        try:
            # Pendant une conversation, le wake word
            # n'est absolument pas analysé.
            self.on_input(pcm)

        except Exception as e:
            print("[Micro -> Gemini] Erreur :", e)

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

    # =========================================================
    # INTERRUPTION VOCALE (« stop » pendant une réponse)
    # =========================================================

    def _barge_in_allowed(self) -> bool:
        """Interruption vocale activée (menu radial / réglages)."""
        if self.barge_in_provider is None:
            return True
        try:
            return bool(self.barge_in_provider())
        except Exception:
            return True

    @staticmethod
    def _block_rms(pcm) -> float:
        """Niveau moyen (RMS, échelle int16) d'un bloc micro."""
        try:
            samples = np.frombuffer(pcm, dtype=np.int16)
            if samples.size == 0:
                return 0.0
            value = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
        except Exception:
            return 0.0
        if value != value:  # NaN
            return 0.0
        return value

    def _learn_barge_floor(self, rms: float) -> None:
        """Apprend le niveau ambiant pendant que Jarvis parle.

        Attaque rapide / relâchement lent : le plancher colle aux pics de
        l'écho des enceintes, ce qui empêche Jarvis de s'interrompre
        lui-même, tout en restant bas si l'utilisateur porte un casque.
        """
        alpha = 0.35 if rms > self._barge_floor else 0.05
        self._barge_floor += alpha * (rms - self._barge_floor)

    def _remember_recent_block(self, pcm) -> None:
        """Conserve les derniers blocs micro captés pendant la réponse.

        Ils sont réémis vers Gemini au moment de l'interruption : sans cela
        le début du mot « stop » serait perdu (détection en ~240 ms).
        """
        with self._speaking_lock:
            speaking = self.speaking
        if not speaking:
            if self._barge_prebuffer:
                with self._barge_lock:
                    self._barge_prebuffer.clear()
            return
        with self._barge_lock:
            self._barge_prebuffer.append(pcm)

    def _detect_barge_in(self, pcm) -> bool:
        """Vrai si l'utilisateur parle par-dessus la réponse de Jarvis."""
        with self._speaking_lock:
            speaking = self.speaking
            since = self._speaking_since

        if not speaking:
            self._barge_hits = 0
            return False

        if not self._barge_in_allowed():
            self._barge_hits = 0
            return False

        now = time.monotonic()
        if now - self._last_barge_in < self.BARGE_IN_COOLDOWN_SECONDS:
            return False

        rms = self._block_rms(pcm)

        # Période de grâce : on se contente d'apprendre le niveau de l'écho.
        if now - since < self.BARGE_IN_GRACE_SECONDS:
            self._learn_barge_floor(rms)
            self._barge_hits = 0
            return False

        threshold = max(
            self.BARGE_IN_MIN_RMS,
            self._barge_floor * self.BARGE_IN_FACTOR,
        )

        if rms >= threshold:
            self._barge_hits += 1
            if self._barge_hits >= self.BARGE_IN_BLOCKS:
                self._barge_hits = 0
                self._last_barge_in = now
                return True
            return False

        self._barge_hits = 0
        self._learn_barge_floor(rms)
        return False

    def _trigger_barge_in(self, source: str = "voix") -> None:
        """Coupe la réponse en cours et prévient le backend Gemini."""
        self._last_barge_in = time.monotonic()

        # 0. Snapshot AVANT clear_output() : la fin de la parole vide le
        #    pré-tampon, il faut donc le récupérer maintenant.
        with self._barge_lock:
            pending = list(self._barge_prebuffer)
            self._barge_prebuffer.clear()

        # 1. Silence immédiat : c'est ce que l'utilisateur attend.
        self.clear_output()

        # 2. Le backend autorise de nouveau l'envoi du micro (le tour de
        #    Gemini sera interrompu côté serveur dès qu'il entend la voix).
        hook = self.on_barge_in
        if hook is not None:
            try:
                hook()
            except Exception as exc:
                print(f"[Interruption] Backend indisponible : {exc}")

        # 3. Rejouer les blocs captés juste avant la détection pour que la
        #    phrase de l'utilisateur arrive entière.
        for block in pending:
            try:
                self.on_input(block)
            except Exception:
                break

        print(f"[Jarvis] Interruption ({source}) : j'arrête de parler.")

    def stop_speaking(self) -> bool:
        """Interruption manuelle (bouton du menu, raccourci clavier).

        Renvoie True si Jarvis était effectivement en train de parler.
        """
        with self._speaking_lock:
            was_speaking = self.speaking
        self._trigger_barge_in(source="manuelle")
        return was_speaking

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
        speaking = bool(speaking)
        with self._speaking_lock:
            was_speaking = self.speaking
            self.speaking = speaking
            if speaking and not was_speaking:
                # Nouvelle réponse : le détecteur d'interruption repart d'une
                # page blanche (période de grâce + compteur de blocs).
                self._speaking_since = time.monotonic()

        if speaking != was_speaking:
            # Changement d'état : le détecteur d'interruption repart propre
            # (compteur de blocs et pré-tampon micro).
            self._barge_hits = 0
            with self._barge_lock:
                self._barge_prebuffer.clear()

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