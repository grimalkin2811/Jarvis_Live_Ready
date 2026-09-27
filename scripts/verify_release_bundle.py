"""Vérifie le CONTENU RÉEL d'un bundle PyInstaller publié.

Pourquoi ce script existe
-------------------------
La release v1.5.0 a été étiquetée sur un commit d'une autre lignée : les
binaires publiés ne contenaient ni les fonds d'items des menus, ni le
solveur anti-chevauchement, ni le Writing Mode. Tous les contrôles de la
chaîne de publication étaient pourtant verts, parce qu'aucun d'eux ne
regardait *l'intérieur* du binaire produit.

``check_release_lineage.py`` verrouille la lignée Git *avant* le build.
Ce script-ci ferme l'autre bout de la chaîne : il ouvre l'exécutable
réellement publié, lit l'archive PYZ que PyInstaller y embarque, et vérifie
que les modules et les identifiants attendus s'y trouvent vraiment.

Il ne fait confiance ni au nom du fichier, ni au tag, ni à GitHub.

Usage
-----
    python scripts/verify_release_bundle.py --bundle dist/app --version 1.5.3
    python scripts/verify_release_bundle.py --bundle C:/tmp/Jarvis --version 1.5.3 \
        --expect-tools 117

``--bundle`` : dossier onedir contenant ``Jarvis.exe`` (ou ``Jarvis``) et
``_internal/``. Code de sortie 0 si tout est conforme, 1 sinon.
"""

from __future__ import annotations

import argparse
import json
import marshal
import os
import sys
import zlib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Modules qui DOIVENT être présents dans l'archive PYZ du binaire.
#: Leur absence signe un build issu d'une mauvaise lignée.
REQUIRED_MODULES = [
    # UI — fonds d'items + anti-chevauchement (1.3.1 / 1.3.2)
    "UI.jarvis_menu",
    "UI.appearance_actions",
    "UI.menu_state",
    "UI.visibility_bridge",
    # Writing Mode (1.4.0 / 1.4.2)
    "src.writing",
    "src.writing.service",
    "src.writing.active_field",
    "src.writing.files",
    "src.writing.filenames",
    "src.writing.intent",
    "src.writing.settings",
    "src.writing.text",
    # Musique Deezer (1.5.0)
    "src.music",
    "src.music.manager",
    "src.music.models",
    "src.music.intents",
    "src.music.providers",
    "src.music.providers.base",
    "src.music.providers.deezer",
    # Socle
    "src.tools",
    "src.version",
    "src.modes",
]

#: Identifiants / constantes qui doivent apparaître dans le code compilé du
#: module. C'est le contrôle qui aurait échoué sur le binaire v1.5.0.
REQUIRED_SYMBOLS = {
    "UI.jarvis_menu": [
        "_draw_hover_background",       # fond d'item (1.3.1)
        "hover_background_color",
        "item_bg_opacity",              # fond permanent réglable (1.3.2)
        "_solve_layout_spacings",       # solveur anti-chevauchement (1.3.2)
        "_layout_overlap_scan",
        "_menu_layout_overlaps",
        "Active Field",                 # menu Voice 12 items (1.4.0)
        "Text Files",
    ],
    "UI.appearance_actions": ["item_bg_opacity", "ITEM_BG_REST"],
    "src.writing.active_field": [
        "ActiveFieldInserter",
        "insert_via_clipboard",
        "assert_plan_is_insert_only",
    ],
    "src.writing.service": ["write_to_active_field", "create_text_file"],
    "src.music.providers.deezer": ["DeezerProvider", "DeezerHTTPClient"],
    "src.music.manager": ["MusicManager"],
}


# ---------------------------------------------------------------------------
# Lecture de l'archive PyInstaller
# ---------------------------------------------------------------------------
def _read_pyz_entries(exe_path: str) -> dict[str, bytes]:
    """Retourne ``{nom_module: donnees_marshalees}`` depuis le PYZ du binaire.

    PyInstaller embarque un CArchive à la fin de l'exécutable ; le PYZ
    (modules Python purs, chacun compressé en zlib) y est une entrée. On
    passe par les lecteurs officiels de PyInstaller pour ne pas dépendre du
    format binaire, qui change entre versions majeures.
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except Exception as exc:  # pragma: no cover - dépend de l'environnement
        raise RuntimeError(
            f"PyInstaller est requis pour lire le bundle ({exc}). "
            f"Installez-le : pip install pyinstaller"
        ) from exc

    archive = CArchiveReader(exe_path)
    pyz_name = None
    for name in archive.toc:
        if str(name).lower().endswith(".pyz") or str(name).lower().startswith("pyz"):
            pyz_name = name
            break
    if pyz_name is None:
        raise RuntimeError("aucune archive PYZ trouvée dans l'exécutable")

    payload = archive.extract(pyz_name)
    if isinstance(payload, tuple):  # anciennes API : (typecode, data)
        payload = payload[1]
    return _parse_pyz(bytes(payload))


def _parse_pyz(blob: bytes) -> dict[str, bytes]:
    """Décode une archive PYZ brute (``PYZ\\0`` + table des matières)."""
    if not blob.startswith(b"PYZ\0"):
        raise RuntimeError("en-tête PYZ inattendu")
    import struct

    toc_offset = struct.unpack("!i", blob[8:12])[0]
    toc = marshal.loads(blob[toc_offset:])
    if isinstance(toc, list):
        toc = dict(toc)

    entries: dict[str, bytes] = {}
    for name, value in toc.items():
        try:
            _typ, offset, length = value
        except Exception:
            continue
        raw = blob[offset:offset + length]
        try:
            entries[str(name)] = zlib.decompress(raw)
        except Exception:
            entries[str(name)] = raw
    return entries


def _code_strings(data: bytes) -> set[str]:
    """Tous les noms et constantes chaîne d'un module compilé (récursif)."""
    found: set[str] = set()
    try:
        code = marshal.loads(data)
    except Exception:
        # .pyc complet (en-tête de 16 octets) plutôt que code brut.
        try:
            code = marshal.loads(data[16:])
        except Exception:
            return found

    def walk(obj) -> None:
        names = getattr(obj, "co_names", None)
        if names is None:
            return
        found.update(names)
        found.update(getattr(obj, "co_varnames", ()))
        found.add(getattr(obj, "co_name", ""))
        for const in getattr(obj, "co_consts", ()):
            if isinstance(const, str):
                found.add(const)
            elif hasattr(const, "co_names"):
                walk(const)

    walk(code)
    return found


