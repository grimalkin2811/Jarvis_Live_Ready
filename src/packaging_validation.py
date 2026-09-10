"""Validation centralisée de l'arborescence de distribution Jarvis.

Ce module est la source unique de vérité pour « qu'est-ce qu'une installation
Jarvis valide ? ». Il est utilisé par :

* scripts/build_windows.ps1 (via scripts/validate_build.py)
* .github/workflows/build.yml (CI)
* src/updater.py (validation avant/après update)
* launcher/main.py (message d'erreur clair si installation corrompue)
* tests/test_packaging.py

PyInstaller 6.x produit une structure moderne :

    Jarvis/
    ├── Jarvis.exe
    └── _internal/
        ├── python311.dll
        ├── base_library.zip
        ├── PySide6/
        └── ...

L'installation finale (via Inno Setup ou ZIP portable) doit conserver cette
structure :

    %LOCALAPPDATA%/Jarvis/
    ├── JarvisLauncher.exe
    ├── version.json
    └── app/
        ├── Jarvis.exe
        └── _internal/
            ├── python311.dll
            ├── base_library.zip
            └── ...

Toute déviation (python311.dll à la racine de app/, _internal manquant, etc.)
doit faire échouer le build et empêcher une release.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Listes de fichiers critiques
# ---------------------------------------------------------------------------

#: Fichiers critiques à l'intérieur d'un dossier app/ (ou dist/Jarvis/)
#: Tous les chemins sont relatifs à la racine de l'app.
CRITICAL_APP_FILES: list[str] = [
    "Jarvis.exe",
    "_internal/python311.dll",
    "_internal/base_library.zip",
]

#: Fichiers critiques supplémentaires recommandés (présence souhaitée mais pas
#: bloquante pour le smoke test minimal). On les vérifie en mode strict.
RECOMMENDED_APP_FILES: list[str] = [
    "_internal/PySide6",
    "_internal/numpy",
]

#: Fichiers critiques pour une installation complète (launcher + app)
#: Chemins relatifs à %LOCALAPPDATA%/Jarvis/
CRITICAL_INSTALL_FILES: list[str] = [
    "JarvisLauncher.exe",
    "version.json",
    "app/Jarvis.exe",
    "app/_internal/python311.dll",
    "app/_internal/base_library.zip",
]

#: Fichiers qui NE DOIVENT PAS exister à la racine de app/ (signe d'aplatissement)
FORBIDDEN_FLATTENED_FILES: list[str] = [
    "python311.dll",
    "base_library.zip",
    "python3.dll",
    "VCRUNTIME140.dll",
]


# ---------------------------------------------------------------------------
# Validation de répertoires
# ---------------------------------------------------------------------------

def _check_required_files(base: Path, required: Iterable[str]) -> list[str]:
    """Retourne la liste des fichiers/dossiers requis manquants."""
    missing: list[str] = []
    for rel in required:
        target = base / rel
        if not target.exists():
            missing.append(rel)
    return missing


def _check_forbidden_files(base: Path, forbidden: Iterable[str]) -> list[str]:
    """Retourne la liste des fichiers interdits qui existent (flatten)."""
    found: list[str] = []
    for rel in forbidden:
        target = base / rel
        if target.exists():
            found.append(rel)
    return found


def validate_app_dir(app_path: str | Path, *, strict: bool = False) -> tuple[bool, list[str], list[str]]:
    """Valide un dossier d'application (dist/Jarvis/ ou dist/app/).

    Retourne (is_valid, missing, forbidden_found).

    * missing : fichiers critiques absents
    * forbidden_found : fichiers qui indiquent un aplatissement
    """
    base = Path(app_path)
    if not base.is_dir():
        return False, [f"Dossier introuvable: {base}"], []

    missing = _check_required_files(base, CRITICAL_APP_FILES)
    forbidden = _check_forbidden_files(base, FORBIDDEN_FLATTENED_FILES)

    if strict:
        # En mode strict, on vérifie aussi les recommandés
        for rel in RECOMMENDED_APP_FILES:
            if not (base / rel).exists():
                missing.append(rel + " (recommandé)")

    # Si _internal n'existe pas du tout, c'est critique
    if not (base / "_internal").is_dir():
        if "_internal/ (dossier)" not in missing:
            missing.append("_internal/ (dossier)")

    is_valid = len(missing) == 0 and len(forbidden) == 0
    return is_valid, missing, forbidden


def validate_install_dir(install_path: str | Path) -> tuple[bool, list[str], list[str]]:
    """Valide une installation complète (dossier contenant launcher + app/)."""
    base = Path(install_path)
    if not base.is_dir():
        return False, [f"Dossier introuvable: {base}"], []

    missing = _check_required_files(base, CRITICAL_INSTALL_FILES)
    # Vérifie aussi l'aplatissement dans app/
    app_dir = base / "app"
    forbidden: list[str] = []
    if app_dir.is_dir():
        forbidden = _check_forbidden_files(app_dir, FORBIDDEN_FLATTENED_FILES)
        # Ajoute préfixe app/ pour clarté
        forbidden = [f"app/{f}" for f in forbidden]

    is_valid = len(missing) == 0 and len(forbidden) == 0
    return is_valid, missing, forbidden


# ---------------------------------------------------------------------------
# Validation de ZIP
# ---------------------------------------------------------------------------

def _zip_contains(zip_file: zipfile.ZipFile, rel_path: str) -> bool:
    """Vrai si le ZIP contient un fichier ou dossier avec ce chemin."""
    # Normalise les séparateurs
    rel_path = rel_path.replace("\\", "/")
    names = zip_file.namelist()
    # Exact match ou préfixe pour dossiers
    for name in names:
        normalized = name.replace("\\", "/")
        if normalized == rel_path or normalized.startswith(rel_path.rstrip("/") + "/"):
            return True
        # Cas où le ZIP contient app/ comme racine (ex: app/Jarvis.exe)
        # On accepte aussi sans préfixe app/ si on valide un app dir
    return False


def _zip_has_flattened(zip_file: zipfile.ZipFile) -> list[str]:
    """Détecte les fichiers aplatis à la racine du ZIP."""
    found: list[str] = []
    names = [n.replace("\\", "/") for n in zip_file.namelist()]
    # Si le ZIP contient app/ comme dossier racine, on regarde à l'intérieur de app/
    # Sinon, on regarde à la racine
    has_app_prefix = any(n.startswith("app/") for n in names)

    for forbidden in FORBIDDEN_FLATTENED_FILES:
        # Chemins à vérifier
        candidates = [forbidden, f"app/{forbidden}"] if has_app_prefix else [forbidden]
        # Mais si has_app_prefix, on ne veut PAS que app/python311.dll existe
        # (il doit être dans app/_internal/)
        # Donc on vérifie app/python311.dll
        if has_app_prefix:
            if f"app/{forbidden}" in names:
                found.append(f"app/{forbidden}")
        else:
            if forbidden in names:
                found.append(forbidden)
    return found


def validate_zip(zip_path: str | Path, *, expect_app_prefix: bool = False) -> tuple[bool, list[str], list[str]]:
    """Valide une archive portable.

    Args:
        zip_path: Chemin vers le ZIP
        expect_app_prefix: Si True, le ZIP doit contenir app/ comme dossier racine.
                          Si False, il doit contenir Jarvis.exe à la racine (ou dans app/).

    Retourne (is_valid, missing, forbidden_found).
    """
    zip_path = Path(zip_path)
    if not zip_path.is_file():
        return False, [f"ZIP introuvable: {zip_path}"], []

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            names = [n.replace("\\", "/") for n in zf.namelist()]
            if not names:
                return False, ["ZIP vide"], []

            # Détecte si le ZIP a un préfixe app/
            has_app_prefix = any(n.startswith("app/") for n in names)
            # Détecte si c'est un ZIP avec un seul dossier racine (ex: app/)
            # ou directement les fichiers de l'app

            # Détermine la base logique pour les checks
            # Si has_app_prefix, on attend app/Jarvis.exe etc.
            # Sinon, on attend Jarvis.exe à la racine

            missing: list[str] = []
            # Vérifie les fichiers critiques
            if has_app_prefix:
                for rel in CRITICAL_APP_FILES:
                    if f"app/{rel}" not in names and not any(n == f"app/{rel}" or n.startswith(f"app/{rel}/") for n in names):
                        # Vérifie aussi avec dossier
                        if rel.endswith(".exe") or rel.endswith(".dll") or rel.endswith(".zip"):
                            if f"app/{rel}" not in names:
                                missing.append(f"app/{rel}")
            else:
                for rel in CRITICAL_APP_FILES:
                    # Pour _internal/*, on vérifie présence
                    if rel not in names and not any(n.startswith(rel + "/") or n == rel for n in names):
                        # Cas spécial: _internal est un dossier, donc on vérifie _internal/python311.dll
                        if rel not in names:
                            missing.append(rel)

            forbidden = _zip_has_flattened(zf)

            # Vérifie aussi que _internal existe
            if has_app_prefix:
                if not any(n.startswith("app/_internal/") for n in names):
                    if "app/_internal/ (dossier)" not in missing:
                        missing.append("app/_internal/ (dossier)")
            else:
                if not any(n.startswith("_internal/") for n in names):
                    if "_internal/ (dossier)" not in missing:
                        missing.append("_internal/ (dossier)")

            is_valid = len(missing) == 0 and len(forbidden) == 0
            return is_valid, missing, forbidden

    except zipfile.BadZipFile as exc:
        return False, [f"ZIP invalide/corrompu: {exc}"], []
    except Exception as exc:
        return False, [f"Erreur lecture ZIP: {exc}"], []


def validate_zip_strict(zip_path: str | Path) -> tuple[bool, list[str], list[str]]:
    """Validation stricte : le ZIP doit contenir Jarvis.exe et _internal/ à la racine,
    sans préfixe app/ (c'est le format attendu pour les updates)."""
    return validate_zip(zip_path, expect_app_prefix=False)


# ---------------------------------------------------------------------------
# Helpers pour logs
# ---------------------------------------------------------------------------

def format_validation_result(is_valid: bool, missing: list[str], forbidden: list[str]) -> str:
    """Formate un résultat de validation pour les logs CI."""
    lines: list[str] = []
    if is_valid:
        lines.append("✓ Validation OK")
    else:
        lines.append("✗ Validation ÉCHOUÉE")
    if missing:
        lines.append("  Fichiers manquants:")
        for m in missing:
            lines.append(f"    - {m}")
    if forbidden:
        lines.append("  Fichiers aplatis détectés (doivent être dans _internal/):")
        for f in forbidden:
            lines.append(f"    - {f}")
    return "\n".join(lines)


def assert_app_dir(app_path: str | Path, *, strict: bool = False) -> None:
    """Lève une exception si le dossier app est invalide (pour build)."""
    is_valid, missing, forbidden = validate_app_dir(app_path, strict=strict)
    if not is_valid:
        msg = f"Layout invalide pour {app_path}:\n" + format_validation_result(is_valid, missing, forbidden)
        raise RuntimeError(msg)


def assert_zip(zip_path: str | Path) -> None:
    """Lève une exception si le ZIP est invalide."""
    is_valid, missing, forbidden = validate_zip(zip_path)
    if not is_valid:
        msg = f"ZIP invalide {zip_path}:\n" + format_validation_result(is_valid, missing, forbidden)
        raise RuntimeError(msg)


def assert_install_dir(install_path: str | Path) -> None:
    """Lève une exception si l'installation est invalide."""
    is_valid, missing, forbidden = validate_install_dir(install_path)
    if not is_valid:
        msg = f"Installation invalide {install_path}:\n" + format_validation_result(is_valid, missing, forbidden)
        raise RuntimeError(msg)
