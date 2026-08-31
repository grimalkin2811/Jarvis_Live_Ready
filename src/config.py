import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    user: str
    api_key: str
    model: str


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

    return Config(user, key, model)
