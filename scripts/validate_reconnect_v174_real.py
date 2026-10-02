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

Synthèse de l'audio d'ENTRÉE (les 5 prompts de ``PROMPTS``) :
    Backend PAR DÉFAUT = synthèse LOCALE (``pyttsx3`` -> SAPI5/NSSS/espeak),
    sans appel réseau ni quota Gemini. ``gemini-2.5-flash-preview-tts`` via
    ``generateContent`` est limité sur le palier disponible à 3
    requêtes/minute ET 10 requêtes/jour — un quota épuisé en une seule
    exécution du script si on s'y fie comme source principale, ce qui
    invalidait les runs de validation précédents. Le backend Gemini (TTS
    dédié, puis repli conversationnel Live si besoin) n'est utilisé qu'en
    secours si aucun moteur local n'est disponible, et chaque prompt
    synthétisé (quel que soit le backend) est mis en cache disque
    (``scripts/.tts_cache/``, jamais versionné) pour ne plus jamais
    reconsommer de quota une fois obtenu avec succès.
    Variables d'environnement : ``JARVIS_VALIDATE_TTS_BACKEND``
    (``auto`` par défaut, ou ``local``/``gemini`` pour forcer un backend) et
    ``JARVIS_VALIDATE_TTS_NO_CACHE=1`` (ignorer le cache disque).

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


# =====================================================================
# Cache disque des prompts synthétisés (clé = backend + modèle + texte).
#
# Root cause (signalé par l'utilisateur) : l'API Gemini 2.5 Flash TTS
# (``generateContent`` avec ``response_modalities=["AUDIO"]``) est limitée à
# 3 requêtes/minute ET 10 requêtes/jour sur le palier disponible ici — le
# quota est donc épuisé en une seule exécution du script (5 prompts) voire
# avant même de la terminer. Un cache disque persistant (indépendant du
# backend utilisé) garantit qu'un prompt donné n'est JAMAIS resynthétisé une
# fois obtenu avec succès, quel que soit le nombre de ré-exécutions du
# script — y compris si le backend Gemini doit être utilisé un jour.
# =====================================================================

_CACHE_DIR = Path(__file__).resolve().parent / ".tts_cache"


def _cache_path(backend: str, model: str, prompt: str) -> Path:
    import hashlib

    key = hashlib.sha256(f"{backend}|{model}|{SAMPLE_RATE}|{prompt}".encode("utf-8")).hexdigest()[:32]
    return _CACHE_DIR / f"{key}.pcm"


def _cache_load(backend: str, model: str, prompt: str) -> bytes | None:
    if os.environ.get("JARVIS_VALIDATE_TTS_NO_CACHE") == "1":
        return None
    path = _cache_path(backend, model, prompt)
    if path.exists():
        try:
            data = path.read_bytes()
            if data:
                return data
        except OSError:
            pass
    return None


def _cache_store(backend: str, model: str, prompt: str, data: bytes) -> None:
    try:
        _CACHE_DIR.mkdir(exist_ok=True)
        _cache_path(backend, model, prompt).write_bytes(data)
    except OSError:
        pass


# =====================================================================
# Backend PRIMAIRE (par défaut) : synthèse vocale LOCALE (pyttsx3 ->
# SAPI5 sur Windows, NSSpeechSynthesizer sur macOS, espeak sur Linux).
#
# Aucun appel réseau, AUCUN quota Gemini consommé : cela règle le problème
# de quota à la racine plutôt que de le contourner (rate-limiting/cache ne
# suffiraient pas face à 10 requêtes/jour). Le backend Gemini
# (``synthesize_prompt`` / ``synthesize_all_via_live`` ci-dessous) devient un
# repli de secours, utilisé seulement si aucun moteur TTS local n'est
# disponible dans l'environnement d'exécution.
# =====================================================================

LOCAL_TTS_BACKEND_NAME = "local-tts"


