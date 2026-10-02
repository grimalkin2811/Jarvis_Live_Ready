import collections
import os
import queue
import threading
import time

import numpy as np
import sounddevice as sd

from . import wakeword as wakeword_utils


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

    # v1.7.5 ter (bug A, validation réelle) : durée MAXIMALE pendant laquelle
    # un tour « ouvert mais pas encore résolu » (``note_turn_open`` appelé,
    # ``note_turn_resolved`` pas encore reçu) suspend le minuteur de
    # conversation. Ce n'est volontairement PAS une attente indéfinie : si
    # ``GeminiLive`` ne clôt jamais le tour (plantage, connexion perdue sans
    # notification), ce filet de sécurité évite de rester éveillé pour
    # toujours. Doit rester nettement en-dessous du ``TURN_TIMEOUT`` des
    # harnais de validation réelle (60 s) tout en couvrant largement le pire
    # cas des relances S6 (``EMPTY_GENERATION_RETRY``, qui ne déclenchent
    # ``on_turn_resolved`` qu'une fois épuisées — plusieurs allers-retours
    # réseau, mais jamais plus de quelques secondes chacun en pratique).
    TURN_PENDING_MAX_GRACE_SECONDS = 45.0

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

    # Taille de l'historique de traçage audio (instrumentation du timer de
    # silence : chaque réarmement doit pouvoir être expliqué a posteriori).
    TRACE_MAX_EVENTS = 512

    # PORTE MICRO ANTI-ÉCHO (correctif de la boucle « Je vous écoute »).
    # Après la vidange des haut-parleurs, le micro reste encore fermé pour
    # Gemini pendant ce délai : réverbération de la pièce et latence du
    # mixeur système continuent de renvoyer la voix de Jarvis un court
    # instant après le dernier échantillon joué. Ce n'est PAS un délai
    # arbitraire : c'est la traîne physique entre « plus rien en file » et
    # « la pièce est silencieuse ». L'interruption (barge-in) ne la subit
    # pas : la sortie est vidée, la porte s'ouvre immédiatement.
    MIC_ECHO_GUARD_SECONDS = 0.25

    def __init__(self, on_input, presence_hook=None, voice_hook=None,
                 mic_enabled=None, wake_threshold=None,
                 volume_provider=None, listen_mode_provider=None,
                 barge_in_provider=None, on_barge_in=None,
                 post_response_provider=None,
                 output_level_hook=None,
                 wakeword_download=None):
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
        # post_response_provider(): bool — Jarvis reste-t-il à l'écoute après
        #                         sa réponse ? (None = comportement historique :
        #                         la fenêtre de suivi est accordée.)
        self.presence_hook = presence_hook
        self.voice_hook = voice_hook
        self.mic_enabled = mic_enabled
        self.wake_threshold = wake_threshold
        self.volume_provider = volume_provider
        self.listen_mode_provider = listen_mode_provider
        self.barge_in_provider = barge_in_provider
        self.on_barge_in = on_barge_in
        self.post_response_provider = post_response_provider
        # output_level_hook(level) : 0.0 .. 1.0 — niveau de la voix de Jarvis
        # (v1.7.0, utilisé par le halo du Desktop Mode). Calculé dans
        # ``play()``, c'est-à-dire dans le thread asyncio et JAMAIS dans le
        # callback temps réel de la carte son.
        self.output_level_hook = output_level_hook
        self._last_output_emit = 0.0
        self._last_voice_emit = 0.0

        # =====================================================
        # INSTRUMENTATION DU TIMER DE SILENCE (v1.7.2)
        # -----------------------------------------------------
        # La fenêtre de conversation (``follow_up_until``) n'est PAS un
        # QTimer : c'est une échéance unique vérifiée par _check_timeout à
        # chaque tour de _audio_worker. Chaque écriture passe par
        # _arm_follow_up(), qui numérote le réarmement (epoch) et enregistre
        # le contexte complet : raison, thread, état, énergie micro, audio
        # de sortie encore en attente. Objectif : pouvoir répondre à
        # « pourquoi les 8 secondes se réinitialisent-elles ? » par
        # l'analyse des événements, pas par devinette.
        # ``JARVIS_AUDIO_TRACE=1`` affiche chaque événement en console ; la
        # collecte en mémoire (bornée) est toujours active pour les tests.
        self._trace = collections.deque(maxlen=self.TRACE_MAX_EVENTS)
        self._trace_lock = threading.Lock()
        self._follow_up_epoch = 0
        self._last_follow_up_arm = 0.0
        # Porte micro anti-écho : dernier instant où de la voix de Jarvis
        # était encore en file de sortie, et dernier état de la porte.
        self._output_last_pending_ts = 0.0
        self._mic_gate_state: bool | None = None
        # Dernier bloc micro observé (RMS/peak int16) : contexte audio des
        # événements déclenchés ailleurs que par un bloc micro.
        self._last_mic_rms = 0.0
        self._last_mic_peak = 0
        self._last_mic_seen = 0.0

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
        # TOUR GEMINI EN ATTENTE (v1.7.5 ter — correctif bug A)
        # -----------------------------------------------------
        # ``note_turn_open``/``note_turn_resolved`` sont appelés par
        # ``GeminiLive`` (callbacks ``on_turn_open``/``on_turn_resolved``,
        # cf. src/gemini_live.py) pour signaler qu'un VRAI tour utilisateur
        # est en train d'être généré, indépendamment du contenu déjà produit.
        # Avant ce correctif, ``_check_timeout`` ne connaissait que
        # ``_voice_audible()`` (parole déjà émise ou en file) : si Gemini
        # mettait plusieurs secondes à produire le premier octet de sa
        # réponse, la fenêtre de conversation pouvait expirer PENDANT la
        # génération et endormir Jarvis juste avant que la réponse arrive —
        # perdant alors silencieusement toute relance posée sans mot de
        # réveil juste après (bug constaté en validation réelle, scénario
        # « deux questions rapides »).
        # =====================================================
        self._turn_pending_since: float | None = None
        self._turn_pending_lock = threading.Lock()

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

        # Chargement centralisé (src/wakeword.py) : résolution explicite des
        # trois modèles ONNX (wake word + melspectrogram + embedding), ONNX
        # d'abord (framework supporté en distribution), TFLite en repli
        # opportuniste, clé de score détectée dynamiquement.
        # ``wakeword_download=None`` suit JARVIS_NO_MODEL_DOWNLOAD.
        self.wake_model = None
        self.wake_key = wakeword_utils.WAKEWORD_NAME
        self.wake_framework = ""
        try:
            model, key, framework = wakeword_utils.load_best_model(
                download=wakeword_download, verbose=True
            )
        except Exception as exc:
            print(f"[Wake Word] Chargement impossible : {exc}")
            model, key, framework = None, "", ""
        if model is None:
            # État dégradé : aucune détection de wake word, Jarvis fonctionne
            # quand même (mode « écoute continue » ou réveil manuel).
            return
        self.wake_model = model
        self.wake_key = key or wakeword_utils.WAKEWORD_NAME
        self.wake_framework = framework

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
    # TRAÇAGE (instrumentation v1.7.2)
    # =========================================================

    @staticmethod
    def _trace_enabled() -> bool:
        """Affichage console des événements audio (``JARVIS_AUDIO_TRACE``)."""
        return os.getenv("JARVIS_AUDIO_TRACE", "").strip() not in ("", "0", "false", "non")

    def _record_trace(self, kind: str, **fields) -> None:
        """Enregistre un événement audio (borné, thread-safe).

        Ne doit JAMAIS être appelé depuis le callback temps réel de la carte
        son (``_in``/``_out``) : les valeurs micro y sont mémorisées sous
        forme de simples flottants, l'événement est construit plus tard dans
        le thread de traitement ou la boucle asyncio.
        """
        event = {
            "ts": time.monotonic(),
            "kind": kind,
            "thread": threading.current_thread().name,
            "awake": self.awake,
            "speaking": self.speaking,
            "mic_rms": round(self._last_mic_rms, 1),
            "mic_peak": self._last_mic_peak,
            "out_pending_s": round(self.output_pending_seconds(), 3),
            **fields,
        }
        with self._trace_lock:
            self._trace.append(event)
        if self._trace_enabled():
            details = " ".join(
                f"{key}={value}" for key, value in event.items()
                if key not in ("ts", "kind", "thread", "awake", "speaking")
            )
            print(
                f"[AUDIO] {kind} id={event.get('id', '-')} {details} "
                f"awake={int(event['awake'])} speaking={int(event['speaking'])} "
                f"thread={event['thread']}",
                flush=True,
            )

    def trace_events(self) -> list[dict]:
        """Copie des événements audio collectés (tests et diagnostics)."""
        with self._trace_lock:
            return list(self._trace)

    def _arm_follow_up(
        self,
        reason: str,
        *,
        src: str = "none",
        vad: str | None = None,
    ) -> None:
        """Écriture UNIQUE de l'échéance de conversation.

        Tout réarmement du « timer de 8 secondes » passe ici : réveil,
        fin de tour Gemini (``extend_listening``), interruption
        (``clear_output``) et renouvellement du mode écoute continue. Chaque
        réarmement reçoit un identifiant (epoch) et est journalisé avec sa
        raison — il n'existe donc pas de réarmement inexpliqué.
        """
        now = time.monotonic()
        self._follow_up_epoch += 1
        since_last = (
            round(now - self._last_follow_up_arm, 3)
            if self._last_follow_up_arm
            else None
        )
        self._last_follow_up_arm = now
        self.follow_up_until = now + self.FOLLOW_UP_SECONDS
        self._record_trace(
            "silence_timer RESET",
            id=self._follow_up_epoch,
            reason=reason,
            src=src,
            mic_age_s=round(now - self._last_mic_seen, 3) if self._last_mic_seen else None,
            vad=vad,
            since_last_reset_s=since_last,
        )

    def output_pending_bytes(self) -> int:
        """Octets de voix de Jarvis pas encore sortis des haut-parleurs.

        Somme de la file réseau (``q``) et du tampon déjà délivré à la carte
        son (``buffer``) : c'est l'avance audio réelle, celle que l'utilisateur
        entend encore. Primitives utilisées par le traçage et par la porte
        micro anti-écho.
        """
        with self.lock:
            queued = sum(len(chunk) for chunk in list(self.q.queue))
            return queued + len(self.buffer)

    def output_pending_seconds(self) -> float:
        """Secondes de voix de Jarvis encore à jouer (haut-parleurs)."""
        return self.output_pending_bytes() / float(
            self.OUTPUT_RATE * self.CHANNELS * self.SAMPLE_WIDTH
        )

    def _mic_gate_open(self) -> bool:
        """Le micro peut-il être transmis à Gemini sans risque d'écho ?

        Non tant que de la voix de Jarvis reste à jouer (file + tampon) ou
        que la traîne acoustique qui suit le dernier échantillon n'est pas
        écoulée. La détection d'interruption locale (barge-in) n'est PAS
        concernée : elle lit le micro brut et vide la sortie elle-même, ce
        qui rouvre la porte immédiatement.
        """
        if self.output_pending_bytes() > 0:
            # Mémorise le dernier instant où de la voix était encore à
            # jouer : la garde acoustique court à partir de là.
            self._output_last_pending_ts = time.monotonic()
            return False
        if self._output_last_pending_ts <= 0.0:
            return True
        return (
            time.monotonic() - self._output_last_pending_ts
        ) >= self.MIC_ECHO_GUARD_SECONDS

    def _trace_mic_gate(self, open_now: bool) -> None:
        """Journalise les transitions de la porte micro (état initial exclu)."""
        if open_now == self._mic_gate_state:
            return
        self._mic_gate_state = open_now
        self._record_trace(
            "mic_gate", state=open_now, src="anti-écho"
        )

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

        # Niveau d'entrée (RMS) pour l'énergie vocale de l'UI, et mémorisé
        # pour l'instrumentation du timer de silence (contexte audio des
        # réarmements). Calcul léger, effectué à chaque bloc de 80 ms.
        if self._is_mic_enabled():
            try:
                samples = np.frombuffer(indata, dtype=np.int16)
                if samples.size:
                    self._last_mic_rms = float(
                        np.sqrt(np.mean(samples.astype(np.float64) ** 2))
                    )
                    self._last_mic_peak = int(np.max(np.abs(samples)))
                    self._last_mic_seen = time.monotonic()
                if self.voice_hook is not None:
                    self._emit_voice(min(1.0, self._last_mic_rms / 3000.0))
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

        was_output_pending = False

        while self.running:

            # IMPORTANT :
            # Vérification du timeout à CHAQUE tour,
            # même lorsque le micro produit des blocs silencieux.
            self._check_timeout()

            # Détection de la vidange réelle de la sortie audio (les
            # haut-parleurs se taisent) : transition > 0 octets -> 0 octet.
            # Événement clé pour diagnostiquer l'écart entre la fin de tour
            # SERVEUR (turn_complete) et la fin de voix EFFECTIVEMENT jouée.
            output_pending = self.output_pending_bytes() > 0 or not self.q.empty()
            if was_output_pending and not output_pending and self.audio_started:
                # Observation seule : audio_started reste géré par _out
                # (prébuffer) et clear_output (interruption).
                self._record_trace("output_drained", src="haut-parleurs")
            was_output_pending = output_pending

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

        # PORTE ANTI-ÉCHO — correctif de la boucle « Je vous écoute » :
        # tant que la voix de Jarvis sort des haut-parleurs (file de sortie
        # non vide) ou que sa traîne acoustique n'est pas écoulée, le micro
        # n'est PAS transmis à Gemini. Sans cette porte, la VAD du serveur
        # entend l'écho de Jarvis, commit un tour « utilisateur » fantôme,
        # le modèle répond, et chaque turn_complete ré-arme la fenêtre de
        # 8 secondes : boucle stable d'environ 2 secondes sans parole
        # humaine. La détection d'interruption ci-dessus reste active :
        # l'utilisateur qui parle fort vide la sortie et rouvre la porte.
        if not self._mic_gate_open():
            self._trace_mic_gate(False)
            return
        self._trace_mic_gate(True)

        try:
            # Pendant une conversation, le wake word
            # n'est absolument pas analysé.
            self.on_input(pcm)

        except Exception as e:
            print("[Micro -> Gemini] Erreur :", e)

    def _handle_idle_block(self, pcm) -> None:
        """En veille : réveil automatique (écoute continue) ou wake word."""
        # Écoute continue activée depuis le menu : Jarvis se réveille
        # tout seul et reste actif sans exiger « Hey Jarvis ». Le
        # réveil respecte le même anti-rebond que le wake word : sans
        # lui, un Jarvis rendu endormi (perte de connexion, erreur) est
        # réveilli à CHAQUE bloc de 80 ms — affichage et fenêtre de 8 s
        # réarmés en rafale, sans aucune parole humaine.
        if self._listen_mode_active():
            if (
                time.monotonic() - self.last_wake_time
                >= self.WAKE_COOLDOWN_SECONDS
            ):
                self._wake(reason="listen_mode", src="mic")
            return

        if self._detect_wake_word(pcm):
            self._wake(reason="wake_word", src="mic")

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

    def _post_response_enabled(self) -> bool:
        """Écoute post-réponse : Jarvis reste-t-il éveillé après sa réponse ?

        ``True`` (défaut, y compris sans provider branché) conserve le
        comportement historique : une fenêtre de ``FOLLOW_UP_SECONDS`` est
        ouverte à la fin du tour de Gemini. ``False`` renvoie immédiatement
        Jarvis en veille : « Hey Jarvis » redevient le seul moyen de réveil.
        """
        if self.post_response_provider is None:
            return True
        try:
            return bool(self.post_response_provider())
        except Exception:
            return True

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

    def _voice_audible(self) -> bool:
        """La voix de Jarvis est audible : génération en cours OU lecture
        locale pas encore terminée (traîne de la file de sortie).

        C'est l'état pertinent pour l'interruption vocale : l'utilisateur
        veut couper une voix qu'il ENTEND. Avant v1.7.2, seule la génération
        (``speaking``) comptait : pendant la traîne de lecture qui suit
        ``turn_complete`` — plusieurs secondes quand le réseau envoie par
        rafales — le barge-in vocal était impossible, seul le bouton Stop
        restait efficace.
        """
        with self._speaking_lock:
            speaking = self.speaking
        return speaking or self.output_pending_bytes() > 0

    # =========================================================
    # TOUR GEMINI EN ATTENTE (v1.7.5 ter — correctif bug A)
    # =========================================================

    def note_turn_open(self) -> None:
        """Un VRAI tour Gemini vient de s'ouvrir (``GeminiLive.on_turn_open``).

        Appelé dès qu'une transcription utilisateur réelle démarre un
        nouveau tour — AVANT que la moindre réponse (audio ou texte) ait pu
        être produite. Suspend ``_check_timeout`` le temps que la génération
        aboutisse, borné par ``TURN_PENDING_MAX_GRACE_SECONDS``.
        """
        with self._turn_pending_lock:
            self._turn_pending_since = time.monotonic()
        self._record_trace("turn_pending", state="open")

    def note_turn_resolved(self) -> None:
        """Le tour en cours vient d'être clos (``GeminiLive.on_turn_resolved``).

        Déclenché inconditionnellement par ``GeminiLive`` (même pour un tour
        resté sans contenu) : lève la suspension posée par
        ``note_turn_open`` quel que soit le résultat obtenu.
        """
        with self._turn_pending_lock:
            self._turn_pending_since = None
        self._record_trace("turn_pending", state="resolved")

    def _turn_pending(self) -> bool:
        """Vrai si un tour Gemini est en cours de génération sans réponse.

        Filet de sécurité : au-delà de ``TURN_PENDING_MAX_GRACE_SECONDS``
        sans ``note_turn_resolved``, on cesse de faire confiance au signal
        (connexion perdue sans notification, par exemple) plutôt que de
        suspendre le minuteur indéfiniment.
        """
        with self._turn_pending_lock:
            since = self._turn_pending_since
        if since is None:
            return False
        return (time.monotonic() - since) <= self.TURN_PENDING_MAX_GRACE_SECONDS

    def _remember_recent_block(self, pcm) -> None:
        """Conserve les derniers blocs micro captés pendant la réponse.

        Ils sont réémis vers Gemini au moment de l'interruption : sans cela
        le début du mot « stop » serait perdu (détection en ~240 ms).
        """
        if not self._voice_audible():
            if self._barge_prebuffer:
                with self._barge_lock:
                    self._barge_prebuffer.clear()
            return
        with self._barge_lock:
            self._barge_prebuffer.append(pcm)

    def _detect_barge_in(self, pcm) -> bool:
        """Vrai si l'utilisateur parle par-dessus la réponse de Jarvis.

        La « réponse » inclut sa traîne de lecture locale : tant que la
        voix sort des haut-parleurs, couper la parole est légitime (v1.7.2).
        """
        audible = self._voice_audible()
        with self._speaking_lock:
            since = self._speaking_since

        if not audible:
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
        self._record_trace("barge_in", source=source, src="micro/manuel")

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

            # La clé dépend du mode de chargement (nom : "hey_jarvis",
            # chemin : "hey_jarvis_v0.1") : elle est détectée au chargement.
            key = getattr(self, "wake_key", None) or "hey_jarvis"
            try:
                score = float(scores.get(key, 0.0))
            except Exception:
                score = 0.0
            if score <= 0.0 and key != "hey_jarvis":
                # Filet de sécurité : repli sur la clé canonique.
                try:
                    score = float(scores.get("hey_jarvis", 0.0))
                except Exception:
                    score = 0.0

            # Pour afficher tous les scores :
            #
            # print(
            #     f"\r[Wake Word] Score : {score:.4f}",
            #     end="",
            #     flush=True
            # )

            if score >= threshold:
                self._last_wake_score = round(score, 3)

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

    def _emit_output_level(self, pcm) -> None:
        """Niveau de la voix de Jarvis (v1.7.0), throttlé à ~20 Hz.

        Appelé depuis ``play()`` (thread asyncio), jamais depuis le callback
        de la carte son : le chemin temps réel reste intact.
        """
        if self.output_level_hook is None:
            return
        now = time.monotonic()
        if now - self._last_output_emit < 0.05:
            return
        self._last_output_emit = now
        try:
            samples = np.frombuffer(pcm, dtype=np.int16)
            if samples.size == 0:
                return
            rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
            self.output_level_hook(min(1.0, rms / 6000.0))
        except Exception:
            pass

    # =========================================================
    # RÉVEIL DE JARVIS
    # =========================================================

    def _wake(self, reason: str = "wake", src: str = "mic"):

        now = time.monotonic()

        self.awake = True
        self._set_speaking(False)
        self.last_wake_time = now
        self._emit_presence("listening")

        # Sécurité : retour en veille si Gemini ne répond pas
        self._arm_follow_up(reason, src=src, vad="reveil")

        self._record_trace(
            "wake",
            reason=reason,
            score_dernier_wake_word=getattr(self, "_last_wake_score", None),
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

        # Écoute post-réponse désactivée (menu Voice → « Listen After Reply ») :
        # aucune fenêtre de suivi n'est accordée, on retourne en veille tout de
        # suite. C'est le même chemin que le timeout de conversation, donc le
        # wake word « Hey Jarvis » redevient l'unique moyen de réveil.
        if not self._post_response_enabled():
            self._go_to_sleep("Écoute post-réponse désactivée.")
            return

        # Fin de tour Gemini : la fenêtre de suivi repart de zéro. C'est le
        # SEUL réarmement déclenché par le serveur — chaque fin de tour
        # (même sans parole utilisateur) repart d'ici, d'où l'importance
        # du contexte audio journalisé (micro vs sortie en attente).
        self._arm_follow_up("turn_complete", src="serveur")
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
            self._record_trace(
                "speaking", value=speaking, src="pipeline audio"
            )

    # =========================================================
    # TIMEOUT / RETOUR EN VEILLE
    # =========================================================

    def _check_timeout(self):

        if not self.awake:
            return

        # Pendant que la voix de Jarvis est audible — génération en cours OU
        # lecture locale pas encore terminée (v1.7.2 : la traîne de la file
        # de sortie fait partie de la « réponse pas finie ») — la fenêtre de
        # conversation est suspendue : on ne retourne en veille qu'une fois
        # que l'utilisateur n'entend plus Jarvis.
        if self._voice_audible():
            return

        # v1.7.5 ter (bug A, validation réelle) : un tour est ouvert
        # (``note_turn_open``) mais Gemini n'a encore produit NI audio NI
        # texte — ``_voice_audible()`` est donc faux alors que la réponse
        # arrive encore. Sans ce garde-fou, la fenêtre de conversation
        # pouvait expirer pendant la génération et endormir Jarvis juste
        # avant que la réponse (et toute relance immédiate de
        # l'utilisateur) n'arrive. Borné par ``TURN_PENDING_MAX_GRACE_SECONDS``
        # pour ne jamais rester suspendu indéfiniment en cas d'anomalie.
        if self._turn_pending():
            return

        if time.monotonic() >= self.follow_up_until:

            # Écoute continue : la fenêtre de conversation se renouvelle
            # indéfiniment tant que le mode reste actif.
            if self._listen_mode_active():
                self._arm_follow_up("listen_mode_renew", src="aucune (mode)")
                return

            self._go_to_sleep("timeout de conversation")

    def _go_to_sleep(self, reason: str = "") -> None:
        """Retour en veille : seul « Hey Jarvis » peut réveiller Jarvis.

        Chemin unique de mise en veille (timeout de conversation ou écoute
        post-réponse désactivée) : l'état, la présence UI et le modèle de
        wake word sont remis à zéro de la même façon dans les deux cas.
        """
        self.awake = False
        self._emit_presence("hidden")

        self._record_trace("sleep", reason=reason or "timeout")

        try:
            self.wake_model.reset()
        except Exception:
            pass

        if reason:
            print(f"[Jarvis] {reason}")
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

            # Remplir le buffer avec les chunks Gemini.
            # NOTE comptage (correctif v1.7.2) : ``_queued_bytes`` représente
            # la file TOTALE (q + buffer). Décompter au passage q -> buffer
            # ET à la consommation par la carte son reviendrait à compter
            # deux fois chaque octet : le compteur passait négatif puis était
            # ramené à 0, ce qui cassait le plafond MAX_QUEUED_SECONDS.
            # On ne décompte donc QU'À LA SORTIE réelle (outdata).
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

        self._emit_output_level(data)

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
        # La sortie est vide : la porte anti-écho s'ouvre immédiatement
        # (l'utilisateur qui interrompt doit être entendu sans délai).
        self._output_last_pending_ts = 0.0
        # L'utilisateur a interrompu Jarvis : il ne parle plus.
        self._set_speaking(False)
        if self.awake:
            # Uniquement si une conversation est réellement ouverte : une
            # interruption résiduelle ne doit jamais rouvrir l'écoute d'un
            # Jarvis endormi (anciens callbacks, arrêt manuel hors tour).
            self._arm_follow_up("interrupt", src="sortie videe")
            self._emit_presence("listening")
        else:
            self._record_trace(
                "clear_output ignore", reason="jarvis endormi", src="sortie videe"
            )

    # =========================================================
    # ARRÊT
    # =========================================================

    def stop(self):

        self.running = False
        self.audio_started = False
        # Fin de vie : plus aucune fenêtre de conversation ne doit être
        # armée par la suite (le clear_output final devient un no-op).
        self.awake = False
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