"""Garde-fou de publication : le commit publié doit CONTENIR les versions précédentes.

Pourquoi ce script existe
-------------------------
La 1.5.0 a été étiquetée sur la tête de la branche Deezer (PR #27), branche
coupée depuis ``v1.1.2``. Ce commit ne contenait donc AUCUN des travaux
1.2.0 → 1.4.2 : plus de fonds d'items dans les menus radiaux, plus de
solveur de layout anti-chevauchement, plus de masquage du Blob, plus de
Writing Mode. Les binaires publiés (``JarvisSetup-1.5.0.exe``, zip
portable) ont donc embarqué une interface antérieure à la 1.2.0.

La CI n'a rien vu : la branche transportait AUSSI ses propres tests
(anciens), et ``tests/test_menu_backgrounds.py``, ``test_menu_layout.py``,
``test_blob_visibility.py``, ``test_item_bg_opacity.py``… n'y existaient
tout simplement pas. Aucun test *interne au dépôt* ne peut détecter ce cas :
il faut une vérification de LIGNÉE, au niveau Git.

Ce que le script vérifie
------------------------
1. Lignée : chaque tag ``vX.Y.Z`` de version INFÉRIEURE à celle publiée doit
   être un ancêtre du commit publié. Sinon, la publication ferait
   régresser des fonctionnalités déjà livrées.
2. Contrat UI : les marqueurs des fonctionnalités UI 1.3.x (fonds d'items,
   solveur anti-chevauchement) sont présents dans l'arbre de travail. Filet
   de sécurité si un jour l'arbre est assemblé autrement que par Git.

Usage
-----
    python scripts/check_release_lineage.py              # vérifie HEAD
    python scripts/check_release_lineage.py --ref v1.5.0 # vérifie un tag

Code de sortie : 0 si tout va bien, 1 sinon.
Nécessite un clone complet (``fetch-depth: 0`` en CI).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Marqueurs du contrat UI livré en 1.3.1/1.3.2 et conservé depuis.
#: Chaque entrée : (chemin relatif, extrait obligatoire, fonctionnalité).
UI_CONTRACT = [
    (
        "UI/appearance_actions.py",
        "item_bg_opacity",
        "réglage Appearance « Item BG Opacity » (fonds des items, 1.3.2)",
    ),
    (
        "UI/jarvis_menu.py",
        "_draw_hover_background",
        "rendu du fond d'item derrière le libellé (1.3.1)",
    ),
    (
        "UI/jarvis_menu.py",
        "item_bg_opacity",
        "fond d'item permanent piloté par l'état d'apparence (1.3.2)",
    ),
    (
        "UI/jarvis_menu.py",
        "_solve_layout_spacings",
        "solveur de layout anti-chevauchement des menus (1.3.2)",
    ),
    (
        "UI/jarvis_menu.py",
        "_menu_layout_overlaps",
        "rapport de chevauchement utilisé par les tests de layout (1.3.2)",
    ),
]

#: Tests de non-régression qui DOIVENT accompagner le contrat UI. Leur
#: absence est exactement ce qui a rendu la CI verte sur le tag v1.5.0.
REQUIRED_TESTS = [
    "tests/test_menu_backgrounds.py",
    "tests/test_menu_layout.py",
    "tests/test_item_bg_opacity.py",
    "tests/test_blob_visibility.py",
    "tests/test_ui_menu_regression.py",
]

_VERSION_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _version_tuple(tag: str) -> tuple[int, int, int] | None:
    match = _VERSION_TAG.match(tag)
    if not match:
        return None
    return tuple(int(part) for part in match.groups())  # type: ignore[return-value]


def _is_ancestor(candidate: str, ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", candidate, ref],
            cwd=REPO_ROOT,
            capture_output=True,
        ).returncode
        == 0
    )


def check_lineage(ref: str) -> list[str]:
    """Tags de version inférieure qui NE SONT PAS contenus dans ``ref``."""
    try:
        tags = _git("tag", "-l").splitlines()
    except subprocess.CalledProcessError:
        return []
    try:
        ref_sha = _git("rev-list", "-n1", ref)
    except subprocess.CalledProcessError:
        print(f"ERREUR : référence Git introuvable : {ref}", file=sys.stderr)
        raise SystemExit(2) from None

    current = None
    for tag in tags:
        version = _version_tuple(tag.strip())
        if version and _git("rev-list", "-n1", tag.strip()) == ref_sha:
            current = version if current is None else max(current, version)

    missing: list[str] = []
    for tag in tags:
        tag = tag.strip()
        version = _version_tuple(tag)
        if version is None:
            continue
        if current is not None and version >= current:
            continue
        if _git("rev-list", "-n1", tag) == ref_sha:
            continue
        if not _is_ancestor(tag, ref):
            missing.append(tag)
    missing.sort(key=lambda tag: _version_tuple(tag) or (0, 0, 0))
    return missing


def check_ui_contract() -> list[str]:
    """Marqueurs du contrat UI absents de l'arbre de travail."""
    problems: list[str] = []
    for relative, needle, feature in UI_CONTRACT:
        path = os.path.join(REPO_ROOT, relative)
        if not os.path.exists(path):
            problems.append(f"{relative} : fichier absent ({feature})")
            continue
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
        if needle not in content:
            problems.append(f"{relative} : « {needle} » introuvable — {feature}")
    for relative in REQUIRED_TESTS:
        if not os.path.exists(os.path.join(REPO_ROOT, relative)):
            problems.append(f"{relative} : test de non-régression absent")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="HEAD", help="référence Git à vérifier")
    parser.add_argument(
        "--skip-lineage",
        action="store_true",
        help="ne vérifier que le contrat UI (clone superficiel)",
    )
    args = parser.parse_args(argv)

    failures = 0

    contract_problems = check_ui_contract()
    if contract_problems:
        failures += len(contract_problems)
        print("CONTRAT UI : ECHEC")
        for problem in contract_problems:
            print(f"  - {problem}")
    else:
        print("CONTRAT UI : OK (fonds d'items + anti-chevauchement presents)")

    if args.skip_lineage:
        print("LIGNEE : ignoree (--skip-lineage)")
    else:
        missing = check_lineage(args.ref)
        if missing:
            failures += len(missing)
            print(f"LIGNEE : ECHEC pour {args.ref}")
            for tag in missing:
                print(f"  - {tag} n'est PAS un ancetre de {args.ref}")
            print(
                "  => publier ce commit ferait disparaitre des fonctionnalites\n"
                "     deja livrees. Fusionner main avant d'etiqueter."
            )
        else:
            print(f"LIGNEE : OK ({args.ref} contient toutes les versions anterieures)")

    if failures:
        print(f"\nTOTAL {failures} probleme(s) — publication bloquee.")
        return 1
    print("\nTOTAL 0 probleme.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
