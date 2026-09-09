"""Télécharge les ressources OpenWakeWord ('Hey Jarvis') pour le bundle.

Utilisé par le build Windows (local et CI) pour inclure le modèle directement
dans l'exécutable, afin que l'utilisateur final n'ait rien à télécharger.

Les modèles sont placés dans ``resources/openwakeword/`` du dépôt (ignorés par
Git ? non, ils sont petits et inclus dans les releases ; ``resources/`` n'est
pas commité).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


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
        # L'application peut encore télécharger au premier lancement ; le build
        # doit néanmoins échouer pour ne pas publier un bundle sans modèle en
        # silencieux. On force l'échec seulement si aucun fichier n'a été placé.
        files = list(target.glob("hey_jarvis*")) + list(target.glob("embedding_model*"))
        if not files:
            print("[download_models] Aucun modèle téléchargé : échec.")
            return 1

    files = list(target.iterdir())
    for f in files:
        print(f"  - {f.name} ({f.stat().st_size} octets)")
    print("[download_models] Terminé.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
