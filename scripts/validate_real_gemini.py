"""Validation FINALE sur l'API Gemini Live RÉELLE (harness Windows, clé requise).

Ce script est le complément explicite des tests du faux serveur
(``tests/test_live_context_harness.py``) : il exécute le VRAI pipeline vocal de
Jarvis (``src/gemini_live.py``, ses reconnexions, son rejeu de contexte, ses
outils) contre l'API Gemini Live réelle, avec de l'AUDIO RÉEL en entrée.

Les tests du faux serveur ne sont PAS équivalents à cette validation : ils
prouvent le protocole côté client, celui-ci prouve (ou non) que le serveur
réel se comporte comme prévu.

Scénarios et verdicts produits (format imposé) ::

    GEMINI LIVE RÉEL : PASS / FAIL / NON TESTABLE
    AUDIO RÉEL : PASS / FAIL / NON TESTABLE
    RECONNEXION RÉELLE : PASS / FAIL / NON TESTABLE
    SESSION RESUMPTION RÉELLE : PASS / FAIL / NON TESTABLE
    OUTILS RÉELS : PASS / FAIL / NON TESTABLE

NON TESTABLE = prérequis absent (clé refusée, quota épuisé, réseau
injoignable), avec sa raison. Un délai dépassé en attendant la réponse, une
erreur de session ou un contenu incorrect est un FAIL, diagnostic du moteur
joint (session, tour, porte micro, interruptions classées, fin de trace).
L'entrée audio passe par un micro simulé CONTINU, filtré par ``can_send()``
comme le pont micro de production (correctif PR #50).

Modes d'entrée (le premier est le chemin audio complet, sans micro) :

* ``--tts`` (défaut) : chaque question est SYNTHÉTISÉE par Gemini TTS puis
  envoyée comme audio temps réel (``send_realtime_input(audio=...)``) — VAD
  serveur, transcription d'entrée, tout le chemin réel sauf le micro.
* ``--mic`` : le micro réel (sounddevice, 16 kHz mono) — l'opérateur parle.
* ``--text`` : ``send_realtime_input(text=...)`` — DIAGNOSTIC SEULEMENT :
  le rappel d'historique fonctionne en texte même sans le correctif (thread
  Google AI 111617) ; un PASS ici ne valide PAS le correctif audio.

Clé API (jamais affichée, jamais journalisée) :
  1. variable d'environnement ``GEMINI_API_KEY`` (ou ``GOOGLE_API_KEY``) ;
  2. à défaut, le ``config.json`` de l'application (cf. ``src/settings.py``).

Utilisation (Windows, depuis la racine du dépôt) ::
    py -3 scripts\\validate_real_gemini.py
    py -3 scripts\\validate_real_gemini.py --mic
    py -3 scripts\\validate_real_gemini.py --model gemini-2.5-flash-native-audio-preview-12-2025
    py -3 scripts\\validate_real_gemini.py --record reponses   # écoute les réponses

Aucune clé ne doit jamais être passée en argument : le CLI ne l'accepte pas.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import time
import wave
from pathlib import Path

# Windows console cp1252 safety: prevent UnicodeEncodeError on unicode characters
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
if hasattr(sys.stderr, "reconfigure"):
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import gemini_live as gl  # noqa: E402
from src.settings import DEFAULT_MODEL  # noqa: E402
from src.conversation import ConversationContext  # noqa: E402

TURN_TIMEOUT = 60.0  # le natif audio peut être lent au premier tour
TTS_TIMEOUT = 45.0
CHUNK_MS = 100
SAMPLE_RATE = 16000

_KEY_RE = re.compile(r"AIza[A-Za-z0-9_\-]{10,}|AQ\.[A-Za-z0-9_\-]+")
VALIDATION_CODE = "KILO-7"

#: Tous les énoncés joués pendant la validation (pré-synthétisés en mode
#: tts/live-tts pour résister à l'expiration d'un jeton éphémère en cours de
#: run : la partie « test » n'a plus besoin de faire appel au modèle TTS).
ALL_PROMPTS = [
    "Bonjour Jarvis, tu m'entends bien ?",
    "Mon prénom est Simon.",
    "Quel est mon prénom ?",
    "J'habite à Tours.",
    "Où est-ce que j'habite déjà ?",
    "Utilise l'outil validation_ping et répète-moi exactement le code qu'il retourne.",
    "Quel était le code retourné par l'outil, déjà ?",
]

WIRE: list[dict] = []  # traçage des envois (patch du SDK, voir _install_wire_tap)


def safe_print(*args, **kwargs) -> None:
    """Print qui ne peut JAMAIS laisser fuiter une clé API."""
    text = " ".join(str(a) for a in args)
    masked = _KEY_RE.sub("<clé masquée>", text)
    try:
        print(masked, **kwargs, flush=True)
    except UnicodeEncodeError:
        print(masked.encode("ascii", errors="replace").decode("ascii"), **kwargs, flush=True)


# ---------------------------------------------------------------------------
# Traçage du câble : patch minimal des méthodes d'envoi du SDK (observation
# seule — aucun comportement modifié). C'est ce qui permet d'affirmer « le
# seed est bien parti » / « aucune re-émission de clientContent ».
# ---------------------------------------------------------------------------


def _install_wire_tap() -> None:
    from google.genai import live

    orig_cc = live.AsyncSession.send_client_content
    orig_rt = live.AsyncSession.send_realtime_input
    orig_tr = live.AsyncSession.send_tool_response

    async def cc(self, *, turns=None, turn_complete=True):
        WIRE.append(
            {
                "ts": time.monotonic(),
                "kind": "client_content",
                "turn_complete": turn_complete,
                "n_turns": len(turns) if turns else 0,
            }
        )
        return await orig_cc(self, turns=turns, turn_complete=turn_complete)

    async def rt(self, **kwargs):
        if kwargs.get("text") is not None:
            kind = "realtime_text"
        elif kwargs.get("audio_stream_end"):
            kind = "audio_stream_end"
        else:
            kind = "realtime_audio"
        WIRE.append({"ts": time.monotonic(), "kind": kind})
        return await orig_rt(self, **kwargs)

    async def tr(self, *, function_responses):
        WIRE.append(
            {
                "ts": time.monotonic(),
                "kind": "tool_response",
                "tools": [getattr(r, "name", "?") for r in function_responses],
            }
        )
        return await orig_tr(self, function_responses=function_responses)

    live.AsyncSession.send_client_content = cc
    live.AsyncSession.send_realtime_input = rt
    live.AsyncSession.send_tool_response = tr


def wire_since(mark: int) -> list[dict]:
    return WIRE[mark:]


# ---------------------------------------------------------------------------
# Clé API : env, puis config.json de l'application. Jamais affichée.
# ---------------------------------------------------------------------------


def load_api_key() -> str:
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            safe_print(f"[clé] lue depuis la variable {name} (longueur {len(value)}).")
            return value
    try:
        from src import settings

        cfg = settings.load_file_config()
        value = str(cfg.get("api_key") or "").strip()
        if value:
            safe_print(f"[clé] lue depuis {settings.config_path()} (longueur {len(value)}).")
            return value
    except Exception as exc:  # pragma: no cover - environnement variable
        safe_print(f"[clé] config.json illisible ({exc}).")
    return ""


# ---------------------------------------------------------------------------
# Synthèse des questions par Gemini TTS (chemin audio sans micro).
# ---------------------------------------------------------------------------


async def synthesize_prompt(client, text: str, model: str) -> bytes:
    """Texte -> PCM 16 kHz mono s16le, via le modèle TTS de Gemini."""
    import numpy as np
    from google.genai import types

    config = types.GenerateContentConfig(
        response_modalities=["AUDIO"],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name="Kore")
            )
        ),
    )
    response = await asyncio.wait_for(
        client.aio.models.generate_content(model=model, contents=text, config=config),
        timeout=TTS_TIMEOUT,
    )
    part = response.candidates[0].content.parts[0]
    data = getattr(getattr(part, "inline_data", None), "data", None)
    if not data:
        raise RuntimeError("le modèle TTS n'a pas renvoyé d'audio")
    mime = getattr(getattr(part, "inline_data", None), "mime_type", "") or "audio/L16;codec=pcm;rate=24000"
    rate = 24000
    match = re.search(r"rate=(\d+)", mime)
    if match:
        rate = int(match.group(1))
    samples = np.frombuffer(data, dtype=np.int16)
    if rate != SAMPLE_RATE:
        count = int(len(samples) * SAMPLE_RATE / rate)
        samples = np.interp(
            np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples
        ).astype(np.int16)
    # Un soupçon de silence final : garantit que la VAD serveur clôt le tour.
    tail = np.zeros(int(SAMPLE_RATE * 0.4), dtype=np.int16)
    return (np.concatenate([samples, tail])).tobytes()


def _resample_to_16k(data: bytes, rate: int) -> bytes:
    """PCM s16le mono -> 16 kHz (+ silence final pour la VAD serveur)."""
    import numpy as np

    samples = np.frombuffer(data, dtype=np.int16)
    if rate != SAMPLE_RATE:
        count = int(len(samples) * SAMPLE_RATE / rate)
        samples = np.interp(
            np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples
        ).astype(np.int16)
    tail = np.zeros(int(SAMPLE_RATE * 0.4), dtype=np.int16)
    return np.concatenate([samples, tail]).tobytes()


def _pcm_with_tail(pcm: bytes, tail_seconds: float = 0.4) -> bytes:
    """Ajoute le silence final attendu par la VAD serveur (comme les TTS)."""
    import numpy as np

    tail = np.zeros(int(SAMPLE_RATE * tail_seconds), dtype=np.int16)
    return np.concatenate([np.frombuffer(pcm, dtype=np.int16), tail]).tobytes()


def synthesize_from_fixtures(prompts: list[str]) -> tuple[dict[str, bytes], list[str]]:
    """Charge les fixtures audio versionnées des prompts demandés.

    Source PRIMAIRE des validations : zéro requête TTS, zéro quota Gemini,
    entrées identiques à chaque exécution. Chaque fixture est validée
    (sha256 manifeste, format 16 kHz mono s16le, durée plausible) AVANT
    l'ouverture de toute session. Retourne ``(cache, problèmes)`` — les
    problèmes sont affichés par l'appelant, jamais avalés.
    """
    from scripts.validation import tts_fixtures as fx

    try:
        cache, problems = fx.load_fixtures(prompts)
    except fx.FixtureError as exc:
        return {}, [str(exc)]
    return {prompt: _pcm_with_tail(pcm) for prompt, pcm in cache.items()}, problems


async def synthesize_all_via_live(client, model: str, prompts: list[str]) -> dict[str, bytes]:
    """Pré-synthétise les énoncés par une session Live (audio de sortie).

    Nécessaire pour les jetons éphémères, qui n'ont souvent accès QU'au Live
    API (pas à generateContent/TTS). La session de synthèse est distincte des
    sessions de test ; son audio (24 kHz) est resamplé en 16 kHz puis rejoué
    comme ENTRÉE UTILISATEUR dans les sessions de test — le chemin audio réel
    (send_realtime_input, VAD serveur, transcription) reste celui de Jarvis.
    """
    from google.genai import types

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=(
            "Tu es un synthétiseur vocal. Quand on t'envoie du texte, tu le "
            "répètes EXACTEMENT mot pour mot en français, d'une voix naturelle, "
            "sans rien ajouter, sans commenter, sans aucune remarque."
        ),
    )
    cache: dict[str, bytes] = {}
    async with client.aio.live.connect(model=model, config=config) as session:
        for prompt in prompts:
            await session.send_realtime_input(text=prompt)
            chunks = bytearray()
            async for message in session.receive():
                content = message.server_content
                if content and content.model_turn:
                    for part in content.model_turn.parts:
                        if part.inline_data and part.inline_data.data:
                            chunks.extend(part.inline_data.data)
                if content and content.turn_complete:
                    break
            if not chunks:
                raise RuntimeError(f"la session Live n'a pas synthétisé : {prompt!r}")
            cache[prompt] = _resample_to_16k(bytes(chunks), 24000)
            safe_print(f"   [synthèse] {prompt[:60]}… ({len(cache[prompt])} octets)")
    return cache


async def record_microphone(seconds: float) -> bytes:
    """Enregistre le micro réel (16 kHz mono s16le). Mode --mic uniquement."""
    try:
        import sounddevice as sd
    except Exception as exc:
        raise RuntimeError(
            "sounddevice est requis pour --mic (pip install sounddevice)"
        ) from exc
    safe_print(f"   [micro] parle maintenant ({seconds:.0f} s)…")
    recording = sd.rec(
        int(seconds * SAMPLE_RATE), samplerate=SAMPLE_RATE, channels=1, dtype="int16"
    )
    sd.wait()
    return recording.tobytes()


# ---------------------------------------------------------------------------
# Runner réel : le VRAI GeminiLive + sa boucle de reconnexion (calquée sur
# src/main.py), observé par les callbacks.
# ---------------------------------------------------------------------------


class RealRunner:
    """Le VRAI ``GeminiLive`` + sa boucle de reconnexion, alimenté comme en production.

    Correctif PR #50 (E11 : AUDIO / RECONNEXION / REPRISE « NON TESTABLE » sur le
    PC de validation alors que S1 et les outils passaient) :

    * micro CONTINU : un bloc de 100 ms toutes les 100 ms, filtré par
      ``can_send()`` comme le pont micro de ``src/ui.py`` (bruit de fond faible,
      énoncé injecté dans le flux). L'ancienne version envoyait l'énoncé puis
      COUPAIT le flux (0,4 s de silence, aucun ``audio_stream_end``) : la VAD
      du serveur pouvait ne jamais clore la parole ;
    * la reconnexion est réellement attendue (nouvelle génération de session
      ET session prête) — l'ancien code attendait un évènement resté levé
      depuis la PREMIÈRE connexion, et comptait les rejeux trop tôt ;
    * la réponse à une question est le tour qui CONTIENT une transcription
      utilisateur (jamais la réponse du modèle au rejeu de contexte).
    """

    #: Bruit de fond du micro simulé (RMS int16), comme ``RealAcousticPath``.
    NOISE_RMS = 12.0

    def __init__(self, api_key: str, model: str, record_dir: Path | None):
        self.conversation = ConversationContext(max_turns=20, max_tokens=4096)
        self.turn_log: list[dict] = []
        self._current: dict = {"user": "", "assistant": "", "tools": [], "interrupted": False}
        self._turn_done = asyncio.Event()
        self._connected = asyncio.Event()
        self.connected = self._connected  # API publique pour les scénarios
        self._stop = asyncio.Event()
        # v1.9.x : positionné si le keeper s'arrête AVANT toute session
        # utilisable (budget de récupération épuisé) — start() échoue alors
        # immédiatement avec un diagnostic, sans attendre TURN_TIMEOUT.
        self._boot_failed = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._mic_task: asyncio.Task | None = None
        self._mic_queue = bytearray()
        self._audio_chunks: list[bytes] = []
        self._record_dir = record_dir
        self.errors: list[str] = []
        self.mic_blocks_sent = 0
        #: Injection d'entrée alternative (selftest) : prompt -> PCM. En mode
        #: réel ce champ reste None et le mode CLI (tts/mic/text) s'applique.
        self.input_policy = None
        self.gemini = gl.GeminiLive(
            api_key,
            model,
            "Validation",
            on_audio=self._on_audio,
            on_turn_complete=self._on_turn_complete,
            on_interrupted=self._on_interrupted,
            on_tool_start=lambda name: self._current["tools"].append(name),
            on_user_transcript=lambda text, final=False: self._current.__setitem__(
                "user", text
            ),
            on_assistant_transcript=lambda text: self._current.__setitem__(
                "assistant", text
            ),
            conversation=self.conversation,
        )

    # -- callbacks -----------------------------------------------------------
    def _on_audio(self, pcm: bytes) -> None:
        self._audio_chunks.append(pcm)

    def _on_interrupted(self) -> None:
        self._current["interrupted"] = True

    def _on_turn_complete(self) -> None:
        self._current["turn_id"] = self.gemini.turn_id
        self.turn_log.append(self._current)
        self._current = {
            "user": "",
            "assistant": "",
            "tools": [],
            "interrupted": False,
        }
        self._turn_done.set()

    # -- cycle de vie ----------------------------------------------------------
    async def start(self) -> None:
        self._task = asyncio.create_task(self._run())
        # Session utilisable OU échec de démarrage explicite (budget de
        # récupération épuisé) — jamais TURN_TIMEOUT dans le vide.
        deadline = time.monotonic() + TURN_TIMEOUT
        while not (self._connected.is_set() or self._boot_failed.is_set()):
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"aucune session utilisable après {TURN_TIMEOUT:.0f} s "
                    f"(erreurs : {self.errors[-1] if self.errors else 'aucune détaillée'})"
                )
            await asyncio.sleep(0.05)
        if self._boot_failed.is_set() and not self._connected.is_set():
            raise RuntimeError(self.errors[-1] if self.errors else
                               "démarrage impossible (cf. erreurs de session)")
        self._mic_task = asyncio.create_task(self._mic_loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._mic_task is not None:
            self._mic_task.cancel()
            try:
                await self._mic_task
            except BaseException:
                pass
        try:
            await self.gemini._shutdown_session()
        except Exception:
            pass
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        try:
            await self.gemini.close()
        except Exception:
            pass

    async def _run(self) -> None:
        gemini = self.gemini
        while not self._stop.is_set():
            gemini.reconnect_requested = False
            try:
                await gemini.connect()
                self._connected.set()
                await gemini.receive_loop()
            except asyncio.CancelledError:
                break
            except gl.SessionRecoveryExhausted as exc:
                # v1.9.x (run 40370419959 : 45 sessions en rafale) : le budget
                # du moteur est épuisé — on ARRÊTE le keeper. Les asks en vol
                # échoueront en TurnTimeout dont le diagnostic porte la trace
                # SESSION_RECOVERY_EXHAUSTED : échec explicite, jamais une
                # boucle de reconnexions sans fin.
                description = describe_exception(exc)
                self.errors.append(f"récupération épuisée : {description}")
                safe_print(f"   [session] ARRÊT : {description}")
                self._connected.clear()
                self._boot_failed.set()
                break
            except Exception as exc:
                description = describe_exception(exc)
                self.errors.append(description)
                safe_print(f"   [session] erreur : {description}")
                if not gemini.reconnect_requested:
                    # Backoff croissant tant que les sessions meurent sans
                    # rien servir (le moteur retombe à ~0 après une session
                    # saine) — plus aucune rafale 1-connexion/s.
                    await asyncio.sleep(
                        max(1.0, gemini.session_recovery_backoff_seconds())
                    )
            finally:
                try:
                    await gemini.close()
                except Exception:
                    pass

    async def _mic_loop(self) -> None:
        """Micro simulé CONTINU : chemin du pont micro de production."""
        import numpy as np

        rng = np.random.default_rng(20261016)
        step = int(SAMPLE_RATE * CHUNK_MS / 1000)  # échantillons par bloc
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        while not self._stop.is_set():
            block = rng.normal(0.0, self.NOISE_RMS, step)
            if self._mic_queue:
                take = bytes(self._mic_queue[: step * 2])
                del self._mic_queue[: step * 2]
                speech = np.frombuffer(take, dtype=np.int16).astype(np.float64)
                block[: speech.size] += speech
            pcm = np.clip(block, -32768, 32767).astype(np.int16).tobytes()
            gemini = self.gemini
            if gemini.can_send():
                self.mic_blocks_sent += 1
                try:
                    await gemini.send_audio(
                        pcm,
                        capture_generation=gemini.session_generation,
                        capture_turn_epoch=gemini.turn_epoch,
                    )
                except Exception:
                    pass  # session en cours de fermeture : la boucle reconnecte
            deadline += CHUNK_MS / 1000
            await asyncio.sleep(max(0.0, deadline - loop.time()))

    # -- interactions ------------------------------------------------------------
    async def _wait_sendable(self, timeout: float = TURN_TIMEOUT) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # v1.9.x : le keeper s'est arrêté (budget épuisé) — échec
            # immédiat avec cause précise, pas 60 s d'attente inutile.
            if self.gemini.session_recovery_exhausted:
                raise TurnTimeout(
                    "récupération de session épuisée : plus aucune session "
                    "n'est ouverte (cf. SESSION_RECOVERY_EXHAUSTED)",
                    self.diagnostic(),
                )
            if self.gemini.can_send() and not self.gemini.speaking and not self._mic_queue:
                return
            await asyncio.sleep(0.05)
        raise TurnTimeout(f"Jarvis indisponible {timeout:.0f} s avant la question", self.diagnostic())

    async def _wait_answer(self, before: int, predicate, what: str) -> dict:
        deadline = time.monotonic() + TURN_TIMEOUT
        while time.monotonic() < deadline:
            for turn in self.turn_log[before:]:
                if predicate(turn):
                    return turn
            await asyncio.sleep(0.05)
        raise TurnTimeout(f"aucune réponse {TURN_TIMEOUT:.0f} s après {what}", self.diagnostic())

    async def speak_pcm(self, pcm) -> dict:
        """Injecte l'énoncé dans le flux micro continu, puis attend SA réponse.

        ``pcm`` : octets, ou fonction qui les produit AU MOMENT de l'injection
        (l'auto-test y programme l'énoncé du faux serveur).
        """
        await self._wait_sendable()
        self._turn_done.clear()
        self._audio_chunks.clear()
        before = len(self.turn_log)
        self._mic_queue.extend(pcm() if callable(pcm) else pcm)
        return await self._wait_answer(before, lambda turn: bool(turn.get("user")), "l'énoncé")

    async def speak_text(self, text: str) -> dict:
        await self._wait_sendable()
        self._turn_done.clear()
        before = len(self.turn_log)
        await self.gemini.submit_text(text)
        return await self._wait_answer(before, lambda turn: bool(turn.get("user")), "le texte")

    async def ask(self, prompt: str, mode: str, client=None, tts_model: str = "") -> dict:
        safe_print(f"   [question] {prompt}")
        if self.input_policy is not None:
            turn = await self.speak_pcm(lambda: self.input_policy(prompt))
        elif mode == "text":
            turn = await self.speak_text(prompt)
        else:
            if mode == "mic":
                pcm = await record_microphone(6.0)
            else:
                pcm = await synthesize_prompt(client, prompt, tts_model)
            turn = await self.speak_pcm(pcm)
        answer = turn.get("assistant", "")
        safe_print(f"   [réponse]  {answer[:160] or '(sans transcription)'}")
        if self._record_dir is not None and self._audio_chunks:
            self._dump_audio(len(self.turn_log))
        return turn

    def _dump_audio(self, index: int) -> None:
        self._record_dir.mkdir(parents=True, exist_ok=True)
        path = self._record_dir / f"reponse_{index:02d}.wav"
        with wave.open(str(path), "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(24000)  # sortie Live native audio
            handle.writeframes(b"".join(self._audio_chunks))
        safe_print(f"   [audio]    réponse écrite : {path}")

    async def drop_connection(self, keep_handle: bool) -> int:
        """Coupure brutale (équivalent perte réseau), avec ou sans le handle.

        Retourne la génération de session AVANT la coupure (cf. ``wait_reconnected``).
        """
        generation = self.gemini.session_generation
        self._connected.clear()
        if not keep_handle:
            self.gemini.resumption_handle = None
        await self.gemini._shutdown_session()  # le runner reconnecte aussitôt
        return generation

    async def wait_reconnected(self, generation_before: int, timeout: float = TURN_TIMEOUT) -> None:
        """Attend une NOUVELLE session, prête à recevoir l'audio."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if (
                self._connected.is_set()
                and self.gemini.session_generation > generation_before
                and self.gemini.can_send()
            ):
                return
            await asyncio.sleep(0.05)
        raise TurnTimeout(f"reconnexion non aboutie en {timeout:.0f} s", self.diagnostic())

    def diagnostic(self) -> str:
        """État du moteur et fin de trace, sans contenu échangé (masqué à l'affichage)."""
        import json

        gemini = self.gemini
        keep = {"INTERRUPTION", "TURN_OPEN", "TURN_CLOSE", "TURN_COMPLETE", "TURN_KEPT_OPEN",
                "EMPTY_GENERATION_RETRY", "EMPTY_TURN_COMPLETE_IN_PROGRESS", "RESPONSE_STALLED",
                "SESSION_CONNECT", "SESSION_RESUME", "SESSION_READY", "SEED_COMMIT_TIMEOUT",
                "SEED_COMMIT_CONFIRMED", "SESSION_RESUME_REJECTED", "AUDIO_STREAM_END",
                "SERVER_VAD", "TEXT_TURN_TIMEOUT",
                "TOOL_CALL_EXECUTED", "TOOL_RESPONSE_SENT", "TOOL_CALL_FALLBACK_DASHBOARD",
                "TOOL_CALL_RECEIVED", "TOOL_CALL_VALIDATED", "TOOL_CALL_UNKNOWN_TOOL",
                "TOOL_RESULT_TURN_COMPLETED", "FINAL_TEXT_RECEIVED",
                "SESSION_RECOVERY_FAILURE", "SESSION_RECOVERY_EXHAUSTED"}
        fields = ("origin", "classification", "reason", "interrupted_generation", "interaction_status",
                  "waiting_for_input", "turn_complete_reason", "has_model_content")
        traces = [
            {"kind": e["kind"], "turn": e.get("turn_id"), **{k: e[k] for k in fields if k in e}}
            for e in gemini.trace_events() if e.get("kind") in keep
        ][-14:]
        try:
            allowed, reason = gemini._can_send_now()
        except Exception:  # pragma: no cover - diagnostic seulement
            allowed, reason = None, "?"
        state = {
            "moteur": gemini.engine_snapshot(),
            "porte_micro": {"ouverte": allowed, "raison": reason},
            "blocs_micro_envoyes": self.mic_blocks_sent,
            "tours_observes": len(self.turn_log),
            "recuperation_sessions": {
                "echecs_consecutifs": self.gemini._recovery_failures,
                "epuise": self.gemini.session_recovery_exhausted,
                "backoff_s": self.gemini.session_recovery_backoff_seconds(),
            },
            "erreurs_session": self.errors[-3:],
            "traces": traces,
        }
        return json.dumps(state, ensure_ascii=False, default=str)[:3000]


