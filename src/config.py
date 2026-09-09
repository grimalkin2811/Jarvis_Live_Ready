"""Configuration de Jarvis.

L'application distribuée utilise un fichier ``config.json`` dans le dossier de
données utilisateur (voir ``src/settings.py``). Le développement continue de
pouvoir utiliser ``.env`` : ``load_config()`` lit d'abord ``.env``, puis laisse
le JSON (s'il existe) prendre le dessus.

``Config`` est conservé pour la compatibilité avec le reste de l'application ;
les champs mémoire sont lus depuis l'environnement ou les valeurs par défaut
(la mémoire, les routines et les rappels ont chacun leur propre gestionnaire qui
gère aussi la persistance, voir ``src/memory.py``, ``src/routines.py``,
``src/scheduler.py``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

from . import paths, settings
from .memory import DEFAULT_MEMORY_DB


@dataclass(frozen=True)
class Config:
    user: str
    api_key: str
    model: str
    memory_enabled: bool = True
    memory_database_path: str = DEFAULT_MEMORY_DB
    memory_max_results: int = 5
    memory_min_importance: int = 1


def _env_bool(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", "non"}


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(os.getenv(name, str(default)))))
    except Exception:
        return default


def _require(value: str, label: str, hint: str) -> str:
    if not value:
        raise RuntimeError(f"Configuration absente : {label}. {hint}")
    return value


def _migrate_if_needed() -> None:
    """Migre depuis ``.env`` vers ``config.json`` une seule fois (dev -> app)."""
    try:
        settings.migrate_from_env()
    except Exception:
        pass


def load_config() -> Config:
    """Charge la configuration effective (JSON prioritaire, ``.env`` en dev).

    Lève ``RuntimeError`` si la configuration est incomplète, avec un message
    orienté utilisateur final (pas besoin de comprendre Python).
    """
    load_dotenv()

    # Assistant de premier lancement : si aucun JSON n'existe encore mais
    # qu'un .env de développement est présent, on le migre silencieusement.
    if not settings.config_exists() and os.getenv(settings.API_KEY):
        _migrate_if_needed()

    cfg = settings.load_app_config()

    # Compat : la mémoire reste configurable via l'environnement / vars
    # (les gestionnaires de mémoire/routines/rappels ont leurs propres défauts).
    user = cfg.user or os.getenv("JARVIS_USER", "").strip()
    key = cfg.api_key or os.getenv(settings.API_KEY, "").strip()
    model = cfg.model or os.getenv("GEMINI_MODEL", "").strip()

    user = _require(user, "JARVIS_USER", "Relance l'assistant de configuration.")
    key = _require(key, settings.API_KEY, "Relance l'assistant de configuration.")
    model = _require(model, "GEMINI_MODEL", "Relance l'assistant de configuration.")

    memory_path = os.getenv("JARVIS_MEMORY_DATABASE_PATH", DEFAULT_MEMORY_DB).strip() or DEFAULT_MEMORY_DB

    return Config(
        user=user,
        api_key=key,
        model=model,
        memory_enabled=_env_bool("JARVIS_MEMORY_ENABLED", cfg.memory_enabled),
        memory_database_path=memory_path,
        memory_max_results=_env_int("JARVIS_MEMORY_MAX_RESULTS", cfg.memory_max_results, 1, 20),
        memory_min_importance=_env_int("JARVIS_MEMORY_MIN_IMPORTANCE", cfg.memory_min_importance, 1, 5),
    )


def save_config(config: Config) -> None:
    """Persiste la configuration dans ``config.json`` (application distribuée).

    Les valeurs enregistrées servent de référentiel d'installation ; elles
    seront relues au prochain lancement même sans ``.env``.
    """
    app_cfg = settings.AppConfig(
        user=config.user,
        api_key=config.api_key,
        model=config.model,
        memory_enabled=config.memory_enabled,
        memory_max_results=config.memory_max_results,
        memory_min_importance=config.memory_min_importance,
    )
    settings.save_app_config(app_cfg)


def config_path() -> str:
    """Chemin du fichier de configuration JSON."""
    return str(paths.config_file())


def is_configured() -> bool:
    """Vrai si une configuration utilisateur existe et est exploitable."""
    return settings.first_run_needed() is False
