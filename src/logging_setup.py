"""Journalisation applicative de Jarvis.

Une application distribuée ne peut pas dépendre d'un terminal ouvert. Ce module
initialise un journal **à rotation** dans le dossier de données utilisateur
(``%LOCALAPPDATA%\\Jarvis\\logs\\jarvis.log`` sur Windows) et installe un
gestionnaire de dernier recours pour ne jamais perdre silencieusement une trace
de crash.

Tous les composants (Gemini, audio, OpenWakeWord, updater, config, scheduler…)
peuvent utiliser ``logging.getLogger(__name__)`` après un appel à
``setup_logging()``.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import sys
import traceback
from pathlib import Path

from . import paths

ROOT_LOGGER = "jarvis"
_FORMAT = "%(asctime)s %(levelname)-8s [%(name)s] %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"
_DEFAULT_LEVEL = "INFO"


def _level_from_env() -> int:
    name = os.getenv("JARVIS_LOG_LEVEL", _DEFAULT_LEVEL).strip().upper()
    return getattr(logging, name, logging.INFO)


def _log_path() -> Path:
    """Chemin du journal principal (surchargeable pour les tests)."""
    override = os.getenv("JARVIS_LOG_FILE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return paths.app_log_file()


def _log_dir() -> Path:
    return _log_path().parent


def setup_logging(
    level: int | str | None = None,
    filename: str | os.PathLike | None = None,
    console: bool = False,
    rotate: bool = True,
    max_bytes: int = 2 * 1024 * 1024,
    backup_count: int = 5,
) -> logging.Logger:
    """Configure le logger racine ``jarvis``."""
    logger = logging.getLogger(ROOT_LOGGER)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    # Ne pas empiler les handlers si déjà initialisé. Fermer explicitement les
    # anciens handlers : sous Windows, un FileHandler conserve le fichier
    # ouvert même après removeHandler(), ce qui bloque TemporaryDirectory.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    level = _level_from_env() if level is None else level
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
    target = _log_path() if filename is None else Path(filename).expanduser().resolve()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        file_handler: logging.Handler
        if rotate:
            file_handler = logging.handlers.RotatingFileHandler(
                target,
                maxBytes=max_bytes,
                backupCount=backup_count,
                encoding="utf-8",
            )
        else:
            file_handler = logging.FileHandler(target, encoding="utf-8")
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    except OSError as exc:
        logger.warning("Impossible d'ouvrir le journal %s : %s", target, exc)
        console = True

    if console:
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if not getattr(logger, "_jarvis_excepthook_installed", False):
        def _jarvis_excepthook(exc_type, exc_value, exc_tb):
            logging.getLogger(ROOT_LOGGER).critical(
                "Exception non interceptée", exc_info=(exc_type, exc_value, exc_tb)
            )
            sys.stderr.write("".join(traceback.format_exception(exc_type, exc_value, exc_tb)))

        sys.excepthook = _jarvis_excepthook
        logger._jarvis_excepthook_installed = True  # type: ignore[attr-defined]

    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Logger enfant du logger racine ``jarvis`` (``jarvis.audio``, etc.)."""
    return logging.getLogger(f"{ROOT_LOGGER}.{name}" if name else ROOT_LOGGER)


def log_exception(logger: logging.Logger, message: str, exc: BaseException | None = None) -> None:
    """Journalise une exception avec pile complète (informe sans masquer)."""
    if exc is not None:
        logger.error("%s: %s", message, exc, exc_info=True)
    else:
        logger.error(message)
