"""Harness d'intégration RÉELLE : le vrai pipeline Jarvis contre l'API Gemini
Live réelle de Google (v1.7.4 — validation du correctif reconnexion-par-tour).

Différence avec ``tests/echo_loop_harness.py``
------------------------------------------------
``EchoLab`` (echo_loop_harness.py) utilise un FAUX serveur Live (VAD par
énergie, réponses synthétiques) : rapide, déterministe, sans réseau ni clé —
mais il ne prouve rien sur le comportement RÉEL du serveur Gemini (rythme des
``turn_complete``, contenu réel des ``GoAway``, latence réseau réelle, etc.).

Ce module réutilise EXACTEMENT la même infrastructure de bas niveau
(fausse carte son avec threads ADC/DAC temps réel, ``AudioIO`` réelle, pont
micro réel identique à ``src/main.py``/``src/ui.py``, ``GeminiLive`` réelle
avec sa boucle ``connect()``/``receive_loop()``) mais :

* remplace le faux serveur par un vrai ``google.genai.Client`` (clé API
  réelle, jamais journalisée) ;
* remplace la tonalité synthétique de parole par de l'AUDIO RÉEL (parole
  humaine synthétisée par le TTS Gemini, rejouée comme entrée micro) — sans
  quoi le VAD serveur réel et la transcription n'auraient rien à reconnaître.

Aucune clé API n'est stockée ni journalisée par ce module : il reçoit la
clé en paramètre (jamais lue depuis un fichier versionné) et ne l'imprime
jamais, y compris dans les traces de diagnostic.
"""

from __future__ import annotations

import asyncio
import collections
import re
import threading
import time

import numpy as np

from tests.echo_loop_harness import (  # noqa: F401  (réexport volontaire)
    AudioIO,
    ConversationContext,
    EchoSoundCard,
    GeminiLive,
)

_KEY_RE = re.compile(r"AIza[A-Za-z0-9_\-]{10,}|AQ\.[A-Za-z0-9_\-]+")


def redact(text: str) -> str:
    """Retire toute sous-chaîne ressemblant à une clé API d'un message."""
    return _KEY_RE.sub("<clé masquée>", str(text))


class RealAcousticPath:
    """Ce que le microphone simulé "entend" : bruit de fond + parole réelle.

    Contrairement à ``AcousticPath`` (echo_loop_harness.py, tonalité
    synthétique pilotée par une enveloppe RMS), cette classe rejoue de VRAIS
    échantillons PCM 16 kHz (parole humaine synthétisée par TTS) placés dans
    une file d'attente par le test — consommés par la fausse carte son au
    même rythme qu'un vrai micro (1280 échantillons / 80 ms).

    Volontairement SANS modèle d'écho haut-parleurs : la validation v1.7.4
    porte sur le cycle de vie des sessions, pas sur l'anti-larsen (déjà
    validé séparément en v1.7.2/v1.7.3 avec le faux serveur).
    """

    def __init__(self, *, noise_rms: float = 12.0):
        self.noise_rms = noise_rms
        self._lock = threading.Lock()
        self._queue: collections.deque[int] = collections.deque()
        self._rng = np.random.default_rng(20260930)

    def queue_pcm(self, pcm: bytes) -> None:
        """Ajoute de l'audio réel (16 kHz mono s16le) à jouer dans le micro."""
        samples = np.frombuffer(pcm, dtype=np.int16)
        with self._lock:
            self._queue.extend(samples.tolist())

    def pending_samples(self) -> int:
        with self._lock:
            return len(self._queue)

    def capture_block(self, rate: int, samples: int) -> bytes:
        out = self._rng.normal(0.0, self.noise_rms, samples)
        with self._lock:
            n = min(samples, len(self._queue))
            for i in range(n):
                out[i] += self._queue.popleft()
        return np.clip(out, -32768, 32767).astype(np.int16).tobytes()

    def speaker_played(self, rms: float) -> None:
        # Pas de modèle d'écho dans ce harness (voir docstring de la classe).
        pass


