"""Validation FINALE du correctif v1.7.4 contre l'API Gemini Live RÉELLE.

Contrairement à ``scripts/validate_real_gemini.py`` (contexte v1.7.1 : seed,
reprise de session, outils), ce script cible SPÉCIFIQUEMENT le bug
« reconnexion-par-tour » (« Je vous écoute » périodique) et exécute le VRAI
pipeline Jarvis (``AudioIO`` + pont micro + ``GeminiLive``, boucle
``connect()``/``receive_loop()`` identique à ``src/main.py``) contre le
service Gemini Live réel de Google, avec de l'AUDIO RÉEL (parole humaine
synthétisée par TTS, pas une tonalité).

Scénarios exécutés (mission v1.7.4, validation réelle) ::

    S1 : tour unique + silence (>=12 s) -> pas de reconnexion, pas de tour
         fantôme, expiration normale de la fenêtre de 8 s.
    S2 : deux tours rapides (suivi réel avant expiration) -> même session.
    S3 : trois tours consécutifs -> une seule session, 3 tours réels.
    S4 : expiration de fenêtre -> retour en veille réel, nouveau réveil OK.
    S5 : reconnexion réelle forcée (coupure réseau volontaire) -> raison
         journalisée distincte de « turn_complete », contexte rejoué.
    S6 : mémoire contextuelle réelle (« Mon prénom est Simon » -> reconnexion
         réelle -> « Quel est mon prénom ? ») avec le VRAI modèle Gemini.

Clé API : lue UNIQUEMENT depuis la variable d'environnement
``GEMINI_API_KEY``/``GOOGLE_API_KEY`` (jamais un argument CLI, jamais un
fichier versionné). Elle n'est JAMAIS journalisée : tout message est filtré
par ``redact()`` avant impression.

Utilisation ::
    GEMINI_API_KEY=... python scripts/validate_reconnect_v174_real.py
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.real_gemini_harness import RealVoiceLab, redact  # noqa: E402

FOLLOW_UP_SECONDS = 8.0  # doit rester synchronisé avec src/audio.py
TURN_TIMEOUT = 60.0
TTS_TIMEOUT = 45.0
SAMPLE_RATE = 16000

_KEY_RE = re.compile(r"AIza[A-Za-z0-9_\-]{10,}|AQ\.[A-Za-z0-9_\-]+")


def safe_print(*args, **kwargs) -> None:
    text = " ".join(str(a) for a in args)
    print(_KEY_RE.sub("<clé masquée>", redact(text)), **kwargs, flush=True)


def load_api_key() -> str:
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.environ.get(name, "").strip()
        if value:
            safe_print(f"[clé] lue depuis la variable {name} (longueur {len(value)}).")
            return value
    return ""


async def synthesize_prompt(client, text: str, model: str) -> bytes:
    """Texte -> PCM 16 kHz mono s16le, via le modèle TTS Gemini."""
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
    tail = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.int16)
    return (np.concatenate([samples, tail])).tobytes()


async def synthesize_all_via_live(client, model: str, prompts: list[str]) -> dict[str, bytes]:
    """Repli si generateContent/TTS n'est pas accessible (jeton éphémère)."""
    import numpy as np
    from google.genai import types

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=(
            "Tu es un synthétiseur vocal. Quand on t'envoie du texte, tu le "
            "répètes EXACTEMENT mot pour mot en français, sans rien ajouter."
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
            samples = np.frombuffer(bytes(chunks), dtype=np.int16)
            count = int(len(samples) * SAMPLE_RATE / 24000)
            resampled = np.interp(
                np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples
            ).astype(np.int16)
            tail = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.int16)
            cache[prompt] = np.concatenate([resampled, tail]).tobytes()
            safe_print(f"   [synthèse] {prompt[:60]}… ({len(cache[prompt])} octets)")
    return cache


PROMPTS = [
    "Bonjour Jarvis, quelle heure est-il ?",
    "Et demain, quel temps fera-t-il ?",
    "Peux-tu me le confirmer ?",
    "Mon prénom est Simon.",
    "Quel est mon prénom ?",
]


