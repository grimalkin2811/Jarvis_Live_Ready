"""Création de fichiers ``.txt`` dans ``user_content``.

Le dossier est créé s'il n'existe pas. Le chemin vient de ``paths.user_content_dir``
(relatif à l'application) ou d'un dossier injecté par les tests. Aucun chemin
absolu de machine n'est codé en dur. Un fichier existant n'est jamais écrasé.
"""

from __future__ import annotations

import os
from pathlib import Path

from .. import paths
from .filenames import (
    MAX_COLLISIONS,
    explicit_filename_is_invalid,
    sanitize_filename,
)

FOLDER_NAME = "user_content"


def resolve_directory(directory: Path | str | None = None) -> Path:
    """Dossier cible, toujours résolu au moment de l'appel."""
    if directory is not None and str(directory).strip():
        return Path(directory).expanduser()
    override = os.environ.get("JARVIS_USER_CONTENT_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return paths.user_content_dir()


def ensure_directory(directory: Path | str | None = None) -> Path:
    """Crée ``user_content`` (ou le dossier demandé) s'il n'existe pas."""
    target = resolve_directory(directory)
    target.mkdir(parents=True, exist_ok=True)
    return target


def create_text_file(
    text: str,
    filename: str | None = None,
    *,
    directory: Path | str | None = None,
    derived_name: str | None = None,
) -> dict:
    """Écrit ``text`` dans un ``.txt`` unique. Ne lève pas : retourne un dict."""
    try:
        if filename and explicit_filename_is_invalid(filename):
            return {
                "success": False,
                "reason": "invalid_filename",
                "error": "Nom de fichier invalide.",
            }
        chosen = sanitize_filename(filename) if filename else sanitize_filename(derived_name or "document")
        target_dir = ensure_directory(directory)
        path = _write_unique(target_dir, chosen, text)
    except PermissionError:
        return {
            "success": False,
            "reason": "permission",
            "error": "Permission insuffisante pour créer le fichier.",
        }
    except OSError as exc:
        return {
            "success": False,
            "reason": "io_error",
            "error": f"Erreur de création de fichier : {exc}",
        }
    except Exception as exc:  # filet : une écriture ne doit jamais planter Jarvis
        return {
            "success": False,
            "reason": "exception",
            "error": str(exc),
        }
    return {
        "success": True,
        "reason": "created",
        "fichier": path.name,
        "dossier": FOLDER_NAME,
        "chemin": str(path),
        "chemin_relatif": f"{FOLDER_NAME}/{path.name}",
        "caracteres": len(text),
    }


def _write_unique(directory: Path, filename: str, text: str) -> Path:
    """Création exclusive : ``nom.txt``, puis ``nom_1.txt``, sans écrasement."""
    stem = sanitize_filename(filename)[:-4]
    for index in range(0, MAX_COLLISIONS + 1):
        name = f"{stem}.txt" if index == 0 else f"{stem}_{index}.txt"
        path = directory / name
        try:
            with open(path, "x", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            return path
        except FileExistsError:
            continue
    raise OSError("Impossible de trouver un nom de fichier libre.")
