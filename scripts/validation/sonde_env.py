"""Sonde d'environnement Python du protocole de validation (lecture seule).

Exécutée par l'orchestrateur avec CHAQUE interpréteur à inspecter (venv de
validation, ``.venv`` de développement, venv jetable PySide6 6.12.0) :

    python sonde_env.py --json sortie.json            # versions et imports
    python sonde_env.py --fuite-pyside --json f.json  # dérive de None (PySide6)

N'installe, ne désinstalle et ne met à jour RIEN. La mesure de fuite tourne
dans un processus dédié : si PySide6 fait planter l'interpréteur
(« Fatal Python error: none_dealloc »), seul ce processus s'arrête et le code
de sortie sert de preuve.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import struct
import sys
import time

PAQUETS = (
    "PySide6", "PySide6-Essentials", "PySide6-Addons", "shiboken6", "google-genai", "numpy",
    "sounddevice", "pyttsx3", "pywin32", "pytest", "ruff", "pyinstaller", "onnxruntime",
    "openwakeword", "python-dotenv", "psutil", "requests", "websockets",
)


def versions_paquets() -> dict:
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover - Python < 3.8
        return {}
    out = {}
    for name in PAQUETS:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
        except Exception as exc:  # pragma: no cover - métadonnées corrompues
            out[name] = f"illisible ({type(exc).__name__})"
    return out


def imports() -> dict:
    out: dict = {}
    try:
        import PySide6
        from PySide6 import QtCore

        out["PySide6.__version__"] = getattr(PySide6, "__version__", None)
        out["Qt (qVersion)"] = QtCore.qVersion()
        out["Qt compilé"] = getattr(QtCore, "__version__", None) or QtCore.QLibraryInfo.version().toString()
    except Exception as exc:
        out["PySide6"] = f"import impossible : {type(exc).__name__}: {exc}"
    try:
        import shiboken6

        out["shiboken6.__version__"] = getattr(shiboken6, "__version__", None)
    except Exception as exc:
        out["shiboken6"] = f"import impossible : {type(exc).__name__}"
    try:
        import sounddevice

        out["PortAudio"] = sounddevice.get_portaudio_version()[1]
    except Exception as exc:
        out["sounddevice"] = f"import impossible : {type(exc).__name__}: {exc}"[:200]
    try:
        from google import genai

        out["google.genai"] = getattr(genai, "__version__", None) or "importé"
    except Exception as exc:
        out["google.genai"] = f"import impossible : {type(exc).__name__}"
    return out


def mesure_fuite(appels: int) -> dict:
    """Même mesure que tests/test_pyside_refcount.py, sur ``appels`` itérations."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    result = {"python": sys.version.split()[0], "appels": appels}
    if sys.version_info >= (3, 12):
        result["note"] = "None est immortel à partir de Python 3.12 : la fuite ne peut pas faire planter."
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QImage, QPainter

    import PySide6

    result["PySide6"] = getattr(PySide6, "__version__", None)
    point = QPointF(1.0, 2.0)
    rect = QRectF(0.0, 0.0, 1.0, 1.0)
    image = QImage(4, 4, QImage.Format_ARGB32)
    painter = QPainter(image)
    try:
        before = sys.getrefcount(None)
        start = time.perf_counter()
        for index in range(appels):
            point.setX(float(index))
            rect.setWidth(2.0)
            painter.save()
            painter.restore()
        result["duree_s"] = round(time.perf_counter() - start, 3)
        result["derive_none"] = sys.getrefcount(None) - before
    finally:
        painter.end()
    result["fuite"] = result["derive_none"] <= -50
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", required=True)
    parser.add_argument("--fuite-pyside", action="store_true")
    parser.add_argument("--appels", type=int, default=500)
    args = parser.parse_args(argv)
    if args.fuite_pyside:
        data = mesure_fuite(args.appels)
    else:
        data = {
            "python": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "bits": struct.calcsize("P") * 8,
            "executable": sys.executable,
            "prefix": sys.prefix,
            "base_prefix": getattr(sys, "base_prefix", sys.prefix),
            "plateforme": platform.platform(),
            "paquets": versions_paquets(),
            "imports": imports(),
        }
    with open(args.json, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
