"""Fait échouer l'étape avec un code qui identifie le premier test en échec.

Les journaux du runner ne sont pas téléchargeables. Le code de sortie, lui,
est visible via l'API des jobs :

* 9 : le script a planté avant un résultat de test
* 10 + index : index du module ``tests/test_*.py`` trié qui a échoué
* l'étape suivante relit ``FAIL_ASSERT_CODE`` (bitmask du message)
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

MODULES = sorted(path.stem for path in Path("tests").glob("test_*.py"))


def _export(name: str, value: str) -> None:
    path = os.environ.get("GITHUB_ENV", "").strip()
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _assert_code(message: str) -> int:
    code = 0
    if "fuite" in message:
        code |= 1
    if "fond" in message:
        code |= 2
    if "position" in message:
        code |= 4
    if "AssertionError" in message:
        code |= 8
    if "ImportError" in message or "ModuleNotFoundError" in message:
        code |= 16
    if "Error" in message and "AssertionError" not in message:
        code |= 32
    if not code:
        code = 64
    return code


def _module_code(test_id: str) -> int:
    matched = [index for index, name in enumerate(MODULES) if name in test_id]
    if not matched:
        return 80
    return 10 + max(matched, key=lambda index: len(MODULES[index]))


class AnnotatingResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.exit_code = 0

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._note(test, err)

    def addError(self, test, err):
        super().addError(test, err)
        self._note(test, err)

    def addSubTest(self, test, subtest, err):
        super().addSubTest(test, subtest, err)
        if err is not None:
            self._note(subtest, err)

    def _note(self, test, err) -> None:
        if self.exit_code:
            return
        text = "".join(traceback.format_exception(*err))
        safe = text.replace(":", " - ").replace("\n", " | ")
        code = _module_code(test.id())
        assert_code = _assert_code(safe)
        self.exit_code = code
        _export("FAIL_ASSERT_CODE", str(assert_code))
        _export("FAIL_MODULE_CODE", str(code))
        last = ""
        for raw in text.splitlines():
            if raw.strip():
                last = raw.strip()
        last = last.replace(":", "-").replace("%", "pct")[:180]
        test_id = test.id().replace(":", "-")[:120]
        print(f"::error title={test_id}::{last}", flush=True)
        print(f"FAILING TEST: {test.id()}", flush=True)
        Path("failure.txt").write_text(f"{test.id()}\n{safe[-1500:]}", encoding="utf-8")


def main() -> int:
    try:
        suite = unittest.defaultTestLoader.discover("tests")
        result = unittest.TextTestRunner(
            verbosity=1,
            failfast=True,
            resultclass=AnnotatingResult,
        ).run(suite)
    except Exception as exc:
        _export("FAIL_ASSERT_CODE", "9")
        print(f"runner crash {type(exc).__name__}", flush=True)
        return 9
    if result.wasSuccessful():
        return 0
    return getattr(result, "exit_code", None) or 7


if __name__ == "__main__":
    raise SystemExit(main())
