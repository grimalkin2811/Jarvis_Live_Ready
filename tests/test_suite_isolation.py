"""La suite pytest ne touche JAMAIS les données réelles de l'utilisateur.

Audit pré-fusion (PR #50) : sans isolation, des tests de l'orbe écrivaient
``%LOCALAPPDATA%\\Jarvis\\ui\\*.json`` et ouvraient (donc migraient en v2) la
vraie ``memory.db``. ``tests/conftest.py`` redirige toute la session vers un
dossier temporaire ; ces tests le vérifient.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _isolated_root() -> Path:
    root = Path(os.environ["JARVIS_DATA_DIR"]).resolve()
    assert root.name.startswith("jarvis-tests-data-"), root
    assert Path(tempfile.gettempdir()).resolve() in root.parents, root
    return root


def test_default_data_paths_point_to_the_session_temporary_directory():
    from src import memory, paths, scheduler

    root = _isolated_root()
    for path in (paths.data_dir(), paths.ui_state_dir(), Path(paths.memory_db()), Path(paths.schedule_db()),
                 Path(memory.DEFAULT_MEMORY_DB), Path(scheduler.DEFAULT_SCHEDULE_DB)):
        assert root == Path(path).resolve() or root in Path(path).resolve().parents, path
    assert "JARVIS_MEMORY_DATABASE_PATH" not in os.environ


def test_default_memory_manager_never_opens_the_real_database():
    from src import memory

    previous = memory._DEFAULT_MANAGER
    memory.set_default_memory_manager(None)
    try:
        manager = memory.get_default_memory_manager()
        assert _isolated_root() in Path(manager.database_path).resolve().parents
    finally:
        memory.set_default_memory_manager(previous)


def test_a_legacy_cleanup_removing_the_variable():
    # Reproduit le nettoyage de certains tests historiques (pop au lieu de restaurer).
    os.environ.pop("JARVIS_DATA_DIR", None)


def test_isolation_is_restored_for_the_next_test():
    assert os.environ.get("JARVIS_DATA_DIR"), "le filet autouse rétablit l'isolation"
    _isolated_root()
