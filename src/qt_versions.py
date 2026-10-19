"""Versions de PySide6 / Qt : contrôle de cohérence (build) et diagnostic (Dashboard).

Contexte (PR #50) : PySide6 6.12.0 (8 oct. 2026) arrête l'interpréteur sous
Linux avec Python < 3.12 (« Fatal Python error: none_dealloc », reproduit par
``tests/test_pyside_refcount.py``) ; ``requirements.txt`` borne donc
``PySide6<6.12``. Sur le PC Windows de validation, l'installation quotidienne
(construite AVANT cette borne) embarque Qt 6.12.0 et n'a montré aucune dérive
sur 20 000 appels : indice favorable, pas une validation de la suite complète.

Le binaire publié doit donc embarquer EXACTEMENT la version testée par la CI :
ce module permet à ``scripts/build_windows.ps1`` de le vérifier juste avant
PyInstaller (qui collecte PySide6 dans ce même environnement) et au Dashboard
d'afficher toute dérive (installation construite avant la borne, Qt mélangé).

Aucune dépendance : comparaison de versions minimale (opérateurs PEP 440
courants sur des versions numériques).
"""

from __future__ import annotations

import operator
import re
from typing import Any

#: Plage de PySide6 couverte par les tests de cette version de Jarvis. DOIT
#: rester identique à la contrainte de ``requirements.txt`` (vérifié par
#: ``tests/test_qt_versions.py``).
VALIDATED_PYSIDE6_SPEC = "<6.12"

_OPERATORS = {
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
    "==": operator.eq,
    "!=": operator.ne,
}
_CLAUSE = re.compile(r"^\s*(<=|>=|==|!=|<|>)\s*([0-9][0-9.]*)\s*$")


def parse_version(text: str) -> tuple[int, ...]:
    """« 6.11.2 » -> (6, 11, 2) ; suffixes non numériques ignorés."""
    parts = []
    for piece in str(text or "").strip().split("."):
        match = re.match(r"(\d+)", piece)
        if not match:
            break
        parts.append(int(match.group(1)))
    return tuple(parts)


def _padded(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    size = max(len(a), len(b))
    return a + (0,) * (size - len(a)), b + (0,) * (size - len(b))


def satisfies(version: str, spec: str) -> bool:
    """Vrai si ``version`` respecte toutes les clauses de ``spec`` (« <6.12,>=6.5 »)."""
    current = parse_version(version)
    if not current:
        return False
    for clause in str(spec or "").split(","):
        if not clause.strip():
            continue
        match = _CLAUSE.match(clause)
        if not match:
            raise ValueError(f"clause de version non prise en charge : {clause!r}")
        left, right = _padded(current, parse_version(match.group(2)))
        if not _OPERATORS[match.group(1)](left, right):
            return False
    return True


def requirement_spec(requirements_text: str, package: str = "PySide6") -> str | None:
    """Contrainte de ``package`` dans un fichier requirements (``None`` si absente).

    « PySide6<6.12 » -> « <6.12 » ; « PySide6 » seul -> « » (aucune borne).
    Les marqueurs d'environnement (``; python_version < "3.12"``) ne sont pas
    évalués : ils sont refusés explicitement plutôt qu'ignorés en silence.
    """
    pattern = re.compile(rf"^{re.escape(package)}(?![A-Za-z0-9_.\-])\s*(?P<spec>[^;]*)(?P<marker>;.*)?$",
                         re.IGNORECASE)
    for raw in requirements_text.splitlines():
        line = raw.split("#", 1)[0].strip()
        match = pattern.match(line)
        if not match:
            continue
        if match.group("marker"):
            raise ValueError(f"marqueur d'environnement non pris en charge pour {package} : {line!r}")
        return match.group("spec").strip()
    return None


def runtime_versions() -> dict[str, str]:
    """Versions RÉELLEMENT chargées (paquet, liaisons, bibliothèque Qt)."""
    info: dict[str, str] = {}
    try:
        import PySide6

        info["pyside6"] = str(PySide6.__version__)
    except Exception as exc:
        info["pyside6"] = f"indisponible ({type(exc).__name__})"
        return info
    try:
        import shiboken6

        info["shiboken6"] = str(shiboken6.__version__)
    except Exception:
        info["shiboken6"] = "indisponible"
    try:
        from PySide6 import QtCore

        info["qt_runtime"] = str(QtCore.qVersion())
    except Exception:
        info["qt_runtime"] = "indisponible"
    return info


def check(spec: str = VALIDATED_PYSIDE6_SPEC, versions: dict[str, str] | None = None) -> dict[str, Any]:
    """Contrôle de cohérence : plage testée, liaisons et Qt de la même version."""
    info = dict(versions if versions is not None else runtime_versions())
    problems: list[str] = []
    pyside = info.get("pyside6", "")
    if not parse_version(pyside):
        problems.append(f"PySide6 introuvable ({pyside})")
    else:
        if not satisfies(pyside, spec):
            problems.append(f"PySide6 {pyside} hors de la plage testée ({spec})")
        prefix = parse_version(pyside)[:3]
        shiboken = info.get("shiboken6", "")
        if parse_version(shiboken)[:3] != prefix:
            problems.append(f"shiboken6 {shiboken} différent de PySide6 {pyside}")
        qt = info.get("qt_runtime", "")
        if parse_version(qt)[:3] != prefix:
            problems.append(f"bibliothèque Qt chargée {qt} différente de PySide6 {pyside} (installation mélangée ?)")
    return {"ok": not problems, "spec": spec, "problems": problems, **info}


def describe(result: dict[str, Any] | None = None) -> str:
    """Ligne lisible pour les diagnostics."""
    result = result if result is not None else check()
    if result["ok"]:
        return f"conforme (PySide6 {result.get('pyside6')}, Qt {result.get('qt_runtime')}, plage testée {result['spec']})"
    return "À VÉRIFIER : " + " ; ".join(result["problems"])
