"""Emplacements de fichiers pour Jarvis (données applicatives et utilisateur).

Ce module centralise TOUTES les décisions de chemins de sorte qu'une application
distribuée ne suppose jamais qu'elle est lancée depuis le répertoire du dépôt ni
qu'un ``.venv`` existe sur la machine de l'utilisateur.

Principes (voir la consigne ``CODE / APPLICATION`` vs ``DONNÉES UTILISATEUR``) :

* Le **code** (l'exécutable, son bundle Python, ses ressources embarquées) vit
  dans le dossier d'installation et peut être **remplacé** à chaque mise à jour.
* Les **données utilisateur** (config, mémoire, logs, routines, préférences UI…)
  vivent dans ``%LOCALAPPDATA%\\Jarvis`` sur Windows et sont **préservées** lors
  d'une mise à jour. Aucun fichier de données n'est écrit dans le dossier
  d'installation.

En développement (Linux/macOS ou ``--dev``), les données restent dans
``~/.jarvis`` par défaut, ou dans le dossier indiqué par ``JARVIS_DATA_DIR``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

#: Nom de l'application (répertoire de données).
APP_NAME = "Jarvis"

#: Nom de l'organisation, utilisé pour les clés de registre / préférences.
ORGANIZATION = "Jarvis"


def is_frozen() -> bool:
    """Vrai quand le code tourne dans un bundle PyInstaller (``.exe``)."""
    return bool(getattr(sys, "frozen", False))


def _is_windows() -> bool:
    return os.name == "nt"


def _base_dir() -> Path:
    """Répertoire de base du programme.

    * En bundle PyInstaller : le dossier de l'exécutable (``sys.executable``).
    * En développement : la racine du dépôt (répertoire parent de ``src/``).
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    # src/paths.py -> repository root
    return Path(__file__).resolve().parent.parent


def app_dir() -> Path:
    """Répertoire d'installation de l'application (code remplaçable).

    En bundle : le dossier de l'exécutable. En dev : la racine du dépôt.
    """
    return _base_dir()


def _user_root() -> Path:
    """Racine des données utilisateur.

    Priorité :
    1. ``JARVIS_DATA_DIR`` (variable d'environnement, pour dev / tests / override) ;
    2. ``%LOCALAPPDATA%\\Jarvis`` sur Windows ;
    3. ``~/.jarvis`` autrement.
    """
    override = os.environ.get("JARVIS_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if _is_windows():
        local = os.environ.get("LOCALAPPDATA", "").strip()
        if local:
            return Path(local) / APP_NAME
        # Fallback sans LOCALAPPDATA : %APPDATA% puis profil.
        appdata = os.environ.get("APPDATA", "").strip()
        if appdata:
            return Path(appdata) / APP_NAME
        return Path.home() / f".{APP_NAME.lower()}"
    return Path.home() / f".{APP_NAME.lower()}"


def data_dir() -> Path:
    """Répertoire racine des données utilisateur (jamais remplacé)."""
    return _user_root()


def config_dir() -> Path:
    """Répertoire de la configuration utilisateur."""
    return data_dir() / "config"


def logs_dir() -> Path:
    """Répertoire des journaux applicatifs."""
    return data_dir() / "logs"


def models_dir() -> Path:
    """Répertoire des modèles téléchargés au premier lancement (OpenWakeWord)."""
    return data_dir() / "models"


def openwakeword_models_dir() -> Path:
    """Répertoire des modèles OpenWakeWord (téléchargés à la demande)."""
    return models_dir() / "openwakeword"


def ui_state_dir() -> Path:
    """Répertoire des fichiers d'état de l'interface (menu, apparence, système)."""
    return data_dir() / "ui"


def cache_dir() -> Path:
    """Répertoire de cache temporaire."""
    return data_dir() / "cache"


# ---------------------------------------------------------------------------
# Fichiers de configuration
# ---------------------------------------------------------------------------

def config_file() -> Path:
    """Fichier de configuration JSON de l'application distribuée."""
    return config_dir() / "config.json"


def memory_db() -> Path:
    """Base SQLite de la mémoire persistante.

    Conservée à la racine du dossier de données pour préserver la compatibilité
    avec les anciennes installations (``~/.jarvis/memory.db``).
    """
    return data_dir() / "memory.db"


def schedule_db() -> Path:
    """Base SQLite des rappels persistants.

    Conservée à la racine du dossier de données (compatibilité : 
    ``~/.jarvis/schedule.db``).
    """
    return data_dir() / "schedule.db"


def routines_file() -> Path:
    """Fichier JSON des routines utilisateur.

    Conservé à la racine du dossier de données (compatibilité :
    ``~/.jarvis/routines.json``).
    """
    return data_dir() / "routines.json"


def modes_file() -> Path:
    """Fichier JSON de l'état des modes.

    Conservé à la racine du dossier de données (compatibilité :
    ``~/.jarvis/mode.json``).
    """
    return data_dir() / "mode.json"


def notes_file() -> Path:
    """Fichier JSON des notes utilisateur."""
    return data_dir() / "notes.json"


def menu_state_file() -> Path:
    """Fichier d'état du menu radial (préférences vocales / UI)."""
    return ui_state_dir() / "menu_state.json"


def appearance_state_file() -> Path:
    """Fichier d'état d'apparence de l'orbe."""
    return ui_state_dir() / "appearance_state.json"


def system_state_file() -> Path:
    """Fichier d'état système (mode de réponse)."""
    return ui_state_dir() / "system_state.json"


def debug_log_file() -> Path:
    """Journal de débogage de l'orbe."""
    return ui_state_dir() / "jarvis_debug.log"


def app_log_file() -> Path:
    """Journal principal de l'application."""
    return logs_dir() / "jarvis.log"


# ---------------------------------------------------------------------------
# Utilitaires
# ---------------------------------------------------------------------------

def ensure_dir(path: Path | str) -> Path:
    """Crée le dossier parent (et tous ses parents) s'il n'existe pas."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def ensure_data_dirs() -> None:
    """Crée l'arborescence de données utilisateur au démarrage."""
    for directory in (
        data_dir(),
        config_dir(),
        logs_dir(),
        models_dir(),
        ui_state_dir(),
        cache_dir(),
    ):
        directory.mkdir(parents=True, exist_ok=True)


def resource_dir(name: str | None = None) -> Path:
    """Répertoire des ressources embarquées du programme.

    En bundle PyInstaller, les ``datas`` sont extraites dans ``sys._MEIPASS``.
    En dev, le dossier ``resources/`` du dépôt est utilisé.
    """
    base = Path(getattr(sys, "_MEIPASS", None)) if is_frozen() else app_dir() / "resources"
    if name:
        return base / name
    return base
