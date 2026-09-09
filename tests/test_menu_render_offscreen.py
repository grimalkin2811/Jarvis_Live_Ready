"""Regression test: render the orb offscreen with every menu sector open.

Reproduces the setup that crashed in paintEvent -> _draw_organic_menu_v2 ->
_draw_menu_node (NameError: sector_index) and makes sure every label layout
branch paints without error for all 5 sectors.
"""
import math
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import _ci_diag  # noqa: E402,F401  (diagnostic CI temporaire)

from PySide6.QtWidgets import QApplication  # noqa: E402

import UI.jarvis_menu as jm  # noqa: E402


def main() -> int:
    app = QApplication(sys.argv)
    w = jm.MorphingOrbWidget()
    w.resize(1280, 800)
    w.show()

    failures = []
    for sector in range(len(jm.MENU_SPECS)):
        spec = jm.MENU_SPECS[sector]
        w._menu_sector = sector
        w._menu_candidate = sector
        w._menu_last_stable_time = w.time
        w._menu_reveal = spec.reveal_scale
        w._menu_alpha = 1.0
        w._menu_nodes.clear()
        # Let nodes sync, spread out and reach full visibility.
        for _ in range(90):
            w.time += 1.0 / 60.0
            w._update_menu_nodes()
        for node in w._menu_nodes:
            node.visible_amount = 1.0
        try:
            pix = w.grab()  # full paintEvent pass
            assert pix is not None and not pix.isNull()
            sx, sy = w._sector_vector(sector)
            branch = (
                f"grid {'down' if sy > 0 else 'up'}"
                if w._menu_layout_mode == "grid"
                else ("line left" if sx < 0 else "line right")
            )
            print(
                f"sector {sector} ({spec.name:<10}) layout={w._menu_layout_mode:<5} "
                f"vector=({sx:+.2f},{sy:+.2f}) branch={branch:<10} "
                f"nodes={len(w._menu_nodes)} OK"
            )
        except Exception as exc:  # noqa: BLE001
            failures.append((sector, spec.name, repr(exc)))

    # Closed state must paint too.
    w._menu_sector = -1
    w._menu_alpha = 0.0
    for _ in range(30):
        w.time += 1.0 / 60.0
        w._update_menu_nodes()
    try:
        w.grab()
        print("closed state OK")
    except Exception as exc:  # noqa: BLE001
        failures.append((-1, "closed", repr(exc)))

    if failures:
        for sector, name, err in failures:
            print(f"FAIL sector={sector} ({name}): {err}")
        return 1
    print("ALL SECTORS RENDER OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