def _select_french_voice(engine):
    """Sélectionne une voix française si le moteur local en propose une.

    Best effort : si aucune voix française n'est détectable (attributs
    ``languages``/``name``/``id`` variables selon la plateforme et le
    pilote), la voix par défaut du moteur est conservée — Gemini Live côté
    reconnaissance vocale reste généralement robuste à un accent ou une
    voix de synthèse imparfaite.
    """
    try:
        voices = engine.getProperty("voices") or []
    except Exception:
        return None

    def _matches_french(haystack: str) -> bool:
        if not haystack:
            return False
        if "french" in haystack or "français" in haystack or "francais" in haystack:
            return True
        if any(name in haystack for name in ("hortense", "julie", "paul", "claude")):
            return True
        if "fr-fr" in haystack or "fr_fr" in haystack:
            return True
        # Code langue "fr" strict (jeton isolé) pour éviter un faux positif
        # sur un sous-mot anglais contenant "fr".
        return "fr" in re.split(r"[^a-z]+", haystack)

    for voice in voices:
        langs = []
        try:
            raw_langs = getattr(voice, "languages", None) or []
            for lang in raw_langs:
                if isinstance(lang, bytes):
                    lang = lang.decode("utf-8", "ignore")
                langs.append(str(lang).lower())
        except Exception:
            langs = []
        name = str(getattr(voice, "name", "") or "").lower()
        vid = str(getattr(voice, "id", "") or "").lower()
        if any(_matches_french(haystack) for haystack in (*langs, name, vid)):
            try:
                engine.setProperty("voice", voice.id)
                return voice
            except Exception:
                continue
    return None



def _wav_file_to_pcm16k(path: Path):
    """Lit un fichier .wav et renvoie du PCM 16 kHz mono s16le avec 0.5 s de silence final."""
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())
    if sampwidth != 2:
        raise RuntimeError(f"format audio local inattendu (sampwidth={sampwidth} octets, 2 attendu)")
    samples = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    if rate != SAMPLE_RATE and len(samples) > 1:
        count = max(1, int(len(samples) * SAMPLE_RATE / rate))
        samples = np.interp(
            np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples
        ).astype(np.int16)
    tail = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.int16)
    return (np.concatenate([samples, tail])).tobytes()


def synthesize_all_local(prompts: list[str]) -> dict[str, bytes] | None:
    """Synthétise tous les prompts via le moteur TTS LOCAL (pas de quota).

    Renvoie ``None`` (jamais ne lève) si aucun moteur local n'est
    disponible dans cet environnement, pour permettre un repli propre vers
    le backend Gemini en amont (``main()``).
    """
    import tempfile

    try:
        import pyttsx3
    except ImportError:
        safe_print(
            "[entrée] pyttsx3 non installé — synthèse locale indisponible "
            "(pip install pyttsx3 ; pywin32 en plus sur Windows)."
        )
        return None

    try:
        engine = pyttsx3.init()
    except Exception as exc:
        safe_print(
            f"[entrée] moteur TTS local indisponible dans cet environnement "
            f"({type(exc).__name__} : {exc})."
        )
        return None

    voice = _select_french_voice(engine)
    if voice is not None:
        safe_print(f"[entrée] voix locale sélectionnée : {getattr(voice, 'name', voice)!r}")
    else:
        safe_print(
            "[entrée] aucune voix française détectée parmi les voix locales "
            "disponibles — voix par défaut du système utilisée."
        )

    cache: dict[str, bytes] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="jarvis_validate_tts_") as td:
            tmp_dir = Path(td)
            for index, prompt in enumerate(prompts):
                cached = _cache_load(LOCAL_TTS_BACKEND_NAME, "local", prompt)
                if cached is not None:
                    cache[prompt] = cached
                    safe_print(f"   [synthèse locale] {prompt[:60]}… (depuis le cache disque)")
                    continue
                wav_path = tmp_dir / f"prompt_{index}.wav"
                engine.save_to_file(prompt, str(wav_path))
                engine.runAndWait()
                if not wav_path.exists() or wav_path.stat().st_size == 0:
                    raise RuntimeError(f"le moteur TTS local n'a produit aucun fichier pour : {prompt!r}")
                pcm = _wav_file_to_pcm16k(wav_path)
                cache[prompt] = pcm
                _cache_store(LOCAL_TTS_BACKEND_NAME, "local", prompt, pcm)
                safe_print(f"   [synthèse locale] {prompt[:60]}… ({len(pcm)} octets)")
    except Exception as exc:
        safe_print(
            f"[entrée] la synthèse locale a échoué en cours de route "
            f"({type(exc).__name__} : {exc}) — abandon de ce backend."
        )
        return None
    finally:
        try:
            engine.stop()
        except Exception:
            pass
    return cache


