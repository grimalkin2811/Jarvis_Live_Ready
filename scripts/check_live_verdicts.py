#!/usr/bin/env python3
"""Porte de verdicts des validations réelles — doit précéder tout PASS (v1.9.0).

Pourquoi ce script existe
-------------------------
L'étape « Conclusion » du workflow se contentait de chercher les chaînes
``' : FAIL'`` et ``'NON TESTABLE'`` dans les logs. Un run qui **n'imprime
aucun verdict** — import cassé, dépendance manquante, plantage avant le bloc
final, log tronqué — ne contient ni l'une ni l'autre : la porte s'ouvrait donc
sur du vide. C'est un vert par **absence de preuve**, exactement ce qu'un
verdict de validation ne doit jamais être.

Ici, chaque verdict attendu doit être **présent** et valoir ``PASS``. Toute
autre situation (absent, ``FAIL``, ``NON TESTABLE``, valeur inconnue, log
absent) est un échec explicite et nommé. Le script ne décide jamais qu'un
scénario est bon : il constate, ou il refuse.

Usage
-----
    python scripts/check_live_verdicts.py \\
        --main-log validation.log --v174-log validation_v174.log

Sortie : 0 seulement si **tous** les verdicts attendus sont PASS.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

#: Verdicts produits par ``scripts/validate_real_gemini.py`` (S1-S4 + outils).
MAIN_VERDICTS: tuple[str, ...] = (
    "GEMINI LIVE RÉEL",
    "AUDIO RÉEL",
    "RECONNEXION RÉELLE",
    "SESSION RESUMPTION RÉELLE",
    "OUTILS RÉELS",
)

#: Verdicts produits par ``scripts/validate_reconnect_v174_real.py``
#: (S1 et S4 sont mesurés dans le même scénario « silence puis expiration »).
V174_VERDICTS: tuple[str, ...] = (
    "S1+S4 SILENCE PUIS EXPIRATION",
    "S2 DEUX TOURS RAPIDES",
    "S3 TROIS TOURS",
    "S5 RECONNEXION REELLE",
    "S6 CONTEXTE REEL APRES RECONNEXION",
)

PASS = "PASS"
KNOWN = ("PASS", "FAIL", "NON TESTABLE")

_LINE = re.compile(r"^(?P<label>.+?)\s*:\s*(?P<verdict>NON TESTABLE|PASS|FAIL)\s*$")


def _norm(text: str) -> str:
    return " ".join(text.strip().upper().split())


def parse_verdicts(text: str, expected: tuple[str, ...]) -> dict[str, str | None]:
    """Associe chaque libellé attendu à son verdict (dernière occurrence)."""
    wanted = {_norm(name): name for name in expected}
    found: dict[str, str | None] = {name: None for name in expected}
    for raw in text.splitlines():
        match = _LINE.match(raw.strip())
        if not match:
            continue
        key = _norm(match.group("label"))
        if key in wanted:
            found[wanted[key]] = match.group("verdict")
    return found


def check_log(path: Path, expected: tuple[str, ...]) -> list[str]:
    """Problèmes d'un log : absent, verdict manquant, ou verdict non-PASS."""
    if not path.is_file():
        return [f"{path.name} : log ABSENT — aucun verdict, donc aucun PASS."]
    text = path.read_text(encoding="utf-8", errors="replace")
    problems: list[str] = []
    for name, verdict in parse_verdicts(text, expected).items():
        if verdict is None:
            problems.append(f"{path.name} : verdict ABSENT pour « {name} ».")
        elif verdict not in KNOWN:
            problems.append(f"{path.name} : « {name} » = {verdict} (valeur inconnue).")
        elif verdict != PASS:
            problems.append(f"{path.name} : « {name} » = {verdict}.")
    return problems


def check(main_log: Path, v174_log: Path) -> list[str]:
    problems = check_log(main_log, MAIN_VERDICTS)
    problems += check_log(v174_log, V174_VERDICTS)
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--main-log", default="validation.log")
    parser.add_argument("--v174-log", default="validation_v174.log")
    args = parser.parse_args(argv)

    main_log, v174_log = Path(args.main_log), Path(args.v174_log)
    problems = check(main_log, v174_log)

    print("=" * 70)
    if problems:
        print("VERDICTS INCOMPLETS OU NON-PASS — ce job ne peut pas être vert :")
        for problem in problems:
            print(f"  - {problem}")
        print("=" * 70)
        return 1

    for path, expected in ((main_log, MAIN_VERDICTS), (v174_log, V174_VERDICTS)):
        for name in expected:
            print(f"  {path.name} : {name} = {PASS}")
    print("Tous les verdicts attendus sont présents et PASS.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
