"""Outil de maintenance des fixtures TTS (scripts/validation/tts_fixtures/).

Les validateurs réels (validate_real_gemini.py, validate_reconnect_v174_
real.py) envoient des phrases françaises EXACTES enregistrées une fois pour
toutes : zéro requête TTS au runtime, donc zéro quota Gemini consommé et des
entrées identiques à chaque exécution (R1, R5).

Régénérer ou ajouter une fixture
--------------------------------
1. Produire la voix HORS de ce dépôt (aucune dépendance réseau ici) :
   * voie recommandée : générateur TTS quelconque produisant un WAV/MP3
     lisible, humainement vérifiable (écoute !) ;
   * la voix d'origine des fixtures actuelles vient du studio vocal Arena
     (voix masculine française « voice-00 »), exportée puis convertie.
2. Convertir + intégrer :

       python scripts/validation/make_tts_fixtures.py \
           --in brut.wav --texte "Bonjour Jarvis, tu m'entends bien ?" \
           --out bonjour_jarvis_tu_m_entends_bien

   Le script :
   * convertit vers PCM 16 kHz mono s16le (rééchantillonnage linéaire numpy,
     mono, écrêtage protégé) — le format exact attendu par le protocole Live
     du projet (16 kHz entrée micro) ;
   * refuse tout remplacement silencieux d'une fixture existante (option
     --force explicite requise) ;
   * recalcule le sha256 et met à jour manifest.json (source de vérité du
     loader ``tts_fixtures.py``) ;
   * applique les mêmes bornes de durée que le loader — une fixture hors
     bornes est refusée à l'intégration, pas au milieu d'un run.

3. Vérifier l'ensemble :

       python scripts/validation/make_tts_fixtures.py --check

4. ÉCOUTER le fichier résultant (étape humaine obligatoire, non automatisable) :

       python scripts/validation/make_tts_fixtures.py --play manifest.json:...

   ou simplement tout lecteur audio ; la première validation réelle doit
   aussi confirmer la transcription (ligne « [entendu] » des validateurs).
"""
from __future__ import annotations

import argparse
import json
import sys
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
FIXTURES_DIR = REPO / "scripts" / "validation" / "tts_fixtures"
MANIFEST = FIXTURES_DIR / "manifest.json"
SAMPLE_RATE = 16000
MIN_SECONDS_PER_CHAR = 0.03
MAX_SECONDS_PER_CHAR = 0.25
MAX_EXTRA_SECONDS = 1.5


def _load_pcm_16k_mono(path: Path) -> bytes:
    """Lit un WAV quelconque et le convertit en PCM 16 kHz mono s16le."""
    import numpy as np

    with wave.open(str(path), "rb") as wf:
        rate, channels, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if width != 2:
        raise SystemExit(f"{path} : largeur d'échantillon {width*8} bits non gérée (16 bits attendu)")
    samples = np.frombuffer(raw, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    if rate != SAMPLE_RATE:
        count = int(round(len(samples) * SAMPLE_RATE / rate))
        if count == 0:
            raise SystemExit(f"{path} : audio vide")
        samples = np.interp(
            np.linspace(0.0, len(samples) - 1.0, count),
            np.arange(len(samples), dtype=np.float64),
            samples.astype(np.float64),
        )
        peak = float(np.abs(samples).max()) if len(samples) else 0.0
        if peak > 32767.0:  # l'interpolation peut dépasser légèrement
            samples = samples * (32767.0 / peak)
        samples = samples.astype(np.int16)
    return samples.tobytes()


def _durations_ok(text: str, seconds: float) -> str | None:
    minimum = max(0.4, MIN_SECONDS_PER_CHAR * len(text))
    maximum = MAX_SECONDS_PER_CHAR * len(text) + MAX_EXTRA_SECONDS
    if not (minimum <= seconds <= maximum):
        return (
            f"durée {seconds:.2f}s hors bornes [{minimum:.2f}, {maximum:.2f}] "
            f"pour {len(text)} caractères"
        )
    return None


def cmd_add(args: argparse.Namespace) -> int:
    import hashlib

    src = Path(args.in_path)
    if not src.exists():
        raise SystemExit(f"introuvable : {src}")
    text = " ".join(args.texte.split())
    if not text:
        raise SystemExit("--texte est obligatoire (le manifeste doit refléter la phrase EXACTe)")
    name = args.out or src.stem.lower().replace(" ", "_").replace("'", "_")

    pcm = _load_pcm_16k_mono(src)
    seconds = len(pcm) / 2 / SAMPLE_RATE
    problem = _durations_ok(text, seconds)
    if problem and not args.force_duration:
        raise SystemExit(f"REFUSÉ : {problem} — vérifiez l'audio, ou --force-duration")

    dest = FIXTURES_DIR / f"{name}.wav"
    if dest.exists() and not args.force:
        raise SystemExit(
            f"REFUSÉ : {dest.name} existe déjà (remplacer une fixture change les entrées "
            "des validations réelles — relancez avec --force en le sachant)"
        )
    with wave.open(str(dest), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm)

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8")) if MANIFEST.exists() else {"fixtures": []}
    entries = [f for f in manifest.get("fixtures", []) if f["file"] != dest.name]
    entries.append(
        {
            "file": dest.name,
            "text": text,
            "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
            "sample_rate": SAMPLE_RATE,
            "channels": 1,
            "sample_width": 2,
            "duration_seconds": round(seconds, 3),
        }
    )
    entries.sort(key=lambda f: f["file"])
    manifest["fixtures"] = entries
    manifest.setdefault(
        "format",
        {"encoding": "PCM", "sample_rate": SAMPLE_RATE, "channels": 1, "sample_width": 2},
    )
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"OK : {dest.name} ({seconds:.2f}s) — manifeste mis à jour.")
    print("ÉTAPE HUMAINE : écoutez le fichier avant toute validation réelle.")
    return 0


def cmd_check(_args: argparse.Namespace) -> int:
    sys.path.insert(0, str(REPO))
    from scripts.validation import tts_fixtures as fx

    prompts = [f["text"] for f in json.loads(MANIFEST.read_text(encoding="utf-8"))["fixtures"]]
    cache, problems = fx.load_fixtures(prompts)
    for problem in problems:
        print(f"PROBLÈME : {problem}")
    print(f"{len(cache)}/{len(prompts)} fixtures valides.")
    return 1 if problems else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="convertir + intégrer une fixture")
    add.add_argument("--in", dest="in_path", required=True, help="WAV source (16/24/48 kHz…)")
    add.add_argument("--texte", required=True, help="phrase EXACTe prononcée")
    add.add_argument("--out", help="nom de fixture (sans .wav)")
    add.add_argument("--force", action="store_true", help="remplacer une fixture existante")
    add.add_argument("--force-duration", action="store_true", help="accepter une durée hors bornes")
    add.set_defaults(func=cmd_add)

    check = sub.add_parser("check", help="revérifier toutes les fixtures via le loader strict")
    check.set_defaults(func=cmd_check)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
