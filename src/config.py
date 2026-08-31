import os
from dataclasses import dataclass

from dotenv import load_dotenv

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


def load_config() -> Config:
    load_dotenv()

    user = os.getenv("JARVIS_USER", "").strip()
    key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "").strip()

    if not user:
        raise RuntimeError("Configuration absente : JARVIS_USER. Relance setup.bat.")
    if not key:
        raise RuntimeError("Configuration absente : GEMINI_API_KEY. Relance setup.bat.")
    if not model:
        raise RuntimeError("Configuration absente : GEMINI_MODEL. Relance setup.bat.")

    return Config(
        user=user,
        api_key=key,
        model=model,
        memory_enabled=_env_bool("JARVIS_MEMORY_ENABLED", True),
        memory_database_path=os.getenv("JARVIS_MEMORY_DATABASE_PATH", DEFAULT_MEMORY_DB).strip() or DEFAULT_MEMORY_DB,
        memory_max_results=_env_int("JARVIS_MEMORY_MAX_RESULTS", 5, 1, 20),
        memory_min_importance=_env_int("JARVIS_MEMORY_MIN_IMPORTANCE", 1, 1, 5),
    )