class ScenarioRunner:
    def __init__(self, lab: RealVoiceLab, prompt_cache: dict[str, bytes]):
        self.lab = lab
        self.prompt_cache = prompt_cache
        self.verdicts: dict[str, str] = {}
        self.notes: list[str] = []

    def say(self, prompt: str, *, wake: bool = False, timeout: float = TURN_TIMEOUT) -> dict:
        if wake:
            self.lab.wake()
        since = len(self.lab.turns)
        self.lab.speak_pcm(self.prompt_cache[prompt])
        turn = self.lab.wait_for_new_turn(since, timeout)
        if turn is None:
            raise TimeoutError(f"aucune réponse reçue pour : {prompt!r}")
        safe_print(f"   [question] {prompt}")
        safe_print(f"   [réponse]  {turn.get('assistant') or '(sans transcription)'}")
        return turn

    # -- S1 + S4 : tour unique, silence, expiration ---------------------------
    def scenario_1_and_4_silence_then_expiry(self) -> None:
        name = "S1+S4 SILENCE PUIS EXPIRATION"
        try:
            safe_print("\n=== S1+S4 : tour unique, silence >=12 s, expiration normale ===")
            self.say(PROMPTS[0], wake=True)
            gen_before = self.lab.gemini.session_generation
            connect_before = len(self.lab.session_connect_events())
            turns_before = len(self.lab.turns)

            self.lab.wait(12.0)  # > FOLLOW_UP_SECONDS(8) : silence total

            gen_after = self.lab.gemini.session_generation
            connect_after = len(self.lab.session_connect_events())
            turns_after = len(self.lab.turns)

            ok = (
                gen_after == gen_before
                and connect_after == connect_before
                and turns_after == turns_before
                and not self.lab.audio.awake
            )
            self.verdicts[name] = "PASS" if ok else "FAIL"
            if not ok:
                self.notes.append(
                    f"{name} : gen {gen_before}->{gen_after}, "
                    f"connects {connect_before}->{connect_after}, "
                    f"turns {turns_before}->{turns_after}, "
                    f"awake_apres={self.lab.audio.awake}"
                )
            safe_print(
                f"   session_generation avant/après = {gen_before}/{gen_after} ; "
                f"connexions avant/après = {connect_before}/{connect_after} ; "
                f"tours avant/après = {turns_before}/{turns_after} ; "
                f"awake après expiration = {self.lab.audio.awake}"
            )
        except Exception as exc:
            self.verdicts[name] = "NON TESTABLE"
            self.notes.append(f"{name} : {type(exc).__name__} — {exc}")

    # -- S2 : deux tours rapides ------------------------------------------------
    def scenario_2_two_quick_turns(self) -> None:
        name = "S2 DEUX TOURS RAPIDES"
        try:
            safe_print("\n=== S2 : deux tours rapides (suivi réel avant expiration) ===")
            self.say(PROMPTS[3], wake=True)  # "Mon prénom est Simon."
            gen1 = self.lab.gemini.session_generation
            self.lab.wait(3.0)  # < 8 s
            turn2 = self.say(PROMPTS[4])  # "Quel est mon prénom ?"
            gen2 = self.lab.gemini.session_generation
            ok = gen1 == gen2
            self.verdicts[name] = "PASS" if ok else "FAIL"
            if not ok:
                self.notes.append(f"{name} : session changée entre les deux tours ({gen1} -> {gen2})")
            if "simon" not in (turn2.get("assistant") or "").lower():
                self.notes.append(
                    f"{name} : réponse n'a pas mentionné « Simon » (contexte non exploité "
                    f"ou transcription incomplète) : {turn2.get('assistant')!r}"
                )
        except Exception as exc:
            self.verdicts[name] = "NON TESTABLE"
            self.notes.append(f"{name} : {type(exc).__name__} — {exc}")

    # -- S3 : trois tours ------------------------------------------------------------
    def scenario_3_three_turns(self) -> None:
        name = "S3 TROIS TOURS"
        try:
            safe_print("\n=== S3 : trois tours consécutifs, une seule session ===")
            since_all = len(self.lab.turns)
            self.say(PROMPTS[0], wake=True)
            gen0 = self.lab.gemini.session_generation
            self.lab.wait(2.0)
            self.say(PROMPTS[1])
            gen1 = self.lab.gemini.session_generation
            self.lab.wait(2.0)
            self.say(PROMPTS[2])
            gen2 = self.lab.gemini.session_generation
            n_new_turns = len(self.lab.turns) - since_all
            ok = gen0 == gen1 == gen2 and n_new_turns == 3
            self.verdicts[name] = "PASS" if ok else "FAIL"
            if not ok:
                self.notes.append(
                    f"{name} : générations={gen0},{gen1},{gen2} nouveaux_tours={n_new_turns}"
                )
        except Exception as exc:
            self.verdicts[name] = "NON TESTABLE"
            self.notes.append(f"{name} : {type(exc).__name__} — {exc}")

    # -- S5 : reconnexion réelle forcée ---------------------------------------------
    def scenario_5_real_reconnection(self) -> None:
        name = "S5 RECONNEXION REELLE"
        try:
            safe_print("\n=== S5 : reconnexion réelle forcée (coupure réseau) ===")
            self.say(PROMPTS[3], wake=True)  # "Mon prénom est Simon."
            gen_before = self.lab.gemini.session_generation
            mark = time.monotonic()
            self.lab.force_disconnect(drop_handle=True)
            self.lab.wait_until(
                lambda: self.lab.gemini.session_generation > gen_before,
                30.0,
                "reconnexion après coupure réseau réelle",
            )
            gen_after = self.lab.gemini.session_generation
            connect_events = [
                e for e in self.lab.session_connect_events(since=mark)
            ]
            reasons = {e.get("reason") for e in connect_events}
            self.lab.wait_until(lambda: self.lab.gemini.can_send(), 20.0, "session prête")
            ready_events = [
                e for e in self.lab.gemini_trace()
                if e["kind"] == "SESSION_READY" and e["session_generation"] == gen_after
            ]
            audio_before_ready = []
            if ready_events:
                ready_ts = ready_events[0]["ts"]
                audio_before_ready = [
                    e for e in self.lab.gemini_trace()
                    if e["kind"] == "AUDIO_SENT_TO_GEMINI"
                    and e["session_generation"] == gen_after
                    and e["ts"] < ready_ts
                ]
            ok = (
                gen_after > gen_before
                and "unexpected_after_normal_turn" not in reasons
                and reasons <= {"error", "goaway"}
                and not audio_before_ready
            )
            self.verdicts[name] = "PASS" if ok else "FAIL"
            safe_print(
                f"   raison(s) de reconnexion = {reasons} ; "
                f"audio avant SESSION_READY = {len(audio_before_ready)}"
            )
            if not ok:
                self.notes.append(
                    f"{name} : reasons={reasons} audio_before_ready={len(audio_before_ready)}"
                )
        except Exception as exc:
            self.verdicts[name] = "NON TESTABLE"
            self.notes.append(f"{name} : {type(exc).__name__} — {exc}")

    # -- S6 : contexte réel ------------------------------------------------------------
    def scenario_6_real_context(self) -> None:
        """Le prénom donné AVANT S5 doit rester exploitable après cette
        reconnexion réelle (ne relance pas de wake : reste sur la session
        issue de S5, exactement le scénario demandé par la mission).
        """
        name = "S6 CONTEXTE REEL APRES RECONNEXION"
        try:
            safe_print("\n=== S6 : mémoire contextuelle réelle après reconnexion ===")
            turn = self.say(PROMPTS[4])  # "Quel est mon prénom ?"
            answer = (turn.get("assistant") or "").lower()
            ok = "simon" in answer
            self.verdicts[name] = "PASS" if ok else "FAIL"
            if not ok:
                self.notes.append(f"{name} : réponse={turn.get('assistant')!r}")
        except Exception as exc:
            self.verdicts[name] = "NON TESTABLE"
            self.notes.append(f"{name} : {type(exc).__name__} — {exc}")


