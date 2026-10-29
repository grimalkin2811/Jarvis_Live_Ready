"""Isolation des données réelles pour la suite de tests (pytest ET unittest).

Plusieurs tests créent l'orbe, le menu ou la mémoire, qui lisent/écrivent les
chemins PAR DÉFAUT de Jarvis (``%LOCALAPPDATA%\\Jarvis`` sous Windows :
réglages ``ui/``, ``memory.db`` — migrée depuis le schéma v2 —,
``schedule.db``, ``routines.json``). Toute la session de tests doit donc
pointer vers un dossier temporaire, AVANT tout import de ``src`` (les chemins
par défaut de ``src.memory``/``src.scheduler`` sont figés à l'import).

Appliqué par ``tests/conftest.py`` (pytest) et, depuis le correctif PR #50,
par ``tests/__init__.py`` UNIQUEMENT quand le programme principal est la
ligne de commande ``python -m unittest`` (qui ne charge jamais conftest.py).
Jamais lors d'un simple ``import tests.xxx`` depuis un script de validation
RÉELLE (voir la docstring de ``tests/__init__.py``).
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile

#: Chemins réels éventuellement fournis par l'environnement du développeur :
#: ignorés pendant les tests.
REAL_PATH_VARIABLES = (
    "JARVIS_MEMORY_DATABASE_PATH",
    "JARVIS_SCHEDULE_DATABASE_PATH",
    "JARVIS_TODO_DATABASE_PATH",
    "JARVIS_USER_CONTENT_DIR",
)

_APPLIED: dict[str, str] = {}


def apply_test_isolation() -> dict[str, str]:
    """Pose l'environnement isolé (idempotent) et renvoie les variables imposées."""
    if _APPLIED:
        return dict(_APPLIED)
    # Aucun téléchargement de modèle depuis le réseau pendant les tests.
    os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")
    # Faux serveur : marge courte pour la confirmation du rejeu de contexte
    # (5 s en production ; constante lue à l'import de src.gemini_live).
    os.environ.setdefault("JARVIS_LIVE_SEED_COMMIT_TIMEOUT", "1.5")
    root = tempfile.mkdtemp(prefix="jarvis-tests-data-")
    _APPLIED["JARVIS_DATA_DIR"] = root
    os.environ.update(_APPLIED)
    for variable in REAL_PATH_VARIABLES:
        os.environ.pop(variable, None)
    atexit.register(shutil.rmtree, root, True)
    return dict(_APPLIED)


def running_under_unittest_cli() -> bool:
    """Vrai si le programme principal est ``python -m unittest`` (discover ou modules)."""
    main = sys.modules.get("__main__")
    spec = getattr(main, "__spec__", None)
    return getattr(spec, "name", "") == "unittest.__main__"