class RealVoiceLab:
    """Assemble AudioIO + GeminiLive RÉELLE + fausse carte son.

    Boucle de connexion IDENTIQUE à ``src/main.py`` (même code dupliqué à
    l'identique dans ``tests/echo_loop_harness.py::EchoLab`` pour les tests
    au faux serveur) : c'est cette boucle, pas le harness, qui contenait le
    bug v1.7.4 (reconnexion après chaque tour normal).
    """

    def __init__(self, api_key: str, model: str, *, user: str = "Validation",
                 conversation: ConversationContext | None = None,
                 noise_rms: float = 12.0):
        self.path = RealAcousticPath(noise_rms=noise_rms)
        self.card = EchoSoundCard(self.path)
        self.presence_log: list[tuple[float, str]] = []
        self.errors: list[str] = []
        self.connected = threading.Event()
        self._stop = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._voice_task_future = None
        self._mic_sent = 0
        self.conversation = conversation or ConversationContext(max_turns=20, max_tokens=4096)

        # Bilan par tour (rempli via les callbacks de transcription — la
        # MÊME matière première que l'UI, pas un système séparé).
        self.turns: list[dict] = []
        self._current: dict = {"user": "", "assistant": "", "t0": None}

        gemini = GeminiLive(
            api_key,
            model,
            user,
            on_audio=lambda pcm: None,  # câblé plus bas -> audio.play
            on_turn_complete=None,
            on_interrupted=None,
            on_speaking=None,
            on_user_transcript=self._on_user_transcript,
            on_assistant_transcript=self._on_assistant_transcript,
            conversation=self.conversation,
        )
        self.gemini = gemini

        def mic(pcm):
            # Pont micro IDENTIQUE à src/main.py / src/ui.py (v1.7.3 :
            # capture_generation/capture_turn_epoch revalidés dans
            # send_audio() au moment de l'exécution réelle sur la boucle
            # asyncio, pas seulement à la capture sur ce thread).
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

        audio = AudioIO(mic, presence_hook=self._presence)
        self.audio = audio
        gemini.on_audio = audio.play
        gemini.on_turn_complete = self._on_turn_complete
        gemini.on_interrupted = audio.clear_output
        gemini.on_speaking = audio.begin_speaking

    # -- callbacks transcription / tour --------------------------------------

    def _on_user_transcript(self, text: str, final: bool = False) -> None:
        if self._current["t0"] is None:
            self._current["t0"] = time.monotonic()
        self._current["user"] = text

    def _on_assistant_transcript(self, text: str) -> None:
        self._current["assistant"] = text

    def _on_turn_complete(self) -> None:
        self.turns.append(
            {
                "user": self._current["user"],
                "assistant": self._current["assistant"],
                "t": time.monotonic(),
                "session_generation": self.gemini.session_generation,
            }
        )
        self._current = {"user": "", "assistant": "", "t0": None}
        # Chemin réel : c'est CE callback qui réarme la fenêtre de 8 s dans
        # src/main.py / src/ui.py.
        self.audio.extend_listening()

    def _presence(self, state: str) -> None:
        self.presence_log.append((time.monotonic(), str(state)))

    # -- cycle de vie ---------------------------------------------------------

    async def _voice_main(self) -> None:
        """Boucle IDENTIQUE à ``src/main.py`` (le composant testé)."""
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
                self.errors.append(redact(repr(exc)))
                if not gemini.reconnect_requested:
                    await asyncio.sleep(1.0)
            else:
                await asyncio.sleep(0.1)
            finally:
                try:
                    await gemini.close()
                except Exception:
                    pass

    def start(self, timeout: float = 30.0) -> None:
        self.card.activate()
        self._loop = asyncio.new_event_loop()

        def _run_loop():
            asyncio.set_event_loop(self._loop)
            self._loop.run_forever()

        self._loop_thread = threading.Thread(target=_run_loop, name="real-voice-loop", daemon=True)
        self._loop_thread.start()
        future = asyncio.run_coroutine_threadsafe(self._voice_main(), self._loop)
        self._voice_task_future = future
        if not self.connected.wait(timeout):
            raise TimeoutError("session Gemini réelle non établie (clé/réseau/quota ?)")
        self.audio.start()

    def wake(self) -> None:
        self.audio._wake(reason="test_wake", src="test")

    async def _force_disconnect(self) -> None:
        """Coupure réseau RÉELLE (pas un GoAway serveur) : ferme le vrai
        WebSocket depuis le client, comme une perte de connexion réelle.
        """
        try:
            await self.gemini._shutdown_session()
        except Exception:
            pass

    def force_disconnect(self, *, drop_handle: bool) -> None:
        if drop_handle:
            self.gemini.resumption_handle = None
        fut = asyncio.run_coroutine_threadsafe(self._force_disconnect(), self._loop)
        fut.result(timeout=5.0)

    def stop(self) -> None:
        self._stop.set()
        if self._loop is not None and self._loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self._force_disconnect(), self._loop
                ).result(timeout=5.0)
            except Exception:
                pass
        try:
            self.audio.stop()
        except Exception:
            pass
        self.card.stop()
        self.card.deactivate()
        future = self._voice_task_future
        if future is not None:
            try:
                future.result(timeout=5.0)
            except Exception:
                pass
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=5.0)
        if self._loop is not None:
            try:
                self._loop.close()
            except Exception:
                pass

    # -- pilotage ---------------------------------------------------------------

    def speak_pcm(self, pcm: bytes) -> None:
        self.path.queue_pcm(pcm)

    def wait(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def wait_for_new_turn(self, since_count: int, timeout: float) -> dict | None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if len(self.turns) > since_count:
                return self.turns[-1]
            time.sleep(0.05)
        return None

    def wait_until(self, predicate, timeout: float, message: str = "condition") -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise TimeoutError(f"{message} non atteinte en {timeout:.1f}s")

    # -- observation --------------------------------------------------------------

    def trace(self) -> list[dict]:
        return self.audio.trace_events()

    def gemini_trace(self) -> list[dict]:
        return self.gemini.trace_events()

    def session_connect_events(self, since: float | None = None) -> list[dict]:
        return [
            e for e in self.gemini_trace()
            if e["kind"] in ("SESSION_CONNECT", "SESSION_RESUME")
            and (since is None or e["ts"] >= since)
        ]

    def resets(self, since: float | None = None) -> list[dict]:
        return [
            e for e in self.trace()
            if e["kind"] == "silence_timer RESET" and (since is None or e["ts"] >= since)
        ]
