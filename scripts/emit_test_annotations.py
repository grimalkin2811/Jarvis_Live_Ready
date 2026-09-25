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
        message = f"{test.id()} -- " + " | ".join(lines[-12:])
        # Les « : » cassent le parseur de commandes workflow (« :: »).
        safe = message.replace(":", " - ").replace("%", "pct")
        Path("failure.txt").write_text(safe, encoding="utf-8")
        print(f"::error::{safe[:1000]}", flush=True)
        _post_status(safe[:140])


def _post_status(description: str) -> None:
    """Publie un résumé lisible via l'API des statuts de commit."""
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    repo = os.environ.get("GITHUB_REPOSITORY", "").strip()
    sha = os.environ.get("GITHUB_SHA", "").strip()
    if not (token and repo and sha):
        return
    import json
    import urllib.request

    body = json.dumps(
        {
            "state": "failure",
            "context": "unittest-summary",
            "description": description or "echec sans message",
        }
    ).encode()
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/statuses/{sha}",
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            print(f"status posted {response.status}", flush=True)
    except Exception as exc:
        print(f"status post failed {exc}", flush=True)


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
