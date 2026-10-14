"""Compare les références Git et métadonnées publiques v1.8.2 à la baseline.

Lecture seule. Ne télécharge ni n'exécute les binaires : les SHA-256 sont ceux
publiés par GitHub, pas des sommes recalculées ici. Nécessite git/gh authentifiés.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REPO = "grimalkin2811/Jarvis_Live_Ready"
NOTES = "docs/releases/RELEASE_NOTES_v1.8.0.md"


def release_identity(release: dict) -> dict:
    fields = ("id", "tag_name", "draft", "prerelease", "published_at")
    result = {key: release.get(key) for key in fields}
    result["name"] = release.get("name")
    result["body_sha256"] = hashlib.sha256((release.get("body") or "").encode("utf-8")).hexdigest()
    return result


def differences(baseline: dict, release: dict, tag_lines: list[str], notes_oid: str) -> list[str]:
    problems = []
    if release_identity(release) != baseline["release"]:
        problems.append("L'identité ou les notes de la release v1.8.2 ont changé.")
    keys = ("id", "name", "size", "digest", "created_at", "updated_at")
    assets = [{key: asset.get(key) for key in keys} for asset in release.get("assets", [])]
    if sorted(assets, key=lambda a: str(a["id"])) != sorted(baseline["assets"], key=lambda a: str(a["id"])):
        problems.append("Les trois assets v1.8.2 (identités, tailles, dates ou empreintes API) ont changé.")
    # Inclut les références d'objets annotés ET leurs cibles ^{}.
    if set(tag_lines) != set(baseline["tags"]):
        problems.append("Les références de tags distants ont changé.")
    if notes_oid != baseline["notes_v180_blob"]:
        problems.append("Les notes historiques v1.8.0 sont absentes ou modifiées.")
    return problems


def _run(*command):
    return subprocess.run(command, cwd=ROOT, check=True, capture_output=True, text=True, timeout=60).stdout.strip()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=ROOT / "docs/releases/INTEGRATION_BASELINE.json")
    args = parser.parse_args(argv)
    try:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        release = json.loads(_run("gh", "api", f"repos/{REPO}/releases/tags/v1.8.2"))
        tags = _run("git", "ls-remote", "origin", "refs/tags/*").splitlines()
        oid = _run("git", "rev-parse", f"HEAD:{NOTES}")
        problems = differences(baseline, release, tags, oid)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        print("Vérification non exécutée complètement : accès GitHub, outils ou baseline indisponibles.")
        return 2
    for problem in problems:
        print("ECHEC : " + problem)
    if problems:
        return 1
    print("OK : tags distants, notes v1.8.0, release v1.8.2 et trois assets inchangés (API/refs).")
    print("Limite : aucune empreinte recalculée depuis les binaires téléchargés dans ce contrôle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