# =====================================================================
# Backend de SECOURS : Gemini ``generateContent`` TTS dédié, puis repli
# conversationnel Live si ``generateContent`` échoue (quota ou autre).
#
# ATTENTION QUOTA : ``gemini-2.5-flash-preview-tts`` via ``generateContent``
# est limité (constaté : 3 req/min, 10 req/jour sur le palier disponible).
# ``GEMINI_TTS_MIN_INTERVAL_SECONDS`` espace donc les appels, et le cache
# disque ci-dessus évite de reconsommer le quota à chaque ré-exécution.
# =====================================================================

GEMINI_TTS_MIN_INTERVAL_SECONDS = 22.0  # marge de sécurité sous 3 requêtes/minute
_last_gemini_tts_call = 0.0


async def synthesize_prompt(client, text: str, model: str) -> bytes:
    """Texte -> PCM 16 kHz mono s16le, via le modèle TTS Gemini.

    Repli de secours uniquement (cf. en-tête de section) : espacé dans le
    temps pour respecter la limite de 3 requêtes/minute, avec un essai
    supplémentaire après une courte attente en cas de 429
    (``RESOURCE_EXHAUSTED``) avant d'abandonner ce prompt.
    """
    global _last_gemini_tts_call
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

    async def _call_once() -> bytes:
        elapsed = time.monotonic() - _last_gemini_tts_call
        wait = GEMINI_TTS_MIN_INTERVAL_SECONDS - elapsed
        if wait > 0:
            await asyncio.sleep(wait)
        response = await asyncio.wait_for(
            client.aio.models.generate_content(model=model, contents=text, config=config),
            timeout=TTS_TIMEOUT,
        )
        return response

    try:
        response = await _call_once()
    except Exception as exc:
        code = getattr(exc, "code", None)
        if code == 429:
            safe_print(
                f"   [TTS Gemini] 429 RESOURCE_EXHAUSTED pour {text!r} — "
                f"nouvel essai dans {GEMINI_TTS_MIN_INTERVAL_SECONDS:.0f}s."
            )
            await asyncio.sleep(GEMINI_TTS_MIN_INTERVAL_SECONDS)
            response = await _call_once()
        else:
            raise
    finally:
        _last_gemini_tts_call = time.monotonic()

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



def _normalize_for_comparison(text: str) -> str:
    """Normalise un texte pour comparer prompt demandé vs. transcription obtenue.

    Minuscules, accents retirés, ponctuation retirée, espaces compressés —
    uniquement pour détecter une dérive SÉMANTIQUE du synthétiseur de repli
    (un modèle conversationnel qui répond au lieu de répéter), pas pour
    exiger une transcription phonétique parfaite.
    """
    import unicodedata

    decomposed = unicodedata.normalize("NFD", text)
    without_accents = "".join(c for c in decomposed if unicodedata.category(c) != "Mn")
    kept = "".join(c.lower() if c.isalnum() else " " for c in without_accents)
    return " ".join(kept.split())


