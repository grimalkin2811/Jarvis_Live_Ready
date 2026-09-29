"""Validation FINALE sur l'API Gemini Live RÉELLE (harness Windows, clé requise).

Ce script est le complément explicite des tests du faux serveur
(``tests/test_live_context_harness.py``) : il exécute le VRAI pipeline vocal de
Jarvis (``src/gemini_live.py``, ses reconnexions, son rejeu de contexte, ses
outils) contre l'API Gemini Live réelle, avec de l'AUDIO RÉEL en entrée.

Les tests du faux serveur ne sont PAS équivalents à cette validation : ils
prouvent le protocole côté client, celui-ci prouve (ou non) que le serveur
réel se comporte comme prévu.

Scénarios et verdicts produits (format imposé) ::

    GEMINI LIVE RÉEL : PASS / NON TESTABLE
    AUDIO RÉEL : PASS / NON TESTABLE
    RECONNEXION RÉELLE : PASS / NON TESTABLE
    SESSION RESUMPTION RÉELLE : PASS / NON TESTABLE
    OUTILS RÉELS : PASS / NON TESTABLE

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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import gemini_live as gl  # noqa: E402
from src.conversation import ConversationContext  # noqa: E402

TURN_TIMEOUT = 60.0  # le natif audio peut être lent au premier tour
TTS_TIMEOUT = 45.0
CHUNK_MS = 100
SAMPLE_RATE = 16000

_KEY_RE = re.compile(r"AIza[A-Za-z0-9_\-]{10,}")
VALIDATION_CODE = "KILO-7"

WIRE: list[dict] = []  # traçage des envois (patch du SDK, voir _install_wire_tap)


def safe_print(*args, **kwargs) -> None:
    """Print qui ne peut JAMAIS laisser fuiter une clé API."""
    text = " ".join(str(a) for a in args)
    print(_KEY_RE.sub("<clé masquée>", text), **kwargs, flush=True)


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
        kind = "realtime_text" if kwargs.get("text") is not None else "realtime_audio"
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
    def __init__(self, api_key: str, model: str, record_dir: Path | None):
        self.conversation = ConversationContext(max_turns=20, max_tokens=4096)
        self.turn_log: list[dict] = []
        self._current: dict = {"user": "", "assistant": "", "tools": [], "interrupted": False}
        self._turn_done = asyncio.Event()
        self._connected = asyncio.Event()
        self.connected = self._connected  # API publique pour les scénarios
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._audio_chunks: list[bytes] = []
        self._record_dir = record_dir
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
        await asyncio.wait_for(self._connected.wait(), timeout=TURN_TIMEOUT)

    async def stop(self) -> None:
        self._stop.set()
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
            except Exception as exc:
                safe_print(f"   [session] erreur : {type(exc).__name__}")
                if not gemini.reconnect_requested:
                    await asyncio.sleep(1.0)
            finally:
                try:
                    await gemini.close()
                except Exception:
                    pass

    # -- interactions ------------------------------------------------------------
    async def _wait_sendable(self, timeout: float = TURN_TIMEOUT) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.gemini.can_send():
                return
            await asyncio.sleep(0.05)
        raise TimeoutError("session non prête à recevoir de l'audio")

    async def speak_pcm(self, pcm: bytes) -> dict:
        """Envoie de l'audio réel par morceaux, puis attend la fin du tour."""
        await self._wait_sendable()
        self._turn_done.clear()
        self._audio_chunks.clear()
        step = int(SAMPLE_RATE * 2 * CHUNK_MS / 1000)
        for i in range(0, len(pcm), step):
            await self.gemini.send_audio(pcm[i : i + step])
            await asyncio.sleep(CHUNK_MS / 1000)
        await asyncio.wait_for(self._turn_done.wait(), timeout=TURN_TIMEOUT)
        return self.turn_log[-1] if self.turn_log else {}

    async def speak_text(self, text: str) -> dict:
        await self._wait_sendable()
        self._turn_done.clear()
        await self.gemini.session.send_realtime_input(text=text)
        await asyncio.wait_for(self._turn_done.wait(), timeout=TURN_TIMEOUT)
        return self.turn_log[-1] if self.turn_log else {}

    async def ask(self, prompt: str, mode: str, client=None, tts_model: str = "") -> dict:
        safe_print(f"   [question] {prompt}")
        if self.input_policy is not None:
            turn = await self.speak_pcm(self.input_policy(prompt))
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

    async def drop_connection(self, keep_handle: bool) -> None:
        """Coupure brutale (équivalent perte réseau), avec ou sans le handle."""
        if not keep_handle:
            self.gemini.resumption_handle = None
        await self.gemini._shutdown_session()  # le runner reconnecte aussitôt


# ---------------------------------------------------------------------------
# Scénarios
# ---------------------------------------------------------------------------