class TurnTimeout(TimeoutError):
    """Délai dépassé côté harnais, avec l'état du moteur à cet instant."""

    def __init__(self, message: str, diagnostic: str) -> None:
        super().__init__(message)
        self.diagnostic = diagnostic


def _tool_failure_diagnostic(runner: "RealRunner", turn: dict) -> str:
    """Distinction explicite des 4 causes possibles d'un échec S4.

    1. « aucun appel d'outil reçu » : pas de TOOL_CALL_EXECUTED côté moteur
       (le serveur n'a jamais envoyé de tool_call — décision du modèle ou
       question mal entendue : cf. transcription ci-jointe) ;
    2. « appel reçu mais outil non exécuté » : impossible côté code (tout
       tool_call déclenche l'exécution ou trace son erreur) — si le câble
       ne montre pas de tool_response, chercher l'erreur de session ;
    3. « réponse de l'outil envoyée » : TOOL_RESPONSE_SENT + câble ;
    4. « réponse finale du modèle absente » : TOOL_RESPONSE_SENT sans
       assistant non vide (relances vides, tour clos en échec…).
    """
    events = runner.gemini.trace_events()
    executed = [e for e in events if e.get("kind") == "TOOL_CALL_EXECUTED"]
    sent = [e for e in events if e.get("kind") == "TOOL_RESPONSE_SENT"]
    wire_responses = [e for e in WIRE if e.get("kind") == "tool_response"]
    retries = [e for e in events if e.get("kind") == "EMPTY_GENERATION_RETRY"]
    heard = (turn.get("user") or "").strip()
    parts = [
        f"question_entendue={heard!r}",
        f"appels_outil_executes={len(executed)}"
        + (f" {[e.get('name') for e in executed]}" if executed else " (aucun tool_call du serveur)"),
        f"reponses_outil_envoyees={len(sent)}",
        f"tool_response_sur_cable={len(wire_responses)}",
        f"relances_generation_vide={len(retries)}",
    ]
    try:
        allowed, reason = runner.gemini._can_send_now()
        parts.append(f"porte_micro={allowed}:{reason}")
    except Exception:
        pass
    return "S4 diagnostic : " + " ; ".join(parts) + " — fin de trace : " + runner.diagnostic()[:800]


