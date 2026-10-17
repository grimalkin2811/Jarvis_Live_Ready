"""Sonde audio du protocole de validation.

Par défaut : INVENTAIRE SEUL, sans aucune capture ni lecture. Liste les
périphériques (noms masqués : ils peuvent contenir un prénom, par exemple un
casque Bluetooth), les périphériques par défaut et vérifie que les formats
utilisés par Jarvis sont acceptés (entrée 16 kHz mono int16, sortie 24 kHz
mono int16) avec ``check_input_settings`` / ``check_output_settings`` — ces
vérifications n'ouvrent aucun flux.

Tests guidés (uniquement à la demande explicite de la fenêtre finale, après
avertissement affiché à l'utilisateur) :

* ``--test-haut-parleur`` : joue une tonalité de 1 s (440 Hz, volume modéré) ;
* ``--test-micro 3`` : capture 3 s EN MÉMOIRE, calcule le niveau (RMS, crête)
  puis efface le tampon. Rien n'est écrit sur disque, rien n'est envoyé.
"""

from __future__ import annotations

import argparse
import json
import math
import sys


def _masquer(nom: str) -> str:
    """Garde le type générique du périphérique, jamais un nom personnel."""
    lowered = (nom or "").lower()
    for mot, libelle in (
        ("micro", "microphone"), ("mic", "microphone"), ("headset", "casque"), ("casque", "casque"),
        ("ecouteur", "écouteurs"), ("écouteur", "écouteurs"), ("speaker", "haut-parleurs"),
        ("haut-parleur", "haut-parleurs"), ("hdmi", "sortie HDMI"), ("realtek", "carte son Realtek"),
        ("usb", "périphérique USB"), ("bluetooth", "Bluetooth"), ("hands-free", "mains libres"),
        ("mappeur", "mappeur de sons Microsoft"), ("mapper", "mappeur de sons Microsoft"),
        ("stereo mix", "mixage stéréo"), ("mixage", "mixage stéréo"),
    ):
        if mot in lowered:
            return libelle
    return "périphérique audio"


def inventaire() -> dict:
    import sounddevice as sd

    devices = sd.query_devices()
    hostapis = sd.query_hostapis()
    default_in, default_out = sd.default.device
    out = {
        "portaudio": sd.get_portaudio_version()[1],
        "nombre": len(devices),
        "entrees": sum(1 for d in devices if d["max_input_channels"] > 0),
        "sorties": sum(1 for d in devices if d["max_output_channels"] > 0),
        "api": sorted({hostapis[d["hostapi"]]["name"] for d in devices}),
    }

    def decrire(index, kind: str) -> dict | None:
        if index is None or index < 0 or index >= len(devices):
            return None
        d = devices[index]
        info = {
            "index": int(index),
            "type": _masquer(d["name"]),
            "api": hostapis[d["hostapi"]]["name"],
            "canaux": int(d["max_input_channels"] if kind == "entree" else d["max_output_channels"]),
            "frequence_defaut": int(d["default_samplerate"]),
        }
        try:
            if kind == "entree":
                sd.check_input_settings(device=index, channels=1, dtype="int16", samplerate=16000)
            else:
                sd.check_output_settings(device=index, channels=1, dtype="int16", samplerate=24000)
            info["format_jarvis_accepte"] = True
        except Exception as exc:
            info["format_jarvis_accepte"] = False
            info["erreur_format"] = str(exc)[:200]
        return info

    out["entree_defaut"] = decrire(default_in, "entree")
    out["sortie_defaut"] = decrire(default_out, "sortie")
    out["entree_utilisable"] = bool(out["entree_defaut"] and out["entree_defaut"]["format_jarvis_accepte"])
    out["sortie_utilisable"] = bool(out["sortie_defaut"] and out["sortie_defaut"]["format_jarvis_accepte"])
    return out


#: Formats RÉELS de Jarvis (``src/audio.py``, ``AudioIO``) : la sortie et
#: l'entrée sont testées avec les mêmes flux bruts, fréquences, canaux, format
#: d'échantillon et tailles de bloc que la production (correctif PR #50).
SORTIE_HZ, SORTIE_BLOC = 24000, 1024
ENTREE_HZ, ENTREE_BLOC = 16000, 1280


def test_haut_parleur() -> dict:
    import numpy as np
    import sounddevice as sd

    t = np.arange(int(SORTIE_HZ * 1.0)) / SORTIE_HZ
    envelope = np.minimum(1.0, np.minimum(t / 0.05, (1.0 - t) / 0.05))
    tone = (0.25 * np.sin(2 * math.pi * 440 * t) * envelope * 32767).astype("int16").tobytes()
    with sd.RawOutputStream(samplerate=SORTIE_HZ, channels=1, dtype="int16", blocksize=SORTIE_BLOC) as stream:
        stream.write(tone)
    return {"joue": True, "duree_s": 1.0, "frequence_hz": 440,
            "format": f"{SORTIE_HZ} Hz mono int16, blocs de {SORTIE_BLOC} (format de sortie de Jarvis)"}


def test_micro(secondes: float) -> dict:
    import numpy as np
    import sounddevice as sd

    blocs = max(1, int(secondes * ENTREE_HZ / ENTREE_BLOC))
    rms_blocs: list[float] = []
    peak = 0.0
    with sd.RawInputStream(samplerate=ENTREE_HZ, channels=1, dtype="int16", blocksize=ENTREE_BLOC) as stream:
        for _ in range(blocs):
            data, _overflow = stream.read(ENTREE_BLOC)
            samples = np.frombuffer(bytes(data), dtype=np.int16).astype("float64")
            if samples.size:
                rms_blocs.append(float(np.sqrt(np.mean(samples ** 2))))
                peak = max(peak, float(np.max(np.abs(samples))))
            # Chaque bloc est jeté aussitôt mesuré : aucun enregistrement conservé.
            del samples, data
    rms = float(np.sqrt(np.mean(np.square(rms_blocs)))) if rms_blocs else 0.0
    blocs_voix = sum(1 for value in rms_blocs if value > 150.0)
    return {
        "duree_s": secondes,
        "rms": round(rms, 1),
        "crete": round(peak, 1),
        "rms_dbfs": round(20 * math.log10(max(rms, 1.0) / 32768.0), 1),
        "blocs_avec_voix": blocs_voix,
        "signal_detecte": rms > 150.0,
        "format": f"{ENTREE_HZ} Hz mono int16, blocs de {ENTREE_BLOC} (format d'entrée de Jarvis)",
        "conserve": False,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", required=True)
    parser.add_argument("--test-haut-parleur", action="store_true")
    parser.add_argument("--test-micro", type=float, default=0.0)
    args = parser.parse_args(argv)
    try:
        if args.test_haut_parleur:
            data = test_haut_parleur()
        elif args.test_micro > 0:
            data = test_micro(min(args.test_micro, 5.0))
        else:
            data = inventaire()
        code = 0
    except Exception as exc:
        data = {"erreur": f"{type(exc).__name__}: {exc}"[:400]}
        code = 1
    with open(args.json, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return code


if __name__ == "__main__":
    sys.exit(main())
