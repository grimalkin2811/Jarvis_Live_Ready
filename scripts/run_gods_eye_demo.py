"""Démo manuelle du module God's Eye View (hors voix).

Ouvre la fenêtre du globe, active la couche vols, vole vers la région demandée,
puis laisse la fenêtre ouverte jusqu'à sa fermeture (Échap / FERMER / X).

Prérequis : web/gods_eye_view construit (npm install && npm run build) et
PySide6 installé avec QtWebEngineWidgets (absent de l'installation par défaut
de ce dépôt — voir docs/gods-eye-view-integration.md §12 pour le packaging).

Exemple : python scripts/run_gods_eye_demo.py --region paris
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main() -> int:
    parser = argparse.ArgumentParser(description="Démo God's Eye View (module Jarvis)")
    parser.add_argument("--region", default="paris", help="Région de world_focus_region (défaut: paris)")
    parser.add_argument("--layer", default="flights", help="Couche à activer (défaut: flights)")
    parser.add_argument("--timeout", type=float, default=8.0, help="Attente max du chargement (s)")
    args = parser.parse_args()

    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:
        print(f"[démo] PySide6 indisponible : {exc}")
        return 2

    app = QApplication(sys.argv)
    app.setApplicationName("Jarvis — God's Eye View (démo)")

    from src.gods_eye import get_default_controller
    from src.gods_eye.widget import request_open

    controller = get_default_controller()
    print("[démo] Ouverture du module…")
    if not request_open(timeout=args.timeout):
        print("[démo] ÉCHEC — construit ? (cd web/gods_eye_view && npm install && npm run build)")
        print(f"[démo]   QtWebEngine installé ? État contrôleur : {controller.lifecycle}")
        return 2

    print(f"[démo] Module en cours de chargement… (lifecycle={controller.lifecycle})")
    enable = controller.enable_layer(args.layer)
    print(f"[démo] enable_layer({args.layer}) → success={enable.get('success')} changed={enable.get('changed')} erreur={enable.get('error')}")

    focus = controller.focus_region(args.region)
    print(f"[démo] focus_region({args.region}) → success={focus.get('success')} caméra={focus.get('camera')} erreur={focus.get('error')}")

    state = controller.get_state()
    print(f"[démo] état (source={state.get('source')}) : {state.get('state')}")
    print("[démo] Fenêtre ouverte — Échap/FERMER/X pour quitter.")
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