def describe_exception(exc: BaseException) -> str:
    """Classe, message, code/statut d'API et cause chaînée — masqués, bornés."""
    parts, seen, current = [], set(), exc
    while current is not None and id(current) not in seen and len(parts) < 3:
        seen.add(id(current))
        details = [f"{attr}={getattr(current, attr)}" for attr in ("code", "status")
                   if getattr(current, attr, None) not in (None, "")]
        message = " ".join(str(current).split())[:200]
        parts.append(f"{type(current).__name__}: {message}" + (f" [{', '.join(details)}]" if details else ""))
        current = current.__cause__ or current.__context__
    return _KEY_RE.sub("<clé masquée>", " <- ".join(parts))


def classify_failure(exc: BaseException) -> tuple[str, str]:
    """Verdict d'un scénario interrompu par une exception.

    « NON TESTABLE » est réservé aux PRÉREQUIS absents (clé refusée, quota,
    réseau injoignable) : un délai dépassé en attendant la réponse de Jarvis,
    une erreur de session ou de protocole est un ÉCHEC (FAIL), produit ou
    harnais, jamais un non-testable. L'ancien code classait TOUT en
    NON TESTABLE (validation réelle du 2026-10-16 : E11).
    """
    text = describe_exception(exc).lower()
    if isinstance(exc, gl.AuthError) or gl._is_auth_error(exc):
        return "NON TESTABLE", "prérequis absent : clé API refusée"
    if any(marker in text for marker in ("resource_exhausted", "quota", " 429", "code=429")):
        return "NON TESTABLE", "prérequis absent : quota de l'API épuisé"
    if any(marker in text for marker in ("getaddrinfo", "name resolution", "nodename nor servname",
                                         "network is unreachable", "no route to host")):
        return "NON TESTABLE", "prérequis absent : réseau injoignable"
    reason = describe_exception(exc)
    diagnostic = getattr(exc, "diagnostic", "")
    return "FAIL", reason + (f" — diagnostic : {diagnostic}" if diagnostic else "")


