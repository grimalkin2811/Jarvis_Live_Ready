"""Télécharge les ressources OpenWakeWord ('Hey Jarvis') pour le bundle.

Utilisé par le build Windows (local et CI) pour inclure les modèles
directement dans l'exécutable, afin que l'utilisateur final n'ait rien à
télécharger.

Les modèles sont placés dans ``resources/openwakeword/`` du dépôt (dossier
ignoré par Git, copié dans le bundle par ``packaging/jarvis.spec``).

``openwakeword.utils.download_models`` télécharge systématiquement les
variantes ``.tflite`` ET ``.onnx`` : les builds distribués utilisent les
``.onnx`` (voir ``src/wakeword.py``), d'où la vérification stricte ci-dessous.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.wakeword import REQUIRED_ONNX_MODELS  # noqa: E402

# Taille minimale de vraisemblance : un fichier plus petit est forcément un
# téléchargement tronqué ou une page d'erreur HTML.
MIN_MODEL_SIZE = 1024


def _verify(target: Path) -> list[str]:
    """Retourne la liste des problèmes détectés (vide = OK)."""
    problems: list[str] = []
    for name in REQUIRED_ONNX_MODELS:
        path = target / name
        if not path.is_file():
            problems.append(f"manquant : {name}")
            continue
        size = path.stat().st_size
        if size < MIN_MODEL_SIZE:
            problems.append(f"suspect ({size} octets) : {name}")
    return problems


def main() -> int:
    try:
        from openwakeword.utils import download_models
    except Exception as exc:
        print(f"[download_models] openwakeword indisponible : {exc}")
        return 1

    target = ROOT / "resources" / "openwakeword"
    target.mkdir(parents=True, exist_ok=True)

    models = ["hey_jarvis"]
    print(f"[download_models] Téléchargement des modèles vers {target} ...")
    try:
        download_models(models, target_directory=str(target))
    except Exception as exc:
        print(f"[download_models] Échec du téléchargement : {exc}")

    files = sorted(target.iterdir())
    for f in files:
        if f.is_file():
            print(f"  - {f.name} ({f.stat().st_size} octets)")

    problems = _verify(target)
    if problems:
        print("[download_models] Modèles ONNX requis invalides :")
        for problem in problems:
            print(f"  ! {problem}")
        print(
            "[download_models] Le build doit échouer plutôt que publier un "
            "bundle sans wake word fonctionnel."
        )
        return 1

    print("[download_models] Terminé (modèles ONNX vérifiés).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