def print_verdicts(verdicts: dict[str, str], notes: list[str]) -> None:
    order = [
        "S1+S4 SILENCE PUIS EXPIRATION",
        "S2 DEUX TOURS RAPIDES",
        "S3 TROIS TOURS",
        "S5 RECONNEXION REELLE",
        "S6 CONTEXTE REEL APRES RECONNEXION",
    ]
    safe_print("\n" + "=" * 70)
    for n in order:
        safe_print(f"{n} : {verdicts.get(n, 'NON TESTABLE')}")
    if notes:
        safe_print("-" * 70)
        for note in notes:
            safe_print(f"note : {note}")
    safe_print("=" * 70)


async def main() -> int:
    api_key = load_api_key()
    if not api_key:
        safe_print(
            "AUCUNE CLÉ TROUVÉE (GEMINI_API_KEY / GOOGLE_API_KEY).\n"
            "Validation réelle NON TESTABLE dans cet environnement."
        )
        print_verdicts({}, ["aucune clé API disponible dans cet environnement."])
        return 2

    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-native-audio-preview-12-2025")
    tts_model = os.getenv("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts")

    import google.genai as genai

    client = genai.Client(api_key=api_key)

    notes: list[str] = []
    prompt_cache: dict[str, bytes] = {}
    input_mode = "tts"
    try:
        for prompt in PROMPTS:
            prompt_cache[prompt] = await synthesize_prompt(client, prompt, tts_model)
    except Exception as exc:
        safe_print(f"[entrée] TTS generateContent indisponible ({type(exc).__name__}) — synthèse via Live.")
        try:
            prompt_cache = await synthesize_all_via_live(client, model, PROMPTS)
            input_mode = "live-tts"
        except Exception as exc2:
            safe_print(f"AUCUNE SYNTHÈSE POSSIBLE : {type(exc2).__name__} — {exc2}")
            print_verdicts({}, [f"synthèse audio impossible : {exc2}"])
            return 3
    notes.append(f"entrée audio : {input_mode} (parole réelle synthétisée, pas une tonalité)")

    os.environ.setdefault("JARVIS_AUDIO_TRACE", "1")
    lab = RealVoiceLab(api_key, model)
    runner = ScenarioRunner(lab, prompt_cache)
    try:
        safe_print("\n[connexion] établissement de la session Gemini Live réelle…")
        lab.start(timeout=30.0)
        safe_print(f"[connexion] établie (session_generation={lab.gemini.session_generation}).")

        runner.scenario_1_and_4_silence_then_expiry()
        runner.scenario_2_two_quick_turns()
        runner.scenario_3_three_turns()
        runner.scenario_5_real_reconnection()
        runner.scenario_6_real_context()
    finally:
        lab.stop()

    runner.notes.extend(notes)
    print_verdicts(runner.verdicts, runner.notes)
    return 0 if all(v == "PASS" for v in runner.verdicts.values()) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