# ---------------------------------------------------------------------------
# Scénarios
# ---------------------------------------------------------------------------


async def run_scenarios(runner: RealRunner, mode: str, client, tts_model: str) -> tuple[dict, list]:
    """S1-S4. Identique en mode réel et en selftest (seule l'entrée change).

    Chaque verdict dit ce qui a été RÉELLEMENT exercé : PASS (vérifié), FAIL
    (exercé et incorrect — produit ou harnais, diagnostic joint) ou
    NON TESTABLE (prérequis absent : clé, quota, réseau), cf. ``classify_failure``.
    """
    verdicts: dict[str, str] = {}
    notes: list[str] = []

    def failed(names: tuple[str, ...], label: str, exc: BaseException) -> None:
        verdict, reason = classify_failure(exc)
        for name in names:
            verdicts[name] = verdict
        notes.append(f"{label} : {reason}")

    async def backoff_after_failure(label: str, seconds: float) -> None:
        # Les échecs d'ouverture de session (1008 « entity not found », gigue
        # réseau) se produisent souvent en rafale : un délai croissant avant
        # le scénario suivant laisse le service se stabiliser SANS requalifier
        # l'échec (pas de relance interne du scénario). Ignoré en selftest.
        if mode == "selftest":
            return
        safe_print(f"[pause] {label} en échec — backoff {seconds:.0f}s avant le scénario suivant.")
        await asyncio.sleep(seconds)

    # ---- S1 : GEMINI LIVE RÉEL (connexion + premier tour audio) -------------
    try:
        safe_print("\n=== S1 : connexion + premier tour audio ===")
        turn = await runner.ask("Bonjour Jarvis, tu m'entends bien ?", mode, client, tts_model)
        ok = bool(turn.get("assistant") and turn.get("user"))
        verdicts["GEMINI LIVE RÉEL"] = "PASS" if ok else "FAIL"
        if not ok:
            notes.append(f"S1 : tour incomplet (transcription utilisateur={bool(turn.get('user'))}, "
                         f"réponse={bool(turn.get('assistant'))}) — diagnostic : {runner.diagnostic()}")
    except Exception as exc:
        failed(("GEMINI LIVE RÉEL",), "S1", exc)
        await backoff_after_failure("S1", 5.0)

    # ---- S2 : AUDIO RÉEL + RECONNEXION RÉELLE (le correctif) -----------------
    try:
        safe_print("\n=== S2 : seed committé après reconnexion (correctif v1.7.1) ===")
        await runner.ask("Mon prénom est Simon.", mode, client, tts_model)
        mark = len(WIRE)
        generation = await runner.drop_connection(keep_handle=False)
        await runner.wait_reconnected(generation)
        seed_sends = [e for e in wire_since(mark) if e["kind"] == "client_content"]
        safe_print(
            f"   [câble] session {generation} → {runner.gemini.session_generation} : "
            f"{len(seed_sends)} clientContent après reconnexion "
            f"(turn_complete={[e['turn_complete'] for e in seed_sends]}), "
            f"rejeu confirmé par le serveur={runner.gemini.context_seed_confirmed}"
        )
        turn = await runner.ask("Quel est mon prénom ?", mode, client, tts_model)
        answer = (turn.get("assistant") or "").lower()
        audio_ok = "simon" in answer
        if mode == "text":
            verdicts["AUDIO RÉEL"] = "NON TESTABLE"
            notes.append("S2 : mode texte — aucun audio n'a été envoyé; le verdict audio reste NON TESTABLE.")
        else:
            verdicts["AUDIO RÉEL"] = "PASS" if audio_ok else "FAIL"
        verdicts["RECONNEXION RÉELLE"] = "PASS" if audio_ok and seed_sends else "FAIL"
        if not audio_ok:
            notes.append(
                "S2 : le rappel audio après seed a échoué — vérifier le protocole "
                f"sur ce modèle (cf. scripts/dump_wire_protocol.py, journal E2/E8). Diagnostic : "
                f"{runner.diagnostic()}"
            )
        if not seed_sends:
            notes.append("S2 : AUCUN clientContent observé après reconnexion sans handle.")
    except Exception as exc:
        failed(("AUDIO RÉEL", "RECONNEXION RÉELLE"), "S2", exc)
        await backoff_after_failure("S2", 10.0)

    # ---- S3 : SESSION RESUMPTION RÉELLE ---------------------------------------
    try:
        safe_print("\n=== S3 : reprise de session par handle ===")
        await runner.ask("J'habite à Tours.", mode, client, tts_model)
        # Le sessionResumptionUpdate arrive juste après le turnComplete :
        # on laisse au serveur le temps de le délivrer (borné).
        deadline = time.monotonic() + (5.0 if client is not None else 0.5)
        while not runner.gemini.resumption_handle and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        if not runner.gemini.resumption_handle:
            verdicts["SESSION RESUMPTION RÉELLE"] = "FAIL"
            notes.append("S3 : aucun handle de reprise reçu (sessionResumptionUpdate absent) alors que la "
                         "reprise est configurée — diagnostic : " + runner.diagnostic())
        else:
            mark_wire = len(WIRE)
            mark_ts = time.monotonic()
            generation = await runner.drop_connection(keep_handle=True)
            await runner.wait_reconnected(generation)
            reseed = [e for e in wire_since(mark_wire) if e["kind"] == "client_content"]
            # Preuves prises sur l'horodatage des traces (et non sur la
            # génération) : le repli « checkpoint périmé » est tracé AVANT
            # l'ouverture de la nouvelle session, donc avec l'ancienne
            # génération — un filtre « generation > N » l'aurait rendu
            # invisible (angle mort exact du verdict S3 des runs réels
            # 2026-10).
            recent = [
                e for e in runner.gemini.trace_events()
                if e.get("ts", 0) >= mark_ts
            ]
            resumed = any(e["kind"] == "SESSION_RESUME" for e in recent)
            stale = [
                e for e in recent
                if e["kind"] == "SESSION_RESUME_STALE_CHECKPOINT"
            ]
            rejected = [
                e for e in recent
                if e["kind"] == "SESSION_RESUME_REJECTED"
            ]
            fallback = bool(stale or rejected)
            safe_print(
                f"   [câble] reprise avec handle : session reprise={resumed}, {len(reseed)} clientContent "
                f"(0 attendu si reprise serveur ; 1+ attendu en repli documenté — "
                f"checkpoint périmé={bool(stale)}, handle refusé={bool(rejected)})"
            )
            turn = await runner.ask("Où est-ce que j'habite déjà ?", mode, client, tts_model)
            answer = (turn.get("assistant") or "").lower()
            # Le contexte doit rester intact, et le câble doit être JUSTIFIÉ :
            # * zéro rejeu + preuve de reprise serveur (SESSION_RESUME) ;
            # * ou un rejeu + preuve que la reprise était impossible
            #   (checkpoint périmé détecté avant connexion, ou handle refusé
            #   par le serveur — repli documenté E12/1008, le rejeu est alors
            #   ce qui PRÉSERVE le contexte, pas un doublon).
            # Un rejeu SANS preuve reste un doublon potentiel (échec).
            replay_ok = (len(reseed) == 0 and resumed and not fallback) or (
                len(reseed) >= 1 and fallback
            )
            resume_ok = resumed or fallback
            ok = "tours" in answer and resume_ok and replay_ok
            verdicts["SESSION RESUMPTION RÉELLE"] = "PASS" if ok else "FAIL"
            safe_print(
                f"   [contexte] checkpoint périmé={bool(stale)}, handle refusé={bool(rejected)}, "
                f"rejeux={len(reseed)}, réponse exploitable={'tours' in answer}"
            )
            if "tours" not in answer:
                notes.append("S3 : le rappel après reprise par handle a échoué — diagnostic : "
                             + runner.diagnostic())
            if fallback and len(reseed) >= 1:
                reason = "checkpoint périmé" if stale else "handle refusé par le serveur"
                notes.append(
                    f"S3 : reprise refusée ({reason}) — repli documenté : session neuve + "
                    "rejeu complet, contexte préservé (pas un doublon)."
                )
            elif reseed:
                last_error = runner.errors[-1] if runner.errors else ""
                notes.append(
                    "S3 : un rejeu a été envoyé sans preuve de reprise impossible "
                    "(checkpoint couvrant + handle accepté) (doublon potentiel)."
                    + (f" Dernière erreur session : {last_error}" if last_error else "")
                )
            if not resumed and not fallback:
                notes.append(
                    "S3 : ni reprise confirmée, ni repli tracé (SESSION_RESUME / "
                    "SESSION_RESUME_STALE_CHECKPOINT / SESSION_RESUME_REJECTED absents) — "
                    "état de reprise indémontrable."
                )
    except Exception as exc:
        failed(("SESSION RESUMPTION RÉELLE",), "S3", exc)

    # ---- S4 : OUTILS RÉELS ------------------------------------------------------
    try:
        safe_print("\n=== S4 : appel d'outil réel + suivi ===")
        turn = await runner.ask(
            "Utilise l'outil validation_ping et répète-moi exactement le code qu'il retourne.",
            mode, client, tts_model,
        )
        tools = turn.get("tools", [])
        answer = (turn.get("assistant") or "")
        tool_ok = "validation_ping" in tools and VALIDATION_CODE.lower() in answer.lower()
        if tool_ok:
            follow = await runner.ask(
                "Quel était le code retourné par l'outil, déjà ?",
                mode, client, tts_model,
            )
            tool_ok = VALIDATION_CODE.lower() in (follow.get("assistant") or "").lower()
        verdicts["OUTILS RÉELS"] = "PASS" if tool_ok else "FAIL"
        if not tool_ok:
            notes.append(
                f"S4 : outils={tools} — le code {VALIDATION_CODE} n'a pas été retrouvé "
                "dans la réponse (ni dans le suivi)."
            )
            notes.append(_tool_failure_diagnostic(runner, turn))
    except Exception as exc:
        failed(("OUTILS RÉELS",), "S4", exc)

    return verdicts, notes


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", DEFAULT_MODEL))
    parser.add_argument("--tts-model", default="gemini-2.5-flash-preview-tts")
    parser.add_argument(
        "--mode",
        choices=["auto", "fixtures", "tts", "live-tts", "mic", "text"],
        default="auto",
        help="auto : fixtures versionnées (zéro TTS), repli TTS generateContent "
        "puis session Live (jetons éphémères) ; fixtures : échoue si une "
        "phrase n'a pas de fixture valide",
    )
    parser.add_argument("--record", default=None, help="dossier où écrire les réponses audio (.wav)")
    parser.add_argument(
        "--selftest",
        action="store_true",
        help="valide CE SCRIPT contre le faux serveur (aucune clé, aucune API) — "
        "ne prouve rien sur Gemini réel, seulement que le harness fonctionne.",
    )
    args = parser.parse_args()

    if args.selftest:
        return await run_selftest()

    api_key = load_api_key()
    if not api_key:
        safe_print(
            "\nAUCUNE CLÉ TROUVÉE (GEMINI_API_KEY / GOOGLE_API_KEY / config.json).\n"
            "Sans clé, la validation réelle est NON TESTABLE dans cet environnement.\n"
            "Sur Windows :  set GEMINI_API_KEY=...  puis  py -3 scripts\\validate_real_gemini.py"
        )
        print_verdicts({}, notes=["aucune clé API disponible dans cet environnement."])
        return 2

    _install_wire_tap()

    import google.genai as genai

    client = genai.Client(api_key=api_key)
    record_dir = Path(args.record) if args.record else None
    notes: list[str] = []
    if args.mode == "text":
        notes.append(
            "mode --text : le rappel en TEXTE fonctionne même sans correctif "
            "(thread 111617) — un PASS ne valide PAS le chemin audio."
        )
    if args.mode not in ("auto", "tts", "live-tts", "mic", "text"):
        raise SystemExit(f"mode inconnu : {args.mode}")
    if args.mode == "mic":
        notes.append("mode --mic : l'opérateur doit parler distinctement chaque question.")

    # Préparation des entrées audio : fixtures WAV versionnées (zéro TTS,
    # zéro quota) — TTS generateContent, ou synthèse via une session Live
    # (jeton éphémère sans accès generateContent) en repli. Tout est
    # préparé AVANT les scénarios : aucune dépendance instable en cours de
    # run, et les entrées sont identiques d'une exécution à l'autre.
    input_mode = args.mode
    prompt_cache: dict[str, bytes] = {}
    if input_mode in ("auto", "tts", "live-tts", "fixtures"):
        fixture_cache, fixture_problems = synthesize_from_fixtures(ALL_PROMPTS)
        for problem in fixture_problems:
            safe_print(f"[entrée] FIXTURE INVALIDE : {problem}")
        if fixture_problems:
            notes.extend(f"fixture : {p}" for p in fixture_problems)
        if len(fixture_cache) == len(ALL_PROMPTS):
            prompt_cache = fixture_cache
            input_mode = "fixtures"
            safe_print(
                f"[entrée] {len(fixture_cache)} phrase(s) depuis les fixtures "
                "versionnées (zéro requête TTS, zéro quota Gemini)."
            )
        elif input_mode == "fixtures":
            safe_print(
                "[entrée] fixtures incomplètes ou invalides (cf. ci-dessus) — "
                "mode fixtures exigé : échec explicite, aucun audio douteux envoyé."
            )
            print_verdicts({}, notes)
            return 3
        else:
            safe_print(
                "[entrée] fixtures incomplètes — repli sur la synthèse TTS "
                "Gemini pour les phrases manquantes (quota limité)."
            )
    if input_mode in ("auto", "tts", "live-tts"):
        try:
            if input_mode in ("auto", "tts"):
                for prompt in ALL_PROMPTS:
                    prompt_cache[prompt] = await synthesize_prompt(
                        client, prompt, args.tts_model
                    )
                input_mode = "tts"
            else:
                raise RuntimeError("mode live-tts demandé explicitement")
        except Exception as exc:
            if args.mode == "tts":
                raise
            safe_print(
                f"[entrée] TTS generateContent indisponible "
                f"({type(exc).__name__}) — synthèse des énoncés via une session Live."
            )
            prompt_cache = await synthesize_all_via_live(client, args.model, ALL_PROMPTS)
            input_mode = "live-tts"
        notes.append(
            f"entrée : {input_mode} — les questions sont de l'AUDIO RÉEL envoyé par "
            "send_realtime_input (VAD serveur, transcription) ; seule la source "
            "de synthèse de la voix varie."
        )

    runner = RealRunner(api_key, args.model, record_dir)
    if prompt_cache:
        runner.input_policy = lambda prompt: prompt_cache[prompt]
    patchers = _install_validation_tool()
    for patcher in patchers:
        patcher.start()
    # Précondition S4 (v1.9.x) : l'outil de validation doit être ENREGISTRÉ
    # dans la session qui exécute le test — fonction exécutable ET
    # déclaration envoyée au serveur. Une absence ici expliquerait un
    # « aucun tool_call reçu » sans même ouvrir de session.
    declared_names = [d.get("name") for d in gl.TOOL_DECLARATIONS]
    precondition = (
        "validation_ping" in gl.TOOL_FUNCTIONS
        and "validation_ping" in declared_names
    )
    if not precondition:
        safe_print(
            "[S4] PRÉCONDITION INVALIDE : validation_ping absent des outils "
            f"installés (fonctions={sorted(gl.TOOL_FUNCTIONS)[:5]}…, "
            f"déclarations={declared_names})"
        )
    verdicts: dict[str, str] = {}
    try:
        try:
            await runner.start()
        except Exception as exc:
            # Aucune session : rien n'a pu être exercé. Prérequis absent
            # (clé, quota, réseau) → NON TESTABLE ; autre cause → FAIL.
            verdict, reason = classify_failure(exc)
            if runner.errors and verdict == "FAIL":
                verdict, reason = classify_failure(RuntimeError(runner.errors[-1]))
            verdicts = {name: verdict for name in VERDICT_NAMES}
            notes.append(f"connexion initiale impossible : {reason}")
        else:
            verdicts, scenario_notes = await run_scenarios(
                runner, input_mode, client, args.tts_model
            )
            notes.extend(scenario_notes)
            notes.append(
                "précondition S4 : outil validation_ping enregistré="
                f"{precondition} (fonction + déclaration envoyée au serveur)"
            )
    finally:
        for patcher in patchers:
            patcher.stop()
        await runner.stop()

    print_verdicts(verdicts, notes)
    return 0 if all(v == "PASS" for v in verdicts.values()) else 1


