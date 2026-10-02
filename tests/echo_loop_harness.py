"""Harness de reproduction de la boucle d'écho « Je vous écoute ».

Pourquoi ce module existe
-------------------------
Symptôme rapporté : après une requête utilisateur, Jarvis répète « Je vous
écoute » environ toutes les 2 secondes alors que l'utilisateur ne dit plus
rien, et la fenêtre d'écoute de 8 secondes semble se réinitialiser en boucle.

Ce harness exécute le **vrai pipeline audio** de Jarvis :

* la vraie ``AudioIO`` (callback micro, thread de traitement, file de sortie,
  deadline ``follow_up_until``) sur une fausse carte son **pilotée par des
  threads réels** (capture 80 ms, restitution 42,67 ms) ;
* le vrai pont micro ``mic() -> can_send() -> send_audio()`` (identique à
  ``src/main.py`` et ``src/ui.py``) ;
* la vraie ``GeminiLive`` (``connect``/``receive_loop``/reconnexion) sur le
  faux serveur Live (``tests/live_harness.py``) étendu d'une **VAD serveur**
  par énergie, comme le fait Gemini Live ;

…avec un **modèle acoustique** : le micro capte la parole de l'utilisateur
 programmée par le test **plus l'écho retardé des haut-parleurs** (la voix de
Jarvis rejouée dans la pièce), plus un bruit de fond.

La boucle reproducible est alors purement endogène :

    réponse de Jarvis -> haut-parleurs -> écho micro -> VAD serveur
    -> nouveau tour sans parole humaine -> réponse « Je vous écoute. »
    -> turn_complete -> extend_listening (reset 8 s) -> …

Compteurs : tours « fantômes » (committés par le serveur sans parole
utilisateur), réarmements du timer de silence, transitions d'état, sessions.
Aucun réseau, aucun matériel réel, aucune API key.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time
import types
import unittest.mock as _mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pas de téléchargement de modèles wake word pendant les tests.
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")


# ---------------------------------------------------------------------------
# Fausse carte son : threads réels de capture et de restitution
# ---------------------------------------------------------------------------

#: Carte active (installée par EchoLab). Hors EchoLab, les flux factices sont
#: inertes : les autres suites de tests ne sont pas affectées.
_ACTIVE_CARD: "EchoSoundCard | None" = None


def _install_fake_sounddevice() -> None:
    """Installe la fausse carte son (avant l'import de ``src.audio``).

    Contrairement aux fakes inertes des autres suites, ces flux délèguent à
    la carte active si — et seulement si — un ``EchoLab`` est en cours : la
    capture appelle le vrai ``AudioIO._in`` toutes les 80 ms et la
    restitution le vrai ``AudioIO._out`` toutes les 42,67 ms.

    IMPORTANT : les attributs sont posés sur l'objet module DÉJÀ présent
    dans ``sys.modules`` s'il existe (les autres suites de tests installent
    un fake inerte avant celle-ci, et ``src.audio`` garde une référence
    directe à ce module) : remplacer ``sys.modules`` ne suffirait pas, il
    faut patcher l'objet existant — ``sd.RawOutputStream(...)`` est résolu
    à l'APPEL, pas à l'import.
    """
    existing = sys.modules.get("sounddevice")
    sd = existing if existing is not None else types.ModuleType("sounddevice")

    class _FakeRawInputStream:
        def __init__(self, samplerate=None, channels=None, dtype=None,
                     blocksize=None, callback=None):
            self.samplerate = samplerate
            self.channels = channels
            self.dtype = dtype
            self.blocksize = blocksize
            self.callback = callback
            self._card = None

        def start(self):
            card = _ACTIVE_CARD
            if card is not None:
                card.attach_input(self)

        def stop(self):
            if self._card is not None:
                self._card.detach_input(self)

        def close(self):
            self.stop()

    class _FakeRawOutputStream:
        def __init__(self, samplerate=None, channels=None, dtype=None,
                     blocksize=None, callback=None):
            self.samplerate = samplerate
            self.channels = channels
            self.dtype = dtype
            self.blocksize = blocksize
            self.callback = callback
            self._card = None

        def start(self):
            card = _ACTIVE_CARD
            if card is not None:
                card.attach_output(self)

        def stop(self):
            if self._card is not None:
                self._card.detach_output(self)

        def close(self):
            self.stop()

    sd.RawInputStream = _FakeRawInputStream
    sd.RawOutputStream = _FakeRawOutputStream
    sys.modules["sounddevice"] = sd
    # Si src.audio a déjà été importé (suite complète), son ``sd`` est le
    # même objet : la surcharge s'applique sans rebinding.


_install_fake_sounddevice()

import numpy as np  # noqa: E402

from src.audio import AudioIO  # noqa: E402
from src.conversation import ConversationContext  # noqa: E402
from src.gemini_live import GeminiLive  # noqa: E402
from tests.live_harness import (  # noqa: E402
    FakeClient,
    FakeLiveServer,
    WireEvent,
    msg_model_transcript,
    msg_turn_complete,
    msg_user_transcript,
)
from tests.live_harness import _Message, _ResumptionUpdate  # noqa: E402


# ---------------------------------------------------------------------------
# Modèle acoustique : parole utilisateur + écho des haut-parleurs + bruit
# ---------------------------------------------------------------------------


class AcousticPath:
    """Ce que le microphone entend physiquement, à 16 kHz.

    * ``user``    : parole humaine programmée par le test (intervalles) ;
    * ``echo``    : sortie haut-parleurs retardée de ``delay`` et atténuée
                    d'``echo_gain`` ( Jarvis entend sa propre voix) ;
    * ``noise``   : bruit de fond de la pièce (RMS constant).

    La sortie haut-parleurs est fournie par la carte son sous forme d'une
    chronologie d'enveloppes RMS (une par tick de restitution) : c'est un
    modèle de pression acoustique, pas un fichier audio — la VAD serveur ne
    voit de toute façon que l'énergie.
    """

    def __init__(self, *, echo_gain: float = 0.30, delay: float = 0.040,
                 noise_rms: float = 25.0):
        self.echo_gain = echo_gain
        self.delay = delay
        self.noise_rms = noise_rms
        self._lock = threading.Lock()
        # Intervalles de parole utilisateur : [(t0, t1, rms)]
        self._speech: list[tuple[float, float, float]] = []
        # Chronologie haut-parleurs : [(t_joué, rms)] — triée par insertion.
        self._speaker: list[tuple[float, float]] = []
        self._rng = np.random.default_rng(20260929)
        self._phase_user = 0.0
        self._phase_echo = 0.0

    # -- pilotage (thread de test) ------------------------------------------

    def speak(self, seconds: float, rms: float = 2600.0) -> None:
        """L'utilisateur parle pendant ``seconds`` (à partir de maintenant)."""
        now = time.monotonic()
        with self._lock:
            self._speech.append((now, now + seconds, rms))

    def speaker_played(self, rms: float) -> None:
        """La carte son vient de restituer un tick (appelé par le DAC)."""
        with self._lock:
            self._speaker.append((time.monotonic(), rms))

    # -- capture (thread ADC) -------------------------------------------------

    def _user_rms(self, t: float) -> float:
        for t0, t1, rms in reversed(self._speech):
            if t0 <= t <= t1:
                return rms
        return 0.0

    def _speaker_rms(self, t: float) -> float:
        """RMS haut-parleurs entendu à l'instant t (écho retardé)."""
        target = t - self.delay
        with self._lock:
            if not self._speaker:
                return 0.0
            best = 0.0
            for ts, rms in reversed(self._speaker[-200:]):
                if ts <= target:
                    best = rms
                    break
            return best

    def capture_block(self, rate: int, samples: int) -> bytes:
        """Bloc micro de ``samples`` échantillons (int16 mono)."""
        now = time.monotonic()
        dt = 1.0 / rate
        out = np.zeros(samples, dtype=np.float64)
        # Bruit de fond.
        out += self._rng.normal(0.0, self.noise_rms, samples)
        for i in range(samples):
            t = now - (samples - i) * dt  # le bloc couvre ~[now-80ms, now]
            # Parole utilisateur : porteuse vocale modulée.
            user = self._user_rms(t)
            if user > 0.0:
                self._phase_user += 2.0 * np.pi * 210.0 * dt
                out[i] += user * np.sqrt(2.0) * np.sin(self._phase_user) * (
                    0.75 + 0.25 * np.sin(2.0 * np.pi * 3.1 * t)
                )
            # Écho haut-parleurs.
            echo = self._speaker_rms(t) * self.echo_gain
            if echo > 0.0:
                self._phase_echo += 2.0 * np.pi * 300.0 * dt
                out[i] += echo * np.sqrt(2.0) * np.sin(self._phase_echo)
        return np.clip(out, -32768, 32767).astype(np.int16).tobytes()

    def user_spoke_between(self, t0: float, t1: float) -> bool:
        """Vrai si l'utilisateur a parlé ne serait-ce qu'un instant."""
        with self._lock:
            return any(a < t1 and b > t0 for a, b, _ in self._speech)