async def _synthesize_one_via_live(client, model: str, prompt: str) -> tuple[bytes, str]:
    """Synthétise un seul prompt dans une session Live FRAÎCHE (sans historique).

    Root cause (audit v1.7.5 bis, S6 « FAIL » reproduit deux fois avec un
    texte entendu totalement différent du prompt demandé) : en réutilisant
    UNE SEULE session Live pour les 5 prompts à la suite, le modèle
    (conversationnel, pas un vrai TTS) dérive au bout de quelques tours et
    se met à RÉPONDRE au texte au lieu de le répéter mot pour mot (ex. :
    « Mon prénom est Simon. » -> audio généré disant « D'accord, Simon. » ;
    « Quel est mon prénom ? » -> audio généré disant « Même en pleine. »).
    Le scénario entendait alors un texte totalement différent de celui
    voulu, et l'echec de S6 observé n'avait RIEN à voir avec le contexte de
    session — c'était l'audio d'entrée lui-même qui était corrompu. Deux
    mesures correctives : (1) une session Live indépendante par prompt (pas
    d'historique de conversation pouvant dériver), (2) une transcription de
    la sortie (``output_audio_transcription``) comparée au prompt demandé,
    avec un nouvel essai si elle ne correspond pas.
    """
    import numpy as np
    from google.genai import types

    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        output_audio_transcription=types.AudioTranscriptionConfig(),
        system_instruction=(
            "Tu es un synthétiseur vocal, pas un assistant conversationnel. "
            "Tu ne réponds JAMAIS à ce qu'on t'envoie, tu ne commentes jamais, "
            "tu ne confirmes jamais : tu te contentes de LIRE À VOIX HAUTE, "
            "mot pour mot et sans aucun ajout, exactement le texte suivant, "
            "une seule fois : "
        ),
    )
    async with client.aio.live.connect(model=model, config=config) as session:
        await session.send_realtime_input(text=prompt)
        chunks = bytearray()
        transcript = ""
        async for message in session.receive():
            content = message.server_content
            if content and content.model_turn:
                for part in content.model_turn.parts:
                    if part.inline_data and part.inline_data.data:
                        chunks.extend(part.inline_data.data)
            if content and content.output_transcription and content.output_transcription.text:
                transcript += content.output_transcription.text
            if content and content.turn_complete:
                break
        if not chunks:
            raise RuntimeError(f"la session Live n'a pas synthétisé : {prompt!r}")
        return bytes(chunks), transcript


