"""Réécrit le premier échec unittest en annotation GitHub Actions.

Les journaux bruts du runner ne sont pas toujours téléchargeables. Une
annotation ``::error::`` reste lisible via l'API des check-runs.
"""

from __future__ import annotations

import os
import sys
import traceback
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class AnnotatingResult(unittest.TextTestResult):
    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._annotate(test, err)

    def addError(self, test, err):
        super().addError(test, err)
        self._annotate(test, err)

    def _annotate(self, test, err) -> None:
        text = "".join(traceback.format_exception(*err))
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        message = f"{test.id()} :: " + " | ".join(lines[-12:])
        message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::error::{message[:1200]}")


def main() -> int:
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(
        verbosity=1,
        failfast=True,
        resultclass=AnnotatingResult,
    ).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
