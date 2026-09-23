"""Diagnostic : géométrie réelle des menus radiaux (chevauchements).

Ouvre chaque menu hors écran, stabilise les positions via le solveur de
layout (source unique ``_solve_layout_spacings`` / ``_layout_targets``) et
demande le rapport officiel ``_menu_layout_overlaps()`` — le même que les
tests de non-régression (``tests/test_menu_layout.py``).

Sortie : 0 chevauchement → code 0 ; sinon code 1.

Usage : QT_QPA_PLATFORM=offscreen python scripts/diag_menu_overlap.py
       (ou LD_LIBRARY_PATH=… sur Linux sans libGL)
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from PySide6.QtWidgets import QApplication  # noqa: E402

import UI.jarvis_menu as jm  # noqa: E402


def _settle(widget, frames: int = 150) -> None:
    for _ in range(frames):
        widget.time += 1.0 / 60.0
        widget._update_menu_nodes()


def main() -> int:
    app = QApplication(sys.argv)  # noqa: F841
    widget = jm.MorphingOrbWidget()
    widget.resize(1920, 1080)
    widget.show()
    failures = 0
    for sector, spec in enumerate(jm.MENU_SPECS):
        widget._close_radial_menu()
        widget._open_radial_menu(sector)
        widget._menu_alpha = 1.0
        widget._menu_reveal = 1.0
        _settle(widget)
        for node in widget._menu_nodes:
            node.visible_amount = 1.0
            node.hover_amount = 0.0

        report = widget._menu_layout_overlaps()
        solved = getattr(widget, "_layout_solved", None)
        mode = widget._menu_layout_mode
        status = "OK" if not report else f"{len(report)} CHEVAUCHEMENT(S)"
        print(
            f"{spec.name:<12} layout={mode:<5} "
            f"nodes={len(widget._menu_nodes):<2} {status} "
            f"solved={solved}"
        )
        for msg in report:
            print(f"    - {msg}")
            failures += 1

        # Second passage : orientation forcée via le vecteur de secteur
        # (couvre grille + ligne — ``_menu_layout_mode`` seul serait écrasé).
        for forced_label, vec in (("grid", (0.0, -1.0)), ("line", (1.0, 0.0))):
            original = widget._sector_vector
            widget._sector_vector = lambda _s, v=vec: v
            try:
                widget._layout_solved = None
                widget._close_radial_menu()
                widget._open_radial_menu(sector)
                widget._menu_alpha = 1.0
                widget._menu_reveal = 1.0
                _settle(widget, 120)
                for node in widget._menu_nodes:
                    node.visible_amount = 1.0
                report2 = widget._menu_layout_overlaps()
                if report2:
                    print(f"    [{forced_label}] {len(report2)} chevauchement(s) :")
                    for msg in report2:
                        print(f"        - {msg}")
                        failures += 1
            finally:
                widget._sector_vector = original
                widget._layout_solved = None

    widget.close()
    print("TOTAL", failures, "chevauchement(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
