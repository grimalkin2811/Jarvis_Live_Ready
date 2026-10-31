"""Contrôle des versions PySide6 / Qt avant PyInstaller (PR #50).

Appelé par ``scripts/build_windows.ps1`` après installation du verrou Windows
et AVANT PyInstaller, qui collecte PySide6 dans ce même interpréteur : le
binaire publié embarque donc exactement les versions vérifiées ici.

Refuse le build (code 1) si :
* PySide6 ne respecte pas la contrainte de ``requirements.txt`` ;
* la borne testée codée dans Jarvis (``src.qt_versions``) diffère de
  ``requirements.txt`` (les deux doivent évoluer ensemble) ;
* shiboken6 ou la bibliothèque Qt chargée n'ont pas la version de PySide6.

Usage : python scripts/check_qt_runtime.py --requirements requirements.txt [--out versions.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import qt_versions  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--requirements", default=str(ROOT / "requirements.txt"))
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)
    spec = qt_versions.requirement_spec(Path(args.requirements).read_text(encoding="utf-8"))
    problems: list[str] = []
    if spec is None:
        problems.append("PySide6 absent de requirements.txt")
        spec = ""
    elif spec != qt_versions.VALIDATED_PYSIDE6_SPEC:
        problems.append(f"borne de requirements.txt ({spec or 'aucune'}) différente de la borne testée "
                        f"src.qt_versions.VALIDATED_PYSIDE6_SPEC ({qt_versions.VALIDATED_PYSIDE6_SPEC})")
    result = qt_versions.check(spec)
    result["problems"] = problems + result["problems"]
    result["ok"] = not result["problems"]
    result["python"] = sys.version.split()[0]
    print(f"PySide6 {result.get('pyside6')} · shiboken6 {result.get('shiboken6')} · Qt chargé "
          f"{result.get('qt_runtime')} · contrainte requirements.txt « {spec or 'aucune'} » · Python {result['python']}")
    for problem in result["problems"]:
        print(f"  PROBLÈME : {problem}")
    print("Versions PySide6/Qt : " + ("CONFORMES" if result["ok"] else "INCOHÉRENTES — build refusé"))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