async def run_scenarios(runner: RealRunner, mode: str, client, tts_model: str) -> tuple[dict, list]:
    """S1-S4. Identique en mode réel et en selftest (seule l'entrée change)."""
    verdicts: dict[str, str] = {}
    notes: list[str] = []

    # ---- S1 : GEMINI LIVE RÉEL (connexion + premier tour audio) -------------
    try:
        safe_print("\n=== S1 : connexion + premier tour audio ===")
        turn = await runner.ask("Bonjour Jarvis, tu m'entends bien ?", mode, client, tts_model)
        ok = bool(turn.get("assistant") or turn.get("user"))
        verdicts["GEMINI LIVE RÉEL"] = "PASS" if ok else "FAIL"
        if not ok:
            notes.append("S1 : aucune transcription reçue (ni user ni assistant).")
    except Exception as exc:
        verdicts["GEMINI LIVE RÉEL"] = "NON TESTABLE"
        notes.append(f"S1 : {type(exc).__name__} — {exc}")

    # ---- S2 : AUDIO RÉEL + RECONNEXION RÉELLE (le correctif) -----------------
    try:
        safe_print("\n=== S2 : seed committé après reconnexion (correctif v1.7.1) ===")
        await runner.ask("Mon prénom est Simon.", mode, client, tts_model)
        mark = len(WIRE)
        await runner.drop_connection(keep_handle=False)
        await asyncio.wait_for(runner.connected.wait(), timeout=TURN_TIMEOUT)
        runner.connected.clear()
        seed_sends = [e for e in wire_since(mark) if e["kind"] == "client_content"]
        safe_print(
            f"   [câble] {len(seed_sends)} clientContent après reconnexion "
            f"(turn_complete={[e['turn_complete'] for e in seed_sends]})"
        )
        turn = await runner.ask("Quel est mon prénom ?", mode, client, tts_model)
        answer = (turn.get("assistant") or "").lower()
        audio_ok = "simon" in answer
        verdicts["AUDIO RÉEL"] = "PASS" if audio_ok else "FAIL"
        verdicts["RECONNEXION RÉELLE"] = "PASS" if audio_ok else "FAIL"
        if not audio_ok:
            notes.append(
                "S2 : le rappel audio après seed a échoué — vérifier le protocole "
                "sur ce modèle (cf. scripts/dump_wire_protocol.py, journal E2/E8)."
            )
        if not seed_sends:
            notes.append("S2 : AUCUN clientContent observé après reconnexion sans handle.")
    except Exception as exc:
        verdicts["AUDIO RÉEL"] = "NON TESTABLE"
        verdicts["RECONNEXION RÉELLE"] = "NON TESTABLE"
        notes.append(f"S2 : {type(exc).__name__} — {exc}")

    # ---- S3 : SESSION RESUMPTION RÉELLE ---------------------------------------
    try:
        safe_print("\n=== S3 : reprise de session par handle ===")
        await runner.ask("J'habite à Tours.", mode, client, tts_model)
        # Le sessionResumptionUpdate arrive juste après le turnComplete :
        # on laisse une seconde au serveur pour le délivrer.
        await asyncio.sleep(1.0 if client is not None else 0.05)
        if not runner.gemini.resumption_handle:
            verdicts["SESSION RESUMPTION RÉELLE"] = "NON TESTABLE"
            notes.append("S3 : aucun handle de reprise reçu (update sessionResumptionUpdate absent).")
        else:
            mark = len(WIRE)
            await runner.drop_connection(keep_handle=True)
            await asyncio.wait_for(runner.connected.wait(), timeout=TURN_TIMEOUT)
            runner.connected.clear()
            reseed = [e for e in wire_since(mark) if e["kind"] == "client_content"]
            safe_print(
                f"   [câble] reprise avec handle : {len(reseed)} clientContent "
                "(0 attendu : le serveur restaure l'historique lui-même)"
            )
            turn = await runner.ask("Où est-ce que j'habite déjà ?", mode, client, tts_model)
            answer = (turn.get("assistant") or "").lower()
            ok = "tours" in answer and len(reseed) == 0
            verdicts["SESSION RESUMPTION RÉELLE"] = "PASS" if ok else "FAIL"
            if "tours" not in answer:
                notes.append("S3 : le rappel après reprise par handle a échoué.")
            if reseed:
                notes.append("S3 : un rejeu a été envoyé malgré le handle (doublon potentiel).")
    except Exception as exc:
        verdicts["SESSION RESUMPTION RÉELLE"] = "NON TESTABLE"
        notes.append(f"S3 : {type(exc).__name__} — {exc}")

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
    except Exception as exc:
        verdicts["OUTILS RÉELS"] = "NON TESTABLE"
        notes.append(f"S4 : {type(exc).__name__} — {exc}")

    return verdicts, notes


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025"))
    parser.add_argument("--tts-model", default="gemini-2.5-flash-preview-tts")
    parser.add_argument("--mode", choices=["tts", "mic", "text"], default="tts")
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
    if args.mode == "mic":
        notes.append("mode --mic : l'opérateur doit parler distinctement chaque question.")

    runner = RealRunner(api_key, args.model, record_dir)
    patchers = _install_validation_tool()
    for patcher in patchers:
        patcher.start()
    try:
        await runner.start()
        verdicts, scenario_notes = await run_scenarios(
            runner, args.mode, client, args.tts_model
        )
        notes.extend(scenario_notes)
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

    runner = RealRunner(
        "cle-factice-selftest", "gemini-2.5-flash-native-audio-preview-12-2025", None
    )
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


def print_verdicts(verdicts: dict[str, str], notes: list[str] | None = None) -> None:
    order = [
        "GEMINI LIVE RÉEL",
        "AUDIO RÉEL",
        "RECONNEXION RÉELLE",
        "SESSION RESUMPTION RÉELLE",
        "OUTILS RÉELS",
    ]
    safe_print("\n" + "=" * 60)
    for name in order:
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
            "parameters": {"type": "OBJECT", "properties": {}, "required": []},
        }
    ]
    return mock.patch.object(
        gl, "TOOL_DECLARATIONS", declaration
    ), mock.patch.dict(gl.TOOL_FUNCTIONS, {"validation_ping": validation_ping})


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
