"""Banc de mesure Python du module God's Eye View.

CE QUE CE SCRIPT MESURE (honnêtement, sans navigateur ni GPU) :
  1. temps d'import du chemin critique (src.tools, src.gods_eye) ;
  2. débit de dispatch du contrôleur avec un transport en mémoire ;
  3. coût d'une bascule enable/disable idempotente (chemin « déjà dans l'état ») ;
  4. taille du build web (dist/) si présent.

CE QUE CE SCRIPT NE MESURE PAS : FPS, frame-time, GPU, heap navigateur,
démarrage Cesium, latence réseau adsb.lol. Ces mesures exigent le PC cible
(Pentium Gold G5400 / UHD 610) et Chrome — voir docs/gods-eye-view-integration.md
§11 pour le protocole. Aucune valeur non mesurée n'est publiée.

Usage : python scripts/benchmark_gods_eye.py [--json]
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.gods_eye import protocol  # noqa: E402
from src.gods_eye.controller import GodsEyeController  # noqa: E402
from tests.test_gods_eye_controller import FakeTransport  # noqa: E402


def _time_it(label, fn, iterations):
    start = time.perf_counter()
    for _ in range(iterations):
        fn()
    elapsed = time.perf_counter() - start
    return {"label": label, "iterations": iterations, "total_s": round(elapsed, 4),
            "per_op_us": round(elapsed / iterations * 1e6, 2)}


def _dist_stats():
    dist = REPO_ROOT / "web" / "gods_eye_view" / "dist"
    if not dist.is_dir():
        return {"built": False}
    total = sum(f.stat().st_size for f in dist.rglob("*") if f.is_file())
    js = sum(f.stat().st_size for f in dist.rglob("*.js"))
    index = dist / "index.html"
    return {
        "built": True,
        "total_mb": round(total / 1e6, 2),
        "js_mb": round(js / 1e6, 2),
        "index_kb": round(index.stat().st_size / 1e3, 1) if index.exists() else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="sortie JSON uniquement")
    args = parser.parse_args()

    results = {"python": sys.version.split()[0], "measurements": []}

    # 1. Imports
    for module in ("src.gods_eye", "src.gods_eye.protocol", "src.gods_eye.controller", "src.tools"):
        start = time.perf_counter()
        importlib.import_module(module)
        results["measurements"].append(
            {"label": f"import {module}", "total_s": round(time.perf_counter() - start, 4)}
        )

    # 2. Dispatch get_state (transport synchrone en mémoire)
    controller = GodsEyeController()
    transport = FakeTransport()
    controller.attach_transport(transport)
    controller.notify_ready()

    results["measurements"].append(
        _time_it("dispatch world_get_state (sync)", lambda: controller.dispatch(protocol.ACTION_GET_STATE), 2000)
    )
    results["measurements"].append(
        _time_it("enable_layer idempotent (no-op)", lambda: controller.enable_layer("flights"), 2000)
    )
    # Note : enable_layer no-op ne renvoie rien au globe — c'est le point P0.

    # Alternance réelle enable→disable (2 dispatches à chaque itération)
    controller.disable_layer("flights")
    def churn():
        controller.enable_layer("flights")
        controller.disable_layer("flights")
    results["measurements"].append(_time_it("cycle enable+disable complet", churn, 500))

    stats = controller.stats()
    results["controller_stats"] = stats
    results["dist"] = _dist_stats()

    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0

    print("== God's Eye View — banc Python (headless) ==")
    for measurement in results["measurements"]:
        if "per_op_us" in measurement:
            print(f"  {measurement['label']:<42} {measurement['per_op_us']:>8.2f} µs/op  (n={measurement['iterations']})")
        else:
            print(f"  {measurement['label']:<42} {measurement['total_s']:>8.4f} s")
    print("  dist:", results["dist"])
    print("  stats contrôleur:", {k: v for k, v in stats.items() if k != 'lifecycle'}, "| lifecycle:", stats["lifecycle"])
    print("\nRappel : aucune mesure FPS/GPU n'est réalisée ici — protocole cible : docs §11.3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
