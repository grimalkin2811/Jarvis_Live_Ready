"""Configuration applicative de Jarvis pour l'application distribuée.

L'application compilée n'utilise pas ``.env`` : elle lit et écrit un fichier
``config.json`` dans le dossier de données utilisateur
(``%LOCALAPPDATA%\\Jarvis\\config\\config.json`` sur Windows). Ce fichier est
créé au **premier lancement** par l'assistant de configuration et **préservé**
lors des mises à jour.

En développement, ``.env`` reste supporté : ``load_config()`` le lit d'abord
puis laisse le fichier JSON (s'il existe) prendre le dessus.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, fields
from pathlib import Path

from . import paths

#: Nom du fichier de configuration géré par l'assistant de premier lancement.
CONFIG_FILENAME = "config.json"

#: Clé API Gemini (masquée dans les journaux).
API_KEY = "GEMINI_API_KEY"


@dataclass
class AppConfig:
    """Configuration persistante de l'application distribuée."""

    user: str = ""
    api_key: str = ""
    model: str = "gemini-2.5-flash-native-audio-preview-12-2025"
    memory_enabled: bool = True
    memory_max_results: int = 5
    memory_min_importance: int = 1
    routines_enabled: bool = True
    reminders_enabled: bool = True
    modes_enabled: bool = True

    def is_configured(self) -> bool:
        """Vrai quand la configuration est utilisable (nom + clé API)."""
        return bool(self.user.strip()) and bool(self.api_key.strip())


def _safe_int(value, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _safe_bool(value, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "non"}


def _parse_env_config() -> dict:
    """Lit une configuration depuis l'environnement / ``.env`` (dev)."""
    env = {
        "user": os.getenv("JARVIS_USER", "").strip(),
        "api_key": os.getenv(API_KEY, "").strip(),
        "model": os.getenv("JARVIS_MODEL", os.getenv("GEMINI_MODEL", "")).strip(),
        "memory_enabled": _safe_bool(os.getenv("JARVIS_MEMORY_ENABLED"), True),
        "memory_max_results": _safe_int(os.getenv("JARVIS_MEMORY_MAX_RESULTS"), 5, 1, 20),
        "memory_min_importance": _safe_int(os.getenv("JARVIS_MEMORY_MIN_IMPORTANCE"), 1, 1, 5),
        "routines_enabled": _safe_bool(os.getenv("JARVIS_ROUTINES_ENABLED"), True),
        "reminders_enabled": _safe_bool(os.getenv("JARVIS_REMINDERS_ENABLED"), True),
        "modes_enabled": _safe_bool(os.getenv("JARVIS_MODES_ENABLED"), True),
    }
    return {k: v for k, v in env.items() if v not in (None, "")}


def config_path(path: str | os.PathLike | None = None) -> Path:
    """Chemin du fichier de configuration (surchargeable pour les tests)."""
    if path:
        return Path(path).expanduser().resolve()
    return paths.config_file()


def config_exists(path: str | os.PathLike | None = None) -> bool:
    """Vrai si un fichier de configuration JSON existe."""
    return config_path(path).is_file()


def load_file_config(path: str | os.PathLike | None = None) -> dict:
    """Lit le fichier JSON de configuration s'il existe, sinon ``{}``."""
    target = config_path(path)
    if not target.is_file():
        return {}
    try:
        with target.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError):
        return {}


def save_file_config(payload: dict, path: str | os.PathLike | None = None) -> Path:
    """Écrit la configuration dans le fichier JSON (crée le dossier)."""
    target = config_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, ensure_ascii=False)
    return target


def load_config_dict(path: str | os.PathLike | None = None) -> dict:
    """Retourne la configuration effective (JSON, puis environnement en dev).

    Le fichier JSON est prioritaire car c'est la configuration "réelle" de
    l'application distribuée. En dev, s'il n'y a pas encore de JSON, on retombe
    sur ``.env`` / variables d'environnement.
    """
    data = load_file_config(path)
    env_data = _parse_env_config()
    merged = dict(env_data)
    merged.update({k: v for k, v in data.items() if v not in (None, "")})
    return merged


def load_app_config(path: str | os.PathLike | None = None) -> AppConfig:
    """Construit un ``AppConfig`` à partir de la configuration effective."""
    data = load_config_dict(path)
    cfg = AppConfig()
    cfg.user = str(data.get("user", cfg.user)).strip()
    cfg.api_key = str(data.get("api_key", cfg.api_key)).strip()
    cfg.model = str(data.get("model", cfg.model)).strip() or cfg.model
    cfg.memory_enabled = _safe_bool(data.get("memory_enabled"), True)
    cfg.memory_max_results = _safe_int(data.get("memory_max_results"), 5, 1, 20)
    cfg.memory_min_importance = _safe_int(data.get("memory_min_importance"), 1, 1, 5)
    cfg.routines_enabled = _safe_bool(data.get("routines_enabled"), True)
    cfg.reminders_enabled = _safe_bool(data.get("reminders_enabled"), True)
    cfg.modes_enabled = _safe_bool(data.get("modes_enabled"), True)
    return cfg


def save_app_config(cfg: AppConfig, path: str | os.PathLike | None = None) -> Path:
    """Sauvegarde un ``AppConfig`` dans le fichier JSON."""
    payload = asdict(cfg)
    return save_file_config(payload, path)


def migrate_from_env(path: str | os.PathLike | None = None) -> AppConfig | None:
    """Migre une configuration ``.env`` de développement vers ``config.json``.

    Retourne la configuration migrée si un ``.env`` fournit des valeurs utiles
    et qu'aucun JSON n'existe déjà, sinon ``None``.
    """
    if config_exists(path):
        return None
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass
    env_data = _parse_env_config()
    if not env_data or not env_data.get("user") or not env_data.get("api_key"):
        return None
    cfg = load_app_config(path)
    cfg.user = env_data.get("user", cfg.user)
    cfg.api_key = env_data.get("api_key", cfg.api_key)
    cfg.model = env_data.get("model", cfg.model)
    save_app_config(cfg, path)
    return cfg


def first_run_needed(path: str | os.PathLike | None = None) -> bool:
    """Vrai si l'application doit passer par l'assistant de premier lancement."""
    if config_exists(path):
        return not load_app_config(path).is_configured()
    return not bool(load_config_dict(path).get("api_key"))


def validate_api_key(raw: str) -> str:
    """Nettoie et valide sommairement une clé API fournie par l'utilisateur."""
    key = str(raw or "").strip()
    if not key:
        return key
    # Les clés Gemini font au moins ~20 caractères ; on retire les espaces
    # accidentels et les guillemets collés.
    key = key.replace('"', "").replace("'", "").strip()
    if len(key) < 20:
        return key
    return key


def _field_defaults() -> dict:
    return {f.name: f.default for f in fields(AppConfig)}