# ---------------------------------------------------------------------------
# Contrôles
# ---------------------------------------------------------------------------
def _find_executable(bundle: str) -> str:
    for candidate in ("Jarvis.exe", "Jarvis"):
        path = os.path.join(bundle, candidate)
        if os.path.isfile(path):
            return path
    raise RuntimeError(f"Jarvis.exe introuvable dans {bundle}")


def expected_tool_names() -> list[str]:
    """Noms d'outils déclarés dans l'arbre source courant."""
    sys.path.insert(0, REPO_ROOT)
    from src import tools

    return sorted(tools.TOOL_FUNCTIONS)


def verify(bundle: str, version: str, expect_tools: int | None) -> list[str]:
    problems: list[str] = []

    # 1. version.json à côté du bundle (écrit par build_windows.ps1).
    for candidate in (
        os.path.join(bundle, "version.json"),
        os.path.join(os.path.dirname(bundle.rstrip("/\\")), "version.json"),
    ):
        if os.path.isfile(candidate):
            with open(candidate, "r", encoding="utf-8-sig") as handle:
                payload = json.load(handle)
            found = str(payload.get("version", ""))
            if found != version:
                problems.append(f"version.json : {found!r} au lieu de {version!r}")
            else:
                print(f"version.json           : {found} OK")
            break
    else:
        print("version.json           : absent (non bloquant à cet emplacement)")

    # 2. Contenu réel de l'archive PYZ.
    exe = _find_executable(bundle)
    print(f"exécutable             : {exe}")
    entries = _read_pyz_entries(exe)
    print(f"modules dans le PYZ    : {len(entries)}")

    missing = [name for name in REQUIRED_MODULES if name not in entries]
    if missing:
        problems.append(f"modules absents du binaire : {', '.join(missing)}")
    else:
        print(f"modules requis         : {len(REQUIRED_MODULES)}/{len(REQUIRED_MODULES)} OK")

    # 3. Identifiants attendus DANS le code compilé.
    for module, symbols in REQUIRED_SYMBOLS.items():
        data = entries.get(module)
        if data is None:
            continue  # déjà signalé
        strings = _code_strings(data)
        absent = [symbol for symbol in symbols if symbol not in strings]
        if absent:
            problems.append(f"{module} : symboles absents -> {', '.join(absent)}")
        else:
            print(f"{module:<24} : {len(symbols)} symboles du contrat OK")

    # 4. Version réellement compilée dans src.version.
    data = entries.get("src.version")
    if data is not None:
        strings = _code_strings(data)
        if version not in strings:
            problems.append(
                f"src.version compilé ne contient pas la constante {version!r}"
            )
        else:
            print(f"src.version (compilé)  : constante {version} OK")

    # 5. Outils Gemini réellement embarqués.
    data = entries.get("src.tools")
    if data is not None:
        strings = _code_strings(data)
        try:
            names = expected_tool_names()
        except Exception as exc:
            print(f"outils                 : liste attendue indisponible ({exc})")
            names = []
        if names:
            absent = [name for name in names if name not in strings]
            if absent:
                problems.append(
                    f"src.tools : {len(absent)} outil(s) absent(s) du binaire "
                    f"(ex. {', '.join(absent[:5])})"
                )
            else:
                print(f"outils Gemini          : {len(names)}/{len(names)} présents OK")
            if expect_tools is not None and len(names) != expect_tools:
                problems.append(
                    f"nombre d'outils : {len(names)} au lieu de {expect_tools}"
                )

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, help="dossier onedir du build")
    parser.add_argument("--version", required=True, help="version attendue (1.5.1)")
    parser.add_argument(
        "--expect-tools",
        type=int,
        default=None,
        help="nombre d'outils Gemini attendu (ex. 117)",
    )
    args = parser.parse_args(argv)

    print("=== VERIFICATION DU CONTENU DU BUNDLE PUBLIE ===")
    try:
        problems = verify(args.bundle, args.version, args.expect_tools)
    except Exception as exc:
        print(f"\nECHEC : {exc}")
        return 1

    if problems:
        print(f"\n{len(problems)} PROBLEME(S) :")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nTOTAL 0 probleme — le binaire contient bien le contenu attendu.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
