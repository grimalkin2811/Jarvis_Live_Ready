"""Exécute une tranche de modules de tests dans un seul processus.

Sert à localiser un échec qui n'apparaît que lorsque les modules tournent
ensemble (état partagé), pas lorsqu'ils sont lancés isolément.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))


def main() -> int:
    start = int(sys.argv[1])
    end = int(sys.argv[2])
    extra = sys.argv[3] if len(sys.argv) > 3 else ""
    modules = sorted(path.stem for path in Path("tests").glob("test_*.py"))
    selected = modules[start:end]
    print("modules:", ", ".join(selected), "extra:", extra or "-")
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in selected:
        suite.addTests(loader.loadTestsFromName(f"tests.{name}"))
    if extra:
        suite.addTests(loader.loadTestsFromName(extra))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
