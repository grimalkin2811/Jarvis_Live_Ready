"""Validation de la structure des builds et installations Jarvis.

Ce module est volontairement léger en dépendances : il sert aussi au launcher,
à l'updater et aux scripts de packaging.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

CRITICAL_APP_FILES = [
    "Jarvis.exe",
    "_internal/python311.dll",
    "_internal/base_library.zip",
]

FORBIDDEN_FLATTENED_FILES = ["python311.dll", "base_library.zip"]


def validate_app_dir(app_path: str | Path, strict: bool = False) -> tuple[bool, list[str], list[str]]:
    """Valide une arborescence PyInstaller onedir moderne."""
    base = Path(app_path)
    missing: list[str] = []
    forbidden: list[str] = []

    for rel in CRITICAL_APP_FILES:
        if not (base / rel).exists():
            missing.append(rel)

    for rel in FORBIDDEN_FLATTENED_FILES:
        if (base / rel).exists():
            forbidden.append(rel)

    return not missing and not forbidden, missing, forbidden


def validate_install_dir(install_path: str | Path) -> tuple[bool, list[str], list[str]]:
    """Valide une installation Jarvis contenant ``app/``."""
    base = Path(install_path)
    app_dir = base / "app"
    is_valid, missing, forbidden = validate_app_dir(app_dir)

    missing = [f"app/{item}" for item in missing]
    forbidden = [f"app/{item}" for item in forbidden]
    return is_valid, missing, forbidden


def _zip_has_flattened(zip_file: zipfile.ZipFile) -> list[str]:
    """Détecte les fichiers aplatis à la racine du ZIP."""
    found: list[str] = []
    names = [n.replace("\\", "/") for n in zip_file.namelist()]
    # Si le ZIP contient app/ comme dossier racine, on regarde à l'intérieur de app/
    # Sinon, on regarde à la racine
    has_app_prefix = any(n.startswith("app/") for n in names)

    for forbidden in FORBIDDEN_FLATTENED_FILES:
        # Si has_app_prefix, app/python311.dll est interdit.
        candidate = f"app/{forbidden}" if has_app_prefix else forbidden
        if candidate in names:
            found.append(candidate)
    return found


def validate_zip(zip_path: str | Path) -> tuple[bool, list[str], list[str]]:
    """Valide la structure critique d'une archive portable Jarvis."""
    path = Path(zip_path)
    try:
        with zipfile.ZipFile(path) as archive:
            names = {n.replace("\\", "/").rstrip("/") for n in archive.namelist()}
            prefix = "app/" if any(n.startswith("app/") for n in names) else ""

            missing = [
                f"{prefix}{rel}"
                for rel in CRITICAL_APP_FILES
                if f"{prefix}{rel}" not in names
            ]
            forbidden = _zip_has_flattened(archive)
            return not missing and not forbidden, missing, forbidden
    except (OSError, zipfile.BadZipFile) as exc:
        return False, [f"Invalid ZIP: {exc}"], []