async def synthesize_all_via_live(client, model: str, prompts: list[str]) -> dict[str, bytes]:
    """Repli si generateContent/TTS n'est pas accessible (jeton éphémère).

    Chaque prompt est synthétisé dans sa PROPRE session Live (cf.
    ``_synthesize_one_via_live``) et la transcription de la sortie est
    vérifiée par rapport au texte demandé — avec un nouvel essai si le
    modèle a dérivé vers une réponse conversationnelle au lieu de répéter
    le texte (symptôme réel observé deux fois en validation réelle).
    """
    import numpy as np

    cache: dict[str, bytes] = {}
    for prompt in prompts:
        cached = _cache_load("gemini-live-tts", model, prompt)
        if cached is not None:
            cache[prompt] = cached
            safe_print(f"   [synthèse] {prompt[:60]}… (depuis le cache disque)")
            continue
        wanted = _normalize_for_comparison(prompt)
        pcm = b""
        last_transcript = ""
        for attempt in range(2):
            pcm, last_transcript = await _synthesize_one_via_live(client, model, prompt)
            got = _normalize_for_comparison(last_transcript)
            if got == wanted or (wanted and wanted in got) or (got and got in wanted):
                break
            safe_print(
                f"   [synthèse] ATTENTION : tentative {attempt + 1} pour {prompt!r} "
                f"a produit un texte différent ({last_transcript!r}) — nouvel essai."
            )
        else:
            raise RuntimeError(
                f"le synthétiseur de repli n'a jamais répété fidèlement {prompt!r} "
                f"(dernière tentative entendue : {last_transcript!r}) — audio d'entrée "
                "non fiable, verdicts en aval non significatifs"
            )
        samples = np.frombuffer(pcm, dtype=np.int16)
        count = int(len(samples) * SAMPLE_RATE / 24000)
        resampled = np.interp(
            np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples
        ).astype(np.int16)
        tail = np.zeros(int(SAMPLE_RATE * 0.5), dtype=np.int16)
        cache[prompt] = np.concatenate([resampled, tail]).tobytes()
        _cache_store("gemini-live-tts", model, prompt, cache[prompt])
        safe_print(
            f"   [synthèse] {prompt[:60]}… ({len(cache[prompt])} octets, "
            f"transcription vérifiée : {last_transcript[:60]!r})"
        )
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
            # v1.7.5 : un délai fixe de 2 s n'est pas fiable (le TTS de la
            # réponse précédente peut encore jouer, ou la passerelle micro
            # peut ne pas encore avoir rouvert la porte) -> cela produisait
            # un TimeoutError (S3 « NON TESTABLE ») sans rapport avec un bug
            # de contexte. On attend explicitement que Jarvis soit
            # redevenu réellement prêt à écouter avant d'envoyer le tour
            # suivant.
            self.lab.wait_until(
                lambda: self.lab.gemini.can_send() and not self.lab.gemini.speaking,
                20.0,
                "Jarvis prêt à écouter (avant 2e tour de S3)",
            )
            self.say(PROMPTS[1])
            gen1 = self.lab.gemini.session_generation
            self.lab.wait_until(
                lambda: self.lab.gemini.can_send() and not self.lab.gemini.speaking,
                20.0,
                "Jarvis prêt à écouter (avant 3e tour de S3)",
            )
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

    notes: list[str] = []
    prompt_cache: dict[str, bytes] = {}
    modes_used: set[str] = set()

    # 1) Backend PRIMAIRE : synthèse locale (pyttsx3), zéro quota Gemini.
    tts_backend = os.getenv("JARVIS_VALIDATE_TTS_BACKEND", "auto").strip().lower()
    if tts_backend in ("auto", "local"):
        local_result = synthesize_all_local(PROMPTS)
        if local_result:
            prompt_cache.update(local_result)
            modes_used.add("local-tts")

    missing = [p for p in PROMPTS if p not in prompt_cache]

    # 2) Repli de SECOURS uniquement pour les prompts manquants (backend
    #    Gemini local indisponible, ou JARVIS_VALIDATE_TTS_BACKEND=gemini
    #    forcé explicitement) : generateContent TTS d'abord (limité à 3
    #    req/min et 10 req/jour sur le palier disponible — ``synthesize_
    #    prompt`` espace donc ses appels), puis repli Live par prompt si
    #    generateContent échoue (quota ou autre erreur).
    if missing and tts_backend != "local":
        if not modes_used:
            safe_print(
                "[entrée] aucun moteur TTS local disponible — repli sur l'API "
                "Gemini pour la synthèse d'entrée (quota limité : 3 requêtes/min, "
                "10 requêtes/jour sur ce palier)."
            )
        import google.genai as genai

        client = genai.Client(api_key=api_key)
        for prompt in missing:
            cached = _cache_load("gemini-tts", tts_model, prompt)
            if cached is not None:
                prompt_cache[prompt] = cached
                modes_used.add("gemini-tts(cache)")
                continue
            try:
                pcm = await synthesize_prompt(client, prompt, tts_model)
                prompt_cache[prompt] = pcm
                _cache_store("gemini-tts", tts_model, prompt, pcm)
                modes_used.add("gemini-tts")
            except Exception as exc:
                safe_print(
                    f"[entrée] TTS generateContent indisponible pour {prompt!r} "
                    f"({type(exc).__name__} : {exc}) — repli Live pour ce prompt."
                )
                try:
                    one = await synthesize_all_via_live(client, model, [prompt])
                    prompt_cache.update(one)
                    modes_used.add("gemini-live-tts")
                except Exception as exc2:
                    safe_print(f"AUCUNE SYNTHÈSE POSSIBLE pour {prompt!r} : {type(exc2).__name__} — {exc2}")
                    print_verdicts({}, [f"synthèse audio impossible pour {prompt!r} : {exc2}"])
                    return 3

    still_missing = [p for p in PROMPTS if p not in prompt_cache]
    if still_missing:
        safe_print(f"AUCUNE SYNTHÈSE POSSIBLE pour : {still_missing!r}")
        print_verdicts({}, [f"synthèse audio impossible pour : {still_missing!r}"])
        return 3

    input_mode = "+".join(sorted(modes_used)) or "inconnu"
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
