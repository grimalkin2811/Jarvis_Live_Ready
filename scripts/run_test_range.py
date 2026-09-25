"""Exécute une tranche de modules de tests dans un seul processus.

Sert à localiser un échec qui n'apparaît que lorsque les modules tournent
ensemble (état partagé), pas lorsqu'ils sont lancés isolément.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


def main() -> int:
    start = int(sys.argv[1])
    end = int(sys.argv[2])
    modules = sorted(path.stem for path in Path("tests").glob("test_*.py"))
    selected = modules[start:end]
    print("modules:", ", ".join(selected))
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in selected:
        suite.addTests(loader.loadTestsFromName(f"tests.{name}"))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