def _pcm_rms(data: bytes) -> float:
    samples = np.frombuffer(data, dtype=np.int16)
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))


def _tone_pcm(seconds: float, rms: float, rate: int) -> bytes:
    """Audio de réponse du modèle (porteuse vocale, amplitude = RMS voulu)."""
    n = int(rate * seconds)
    t = np.arange(n) / rate
    carrier = np.sin(2.0 * np.pi * 230.0 * t) * (0.8 + 0.2 * np.sin(2.0 * np.pi * 4.0 * t))
    return (np.clip(carrier * rms * np.sqrt(2.0), -32768, 32767)).astype(np.int16).tobytes()


# ---------------------------------------------------------------------------
# Serveur Live avec VAD par énergie (comme Gemini Live)
# ---------------------------------------------------------------------------


class VadLiveServer(FakeLiveServer):
    """Faux serveur Live + détection d'activité vocale sur l'audio reçu.

    Chaque bloc ``send_realtime_input(audio=...)`` est analysé (RMS) :
    une activité au-dessus du seuil ouvre une prise de parole, ``end_blocks``
    blocs sous le seuil la clôturent (fin de parole VAD) — le serveur commet
    alors un tour UTILISATEUR et génère une réponse audio, puis
    ``turn_complete`` : exactement la sémantique de la Live API.

    Le tour est qualifié **fantôme** si l'utilisateur n'a pas parlé pendant
    la fenêtre (l'acoustique le sait) : c'est la métrique centrale du bug.
    """

    def __init__(self, path: AcousticPath, *, threshold_rms: float = 350.0,
                 end_blocks: int = 5, response_seconds: float = 1.2,
                 response_rms: float = 4500.0,
                 turn_complete_delay: float = 0.15, chunks: int = 3):
        super().__init__(semantics="2.x")
        self.path = path
        self.threshold_rms = threshold_rms
        self.end_blocks = end_blocks
        self.response_seconds = response_seconds
        self.response_rms = response_rms
        #: Délai entre le DERNIER chunk audio et turn_complete : la
        #: génération est temps réel côté serveur, elle se termine après la
        #: production du dernier échantillon — la LECTURE locale, elle,
        #: traîne derrière (file profonde, réseau en rafales).
        self.turn_complete_delay = turn_complete_delay
        self.chunks = chunks
        #: Tours committés : [{t, kind: user|ghost, text, peak_rms, duration}]
        self.turns: list[dict] = []
        #: Réponses/transcriptions forcées pour les prochains tours
        #: utilisateur (files consommées une par une par les tests).
        self.next_user_answers: list[str] = []
        self.next_user_transcripts: list[str] = []
        #: Journal de l'audio micro reçu par le serveur : [(t, rms)].
        self.audio_in_log: list[tuple[float, float]] = []

    def _register(self, session) -> None:
        super()._register(session)
        session.send_realtime_input = types.MethodType(
            self._vad_send_realtime_input, session
        )

    # -- VAD ------------------------------------------------------------------

    async def _vad_send_realtime_input(self, session, audio=None, text=None,
                                       media=None, video=None,
                                       audio_stream_end=None,
                                       activity_start=None, activity_end=None):
        if session._closed:
            raise RuntimeError("session fermée")
        if text is not None:
            self.wire.append(WireEvent(session_id=session.session_id,
                                       generation=session.generation,
                                       kind="realtime_text"))
            await session._emit(msg_user_transcript(str(text)))
            await self._commit_turn(session, str(text), peak=0.0, started=time.monotonic())
            return
        data = getattr(audio, "data", audio) or b""
        self.wire.append(WireEvent(session_id=session.session_id,
                                   generation=session.generation,
                                   kind="realtime_audio",
                                   audio_bytes=len(data)))
        session.initial_phase = False

        rms = _pcm_rms(data)
        self.audio_in_log.append((time.monotonic(), rms))
        st = session.__dict__.setdefault(
            "_vad", {"in_speech": False, "below": 0, "started": 0.0, "peak": 0.0}
        )
        if not st["in_speech"]:
            if rms >= self.threshold_rms:
                st["in_speech"] = True
                st["below"] = 0
                st["started"] = time.monotonic()
                st["peak"] = rms
            return
        st["peak"] = max(st["peak"], rms)
        if rms >= self.threshold_rms:
            st["below"] = 0
            return
        st["below"] += 1
        if st["below"] < self.end_blocks:
            return
        # Fin de parole détectée par la VAD serveur : commit du tour.
        st.update(in_speech=False, below=0)
        started = st["started"]
        st["started"] = 0.0
        ghost_text = "je vous écoute"  # écho : le modèle entend sa propre voix
        if self.next_user_transcripts:
            text_commit = self.next_user_transcripts.pop(0)
        else:
            text_commit = ghost_text
        await self._commit_turn(session, text_commit, peak=st["peak"], started=started)

    async def _commit_turn(self, session, text: str, *, peak: float,
                           started: float) -> None:
        now = time.monotonic()
        user_spoke = self.path.user_spoke_between(started - 0.6, now + 0.05)
        kind = "user" if user_spoke else "ghost"
        self.turns.append({
            "t": now,
            "kind": kind,
            "text": text,
            "peak_rms": round(peak, 1),
            "duration": round(now - started, 3) if started else None,
        })
        session.committed.append({"role": "user", "parts": [{"text": text}]})
        await session._emit(msg_user_transcript(text))
        answer = "Je vous écoute."
        if kind == "user" and self.next_user_answers:
            answer = self.next_user_answers.pop(0)
        audio = _tone_pcm(self.response_seconds, self.response_rms, AudioIO.OUTPUT_RATE)
        # Génération « temps réel » : l'audio arrive en rafales de chunks
        # (réseau plus rapide que la lecture), puis le serveur clôt son tour
        # peu après le dernier échantillon PRODUIT — pas après sa lecture.
        piece = len(audio) // max(1, self.chunks)
        for i in range(max(1, self.chunks)):
            chunk = audio[i * piece:(i + 1) * piece] if i < self.chunks - 1 else audio[i * piece:]
            if chunk:
                await session._emit(msg_model_transcript(answer if i == 0 else "", audio=chunk))
        await asyncio.sleep(self.turn_complete_delay)
        await session._emit(msg_turn_complete())
        session.committed.append({"role": "model", "parts": [{"text": answer}]})
        handle = f"{session.session_id}-{len(session.committed)}"
        self.handles[handle] = [dict(c) for c in session.committed]
        session.last_handle = handle
        await session._emit(_Message(session_resumption_update=_ResumptionUpdate(handle, True)))