async def run_selftest() -> int:
    """Exécute les scénarios du script contre le faux serveur Live.

    But : garantir que le harness lui-même (runner, coupures, verdicts, tap du
    câble) fonctionne AVANT de l'utiliser avec une vraie clé. Le résultat
    « PASS » ici ne dit RIEN de l'API Gemini réelle.
    """
    from tests.live_harness import FakeClient, FakeLiveServer

    server = FakeLiveServer("2.x")
    server.oracle.register(r"habite", lambda pairs: "Tu habites à Tours.")
    server.oracle.register(r"code", lambda pairs: "Le code retourné par l'outil est KILO-7.")
    _install_fake_wire_tap(server)

    runner = RealRunner("cle-factice-selftest", DEFAULT_MODEL, None)
    # Le faux client remplace le vrai : aucune requête réseau n'est émise.
    runner.gemini.client = FakeClient(server)
    # 3 chunks de 100 ms (le pas de speak_pcm) : la file d'attente du faux
    # serveur est programmée pour 3 chunks par énoncé.
    chunk_bytes = SAMPLE_RATE * 2 * CHUNK_MS // 1000  # 16 kHz, s16le, mono
    junk_pcm = b"\x01\x02" * (chunk_bytes * 3 // 2)  # 3 chunks exactement

    def policy(prompt: str) -> bytes:
        if "validation_ping" in prompt and server.next_tool_script is None:
            server.next_tool_script = (r"validation_ping", "validation_ping", {})
        server.current.queue_utterance(prompt, 3)
        return junk_pcm

    runner.input_policy = policy

    patchers = _install_validation_tool()
    for patcher in patchers:
        patcher.start()
    try:
        await runner.start()
        verdicts, notes = await run_scenarios(runner, "selftest", None, "")
    finally:
        for patcher in patchers:
            patcher.stop()
        await runner.stop()

    notes.append("SELFTEST : exécuté contre le faux serveur — aucun appel réseau.")
    print_verdicts(verdicts, notes)
    ok = all(v == "PASS" for v in verdicts.values())
    return 0 if ok else 1


def _install_fake_wire_tap(server) -> None:
    """Alimente le WIRE du script depuis le faux serveur (selftest seulement)."""
    from tests.live_harness import FakeLiveSession

    orig_cc = FakeLiveSession.send_client_content
    orig_tr = FakeLiveSession.send_tool_response

    async def cc(self, turns=None, turn_complete=True):
        WIRE.append(
            {
                "ts": time.monotonic(),
                "kind": "client_content",
                "turn_complete": turn_complete,
                "n_turns": len(turns) if turns else 0,
            }
        )
        return await orig_cc(self, turns=turns, turn_complete=turn_complete)

    async def tr(self, function_responses):
        WIRE.append(
            {
                "ts": time.monotonic(),
                "kind": "tool_response",
                "tools": [getattr(r, "name", "?") for r in function_responses],
            }
        )
        return await orig_tr(self, function_responses=function_responses)

    FakeLiveSession.send_client_content = cc
    FakeLiveSession.send_tool_response = tr


VERDICT_NAMES = (
    "GEMINI LIVE RÉEL",
    "AUDIO RÉEL",
    "RECONNEXION RÉELLE",
    "SESSION RESUMPTION RÉELLE",
    "OUTILS RÉELS",
)


def print_verdicts(verdicts: dict[str, str], notes: list[str] | None = None) -> None:
    safe_print("\n" + "=" * 60)
    for name in VERDICT_NAMES:
        verdict = verdicts.get(name, "NON TESTABLE")
        safe_print(f"{name} : {verdict}")
    if notes:
        safe_print("-" * 60)
        for note in notes:
            safe_print(f"note : {note}")
    safe_print("=" * 60)


def _install_validation_tool():
    """Remplace les outils par UN outil de validation déterministe et bénin."""
    from unittest import mock

    def validation_ping():
        return {"success": True, "code": VALIDATION_CODE, "message": "canal outil fonctionnel"}

    declaration = [
        {
            "name": "validation_ping",
            "description": (
                "Outil de validation de la connexion : retourne un code de test. "
                "Appelle-le quand on te le demande et répète le code retourné."
            ),
            # Forme OpenAPI restreinte identique au produit (le SDK
            # normalise le type vers son enum — même câble, lisible).
            "parameters": {"type": "object", "properties": {}, "required": []},
        }
    ]
    return mock.patch.object(
        gl, "TOOL_DECLARATIONS", declaration
    ), mock.patch.dict(gl.TOOL_FUNCTIONS, {"validation_ping": validation_ping})


if __name__ == "__main__":
    # Diagnostic AUDIO RÉEL : tracer chaque envoi d'entrée (taille + niveau
    # estimé) — distingue un envoi absent d'un envoi à niveau nul.
    os.environ.setdefault("JARVIS_AUDIO_TRACE", "1")
    raise SystemExit(asyncio.run(main()))
