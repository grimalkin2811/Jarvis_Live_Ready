"""Publie l'extrait d'un journal unittest dans un check-run GitHub.

Les journaux bruts du runner ne sont pas toujours téléchargeables. Le résumé
est donc lisible via l'API des check-runs.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.request


def _excerpt(text: str) -> str:
    lines = text.splitlines()
    keep = [
        line
        for line in lines
        if line.startswith(
            (
                "FAIL:",
                "ERROR:",
                "AssertionError",
                "Traceback",
                "Ran ",
                "FAILED",
                "OK",
                "    ",
                "  File ",
            )
        )
    ]
    return "\n".join(keep[-100:] or lines[-50:])[:60000]


def main() -> int:
    log_path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "unittest.log")
    text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""
    summary = _excerpt(text) or "journal introuvable"
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    sha = os.environ.get("GITHUB_SHA", "")
    if not (token and repo and sha):
        print(summary)
        return 0
    payload = {
        "name": "unittest-failures",
        "head_sha": sha,
        "status": "completed",
        "conclusion": "failure",
        "output": {
            "title": "Échecs unittest",
            "summary": summary,
        },
    }
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/check-runs",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request) as response:
            print("check-run", response.status)
    except Exception as exc:
        detail = ""
        if hasattr(exc, "read"):
            detail = exc.read().decode("utf-8", errors="replace")
        print("check-run failed", exc, detail)
        print(summary)
    comment = {
        "body": "Échecs unittest\n\n```\n" + summary[:6000] + "\n```",
        "path": "scripts/report_unittest_log.py",
        "line": 1,
        "side": "RIGHT",
    }
    comment_request = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/commits/{sha}/comments",
        data=json.dumps(comment).encode("utf-8"),
        headers=request.headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(comment_request) as response:
            print("commit-comment", response.status)
    except Exception as exc:
        detail = ""
        if hasattr(exc, "read"):
            detail = exc.read().decode("utf-8", errors="replace")
        print("commit-comment failed", exc, detail)
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        pathlib.Path(summary_path).write_text(summary, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