# ---------------------------------------------------------------------------
# Carte son : threads ADC (80 ms) et DAC (42,67 ms)
# ---------------------------------------------------------------------------


class EchoSoundCard:
    """Deux threads réels qui cadencent la capture et la restitution."""

    def __init__(self, path: AcousticPath):
        self.path = path
        self._lock = threading.Lock()
        self._input_stream = None
        self._output_stream = None        # type: ignore[assignment]
        self._adc_thread: threading.Thread | None = None
        self._dac_thread: threading.Thread | None = None
        self._stop = threading.Event()
        #: Statistiques de restitution (diagnostics).
        self.dac_ticks = 0
        self.adc_ticks = 0

    # -- cycle de vie ---------------------------------------------------------

    def activate(self) -> None:
        global _ACTIVE_CARD
        _ACTIVE_CARD = self

    def deactivate(self) -> None:
        global _ACTIVE_CARD
        _ACTIVE_CARD = None

    def attach_input(self, stream) -> None:
        with self._lock:
            self._input_stream = stream
            stream._card = self
        if self._adc_thread is None or not self._adc_thread.is_alive():
            self._stop.clear()
            self._adc_thread = threading.Thread(
                target=self._adc_loop, name="fake-adc", daemon=True
            )
            self._adc_thread.start()

    def attach_output(self, stream) -> None:
        with self._lock:
            self._output_stream = stream
            stream._card = self
        if self._dac_thread is None or not self._dac_thread.is_alive():
            self._dac_thread = threading.Thread(
                target=self._dac_loop, name="fake-dac", daemon=True
            )
            self._dac_thread.start()

    def detach_input(self, stream) -> None:
        with self._lock:
            if self._input_stream is stream:
                self._input_stream = None

    def detach_output(self, stream) -> None:
        with self._lock:
            if self._output_stream is stream:
                self._output_stream = None

    def stop(self) -> None:
        self._stop.set()
        for thread in (self._adc_thread, self._dac_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
        self._adc_thread = None
        self._dac_thread = None

    # -- boucles temps réel ----------------------------------------------------

    def _adc_loop(self) -> None:
        """Capture : un bloc micro toutes les 80 ms (1280 échantillons 16 kHz)."""
        period = AudioIO.INPUT_BLOCKSIZE / float(AudioIO.INPUT_RATE)
        next_t = time.monotonic() + period
        while not self._stop.is_set():
            with self._lock:
                stream = self._input_stream
            if stream is not None and stream.callback is not None:
                block = self.path.capture_block(AudioIO.INPUT_RATE,
                                                AudioIO.INPUT_BLOCKSIZE)
                try:
                    stream.callback(block, AudioIO.INPUT_BLOCKSIZE, None, None)
                    self.adc_ticks += 1
                except Exception:
                    pass
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            next_t += period
            if next_t < time.monotonic() - 1.0:  # re-synchronisation
                next_t = time.monotonic() + period

    def _dac_loop(self) -> None:
        """Restitution : 1024 trames toutes les 42,67 ms (24 kHz, int16)."""
        frames = 1024
        period = frames / float(AudioIO.OUTPUT_RATE)
        next_t = time.monotonic() + period
        while not self._stop.is_set():
            with self._lock:
                stream = self._output_stream
            if stream is not None and stream.callback is not None:
                outdata = bytearray(frames * AudioIO.SAMPLE_WIDTH)
                try:
                    stream.callback(outdata, frames, None, None)
                    self.dac_ticks += 1
                except Exception:
                    outdata = None
                if outdata is not None:
                    self.path.speaker_played(_pcm_rms(bytes(outdata)))
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            next_t += period
            if next_t < time.monotonic() - 1.0:
                next_t = time.monotonic() + period


# ---------------------------------------------------------------------------
# EchoLab : le vrai pipeline complet, instrumenté et observable
# ---------------------------------------------------------------------------


class EchoLab:
    """Assemble AudioIO + GeminiLive + VAD serveur + carte son factice."""

    def __init__(self, *, echo_gain: float = 0.30, response_seconds: float = 1.2,
                 listen_mode: bool = False, post_response: bool = True):
        self.path = AcousticPath(echo_gain=echo_gain)
        self.server = VadLiveServer(self.path, response_seconds=response_seconds)
        self.card = EchoSoundCard(self.path)
        self.presence_log: list[tuple[float, str]] = []
        self.errors: list[str] = []
        self.connected = threading.Event()
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._voice_task_future = None
        self._mic_sent = 0
        self._t0 = time.monotonic()

        gemini = GeminiLive(
            key="test-key",
            model="gemini-2.5-flash-native-audio-preview-12-2025",
            user="Test",
            on_audio=lambda pcm: None,       # câblé après création de audio
            on_turn_complete=None,
            on_interrupted=None,
            on_speaking=None,
            conversation=ConversationContext(max_turns=20, max_tokens=4096),
        )
        gemini.client = FakeClient(self.server)
        self.gemini = gemini

        def mic(pcm):
            # Pont micro IDENTIQUE à src/ui.py / src/main.py (y compris
            # capture_generation/capture_turn_epoch qui permettent à
            # send_audio() de détecter un bloc devenu périmé entre la
            # capture et l'exécution, v1.7.3).
            if gemini is not None and gemini.can_send():
                self._mic_sent += 1
                capture_generation = gemini.session_generation
                capture_turn_epoch = gemini.turn_epoch
                asyncio.run_coroutine_threadsafe(
                    gemini.send_audio(
                        pcm,
                        capture_generation=capture_generation,
                        capture_turn_epoch=capture_turn_epoch,
                    ),
                    self._loop,
                )

        audio = AudioIO(
            mic,
            presence_hook=self._presence,
            listen_mode_provider=(lambda: listen_mode) if listen_mode else None,
            post_response_provider=(lambda: post_response) if not post_response else None,
        )
        self.audio = audio
        gemini.on_audio = audio.play
        gemini.on_turn_complete = audio.extend_listening
        gemini.on_interrupted = audio.clear_output
        gemini.on_speaking = audio.begin_speaking
        # v1.7.5 ter (bug A, validation réelle) : câblage identique à
        # src/main.py / src/ui.py / tests/real_gemini_harness.py.
        gemini.on_turn_open = audio.note_turn_open
        gemini.on_turn_resolved = audio.note_turn_resolved

    # -- cycle de vie -----------------------------------------------------------

    def _presence(self, state: str) -> None:
        self.presence_log.append((time.monotonic(), str(state)))

    async def _voice_main(self) -> None:
        gemini = self.gemini
        while not self._stop.is_set():
            gemini.reconnect_requested = False
            try:
                await gemini.connect()
                self.connected.set()
                await gemini.receive_loop()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.errors.append(repr(exc))
                await asyncio.sleep(0.05)
            else:
                await asyncio.sleep(0.05)
            finally:
                try:
                    await gemini.close()
                except Exception:
                    pass

    def start(self, timeout: float = 5.0) -> None:
        self.card.activate()
        self._loop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._loop_thread = threading.Thread(target=_run_loop, name="echo-loop",
                                             daemon=True)
        self._loop_thread.start()
        future = asyncio.run_coroutine_threadsafe(self._voice_main(), self._loop)
        self._voice_task_future = future
        if not self.connected.wait(timeout):
            raise TimeoutError("session Gemini non établie")
        self.audio.start()
        # Réveil par le chemin réel (trace + présence + fenêtre de 8 s).
        self.audio._wake(reason="test_wake", src="test")

    async def _drop_current_session(self) -> None:
        """Coupe la session courante DEPUIS la boucle asyncio.

        ``FakeLiveSession.close()`` fait un ``put_nowait`` sur une
        ``asyncio.Queue`` : appelé depuis un autre thread, le réveil du
        consommateur passe par un ``call_soon`` non thread-safe et peut
        être différé indéfiniment (tâche « pending » à jamais). En
        exécutant la coupure sur la boucle, l'arrêt est propre.
        """
        try:
            self.server.drop_connection()
        except Exception:
            pass

    def stop(self) -> None:
        self._stop.set()
        # Fermer la session AVANT d'arrêter la boucle : receive_loop()
        # retourne proprement (fin du flux) au lieu de rester en attente.
        if self._loop is not None and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._drop_current_session(), self._loop
                ).result(timeout=2.0)
            except Exception:
                pass
        try:
            self.audio.stop()
        except Exception:
            pass
        self.card.stop()
        self.card.deactivate()
        future = getattr(self, "_voice_task_future", None)
        if future is not None:
            try:
                future.result(timeout=3.0)
            except Exception:
                pass
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=3.0)
        if self._loop is not None:
            try:
                self._loop.close()
            except Exception:
                pass

    # -- pilotage -----------------------------------------------------------------

    def user_speaks(self, seconds: float, rms: float = 2600.0) -> None:
        self.path.speak(seconds, rms=rms)

    def wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def wait_for_turns(self, count: int, timeout: float) -> list[dict]:
        deadline = time.monotonic() + timeout
        while len(self.server.turns) < count:
            if time.monotonic() > deadline:
                break
            time.sleep(0.02)
        return list(self.server.turns)

    def wait_until(self, predicate, timeout: float, message: str = "condition") -> float:
        """Attend qu'une condition sur le lab devienne vraie (retourne t)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return time.monotonic()
            time.sleep(0.02)
        raise TimeoutError(f"{message} non atteinte en {timeout}s")

    def gate_opened_after(self, since: float) -> bool:
        """La porte micro s'est rouverte après l'instant donné."""
        for event in self.trace():
            if (
                event["kind"] == "mic_gate"
                and event.get("state")
                and event["ts"] >= since
            ):
                return True
        return False

    def last_reset_ts(self) -> float | None:
        resets = [e for e in self.trace() if e["kind"] == "silence_timer RESET"]
        return resets[-1]["ts"] if resets else None

    # -- métriques -----------------------------------------------------------------

    def trace(self) -> list[dict]:
        return self.audio.trace_events()

    def resets(self, since: float | None = None) -> list[dict]:
        out = []
        for event in self.trace():
            if event["kind"] != "silence_timer RESET":
                continue
            if since is not None and event["ts"] < since:
                continue
            out.append(event)
        return out

    def ghost_turns(self, since: float | None = None) -> list[dict]:
        return [
            turn for turn in self.server.turns
            if turn["kind"] == "ghost" and (since is None or turn["t"] >= since)
        ]

    def user_turns(self, since: float | None = None) -> list[dict]:
        return [
            turn for turn in self.server.turns
            if turn["kind"] == "user" and (since is None or turn["t"] >= since)
        ]

    def audio_in(self, since: float | None = None) -> list[tuple[float, float]]:
        """Audio micro reçu par le serveur (t, rms) après un instant donné."""
        return [(t, rms) for t, rms in self.server.audio_in_log
                if since is None or t >= since]

    def presence_states(self, since: float | None = None) -> list[str]:
        return [state for ts, state in self.presence_log
                if since is None or ts >= since]

    def metrics(self) -> dict:
        events = self.trace()
        resets = [e for e in events if e["kind"] == "silence_timer RESET"]
        epochs = {e.get("id") for e in resets}
        return {
            "turns_total": len(self.server.turns),
            "ghost_turns": len(self.ghost_turns()),
            "user_turns": len(self.user_turns()),
            "timer_resets": len(resets),
            "timer_epochs_created": len(epochs),
            "timer_epochs": sorted(epochs),
            "resets_by_reason": {
                reason: sum(1 for e in resets if e["reason"] == reason)
                for reason in {e["reason"] for e in resets}
            },
            "wake_events": sum(1 for e in events if e["kind"] == "wake"),
            "sleep_events": sum(1 for e in events if e["kind"] == "sleep"),
            "listening_transitions": sum(
                1 for s in self.presence_states() if s == "listening"
            ),
            "sessions": self.server.connect_count,
            "mic_blocks_sent": self._mic_sent,
            "dac_ticks": self.card.dac_ticks,
            "adc_ticks": self.card.adc_ticks,
            "errors": list(self.errors),
        }
