"""Fixtures audio d'ENTRÉE des validations Gemini réelles (zéro quota TTS).

Pourquoi
--------
Les validateurs réels (``scripts/validate_real_gemini.py``,
``scripts/validate_reconnect_v174_real.py``) envoient les questions sous
forme AUDIO. La synthèse à l'exécution consomme le quota TTS Gemini
(constaté : 10 requêtes/jour) — épuisé, elle rendait tous les scénarios
« NON TESTABLE », ou pire, produisait via le repli conversationnel un
audio NE CORRESPONDANT PAS à la question (constat réel : « Sunday,
October 25, 2026 at 8:03 PM UTC » envoyé pour « Mon prénom est Simon. »).

Ces fixtures WAV versionnées (PCM 16 kHz mono s16le, phrases FRANÇAISES
EXACTES des scénarios) suppriment la dépendance : aucun appel TTS au
runtime, entrées identiques à chaque exécution (comparaisons fiables).

Garanties (ce module ne doit JAMAIS renvoyer un audio douteux)
--------------------------------------------------------------
* correspondance par le TEXTE EXACT de la phrase (manifest.json) — aucun
  fuzzy matching : un prompt sans fixture est signalé, pas approximé ;
* SHA-256 de chaque fichier vérifié contre le manifeste (toute corruption
  ou substitution est détectée AVANT l'ouverture d'une session Gemini) ;
* format vérifié (16 kHz, mono, s16le) et durée dans des bornes
  plausibles pour la phrase (une fixture tronquée/vide ne passe pas) ;
* aucune exception avalée : les problèmes sont retournés à l'appelant,
  qui décide (échec explicite ou repli documenté) — jamais un silence.

Régénération : ``scripts/validation/make_tts_fixtures.py`` (conversion +
intégration d'un enregistrement quelconque) — cf. ``tts_fixtures/README.md``
(origine des voix, étape d'écoute humaine obligatoire).
"""

from __future__ import annotations

import hashlib
import json
import wave
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent / "tts_fixtures"
MANIFEST_PATH = FIXTURES_DIR / "manifest.json"

#: Bornes de durée plausibles (s) par caractère de la phrase — volontairement
#: larges : elles ne valident pas la prononciation (contrôle humain au premier
#: run + transcription d'entrée affichée par le validateur à chaque tour),
#: elles piègent un fichier tronqué, vide, corrompu ou remplacé par un autre.
MIN_SECONDS_PER_CHAR = 0.03
MAX_SECONDS_PER_CHAR = 0.25
MAX_EXTRA_SECONDS = 1.5


class FixtureError(RuntimeError):
    """Fixture manquante, corrompue ou incompatible — jamais silencieux."""


def _read_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        raise FixtureError(f"manifeste absent : {MANIFEST_PATH}")
    try:
        manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        raise FixtureError(f"manifeste illisible ({exc}) : {MANIFEST_PATH}") from exc
    if not isinstance(manifest.get("fixtures"), list) or not manifest["fixtures"]:
        raise FixtureError(f"manifeste sans fixtures : {MANIFEST_PATH}")
    return manifest


def _verify_entry(entry: dict) -> bytes:
    """Vérifie UNE fixture et renvoie son PCM 16 kHz mono s16le."""
    name = entry.get("file") or ""
    text = entry.get("text") or ""
    path = FIXTURES_DIR / name
    if not path.exists():
        raise FixtureError(f"fixture absente : {path}")
    data = path.read_bytes()
    expected = entry.get("sha256") or ""
    actual = hashlib.sha256(data).hexdigest()
    if expected and actual != expected:
        raise FixtureError(
            f"fixture corrompue (sha256 {actual[:12]} != manifeste {expected[:12]}) : {path}"
        )
    try:
        with wave.open(str(path), "rb") as wf:
            rate, channels, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
            frames_bytes = wf.readframes(wf.getnframes())
    except Exception as exc:
        raise FixtureError(f"fixture illisible en WAV PCM ({exc}) : {path}") from exc
    if (rate, channels, width) != (16000, 1, 2):
        raise FixtureError(
            f"format inattendu (rate={rate}, channels={channels}, width={width}) : {path}"
        )
    duration = (len(frames_bytes) // (width * channels)) / rate if rate else 0.0
    minimum = max(0.4, MIN_SECONDS_PER_CHAR * len(text))
    maximum = MAX_SECONDS_PER_CHAR * len(text) + MAX_EXTRA_SECONDS
    if not (minimum <= duration <= maximum):
        raise FixtureError(
            f"durée implausible ({duration:.2f}s hors [{minimum:.2f}, {maximum:.2f}] "
            f"pour {len(text)} caractères) : {path}"
        )
    return frames_bytes


def load_fixtures(prompts: list[str]) -> tuple[dict[str, bytes], list[str]]:
    """Charge les fixtures des prompts demandés.

    Retourne ``(cache, problèmes)`` : ``cache`` associe la phrase EXACTE au
    PCM 16 kHz mono s16le ; ``problèmes`` liste les prompts sans fixture
    valide (motif explicite). L'appelant décide : repli documenté ou échec.
    Aucune fixture douteuse n'est jamais retournée dans ``cache``.
    """
    manifest = _read_manifest()
    by_text: dict[str, dict] = {}
    for entry in manifest["fixtures"]:
        text = str(entry.get("text") or "")
        if text in by_text:
            raise FixtureError(f"phrase dupliquée dans le manifeste : {text!r}")
        by_text[text] = entry

    cache: dict[str, bytes] = {}
    problems: list[str] = []
    for prompt in prompts:
        entry = by_text.get(prompt)
        if entry is None:
            problems.append(
                f"aucune fixture pour la phrase exacte : {prompt!r} "
                f"(le manifeste n'a pas été régénéré ?)"
            )
            continue
        try:
            cache[prompt] = _verify_entry(entry)
        except FixtureError as exc:
            problems.append(str(exc))
    return cache, problems


def fixture_files() -> list[Path]:
    """Les fichiers WAV versionnés (pour les tests et la documentation)."""
    manifest = _read_manifest()
    return [FIXTURES_DIR / str(entry.get("file")) for entry in manifest["fixtures"]]
