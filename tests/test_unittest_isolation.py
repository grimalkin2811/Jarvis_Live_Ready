"""``python -m unittest`` isole les données réelles comme pytest (correctif PR #50).

Avant : seul pytest chargeait ``tests/conftest.py`` ; un
``python -m unittest discover tests`` lancé sur un PC exécutait les tests de
l'orbe, du menu et de la mémoire sur les VRAIES données de Jarvis.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _env(tmp_path: Path) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("JARVIS_")}
    reel = tmp_path / "donnees_reelles_simulees"
    reel.mkdir()
    env.update({"JARVIS_DATA_DIR": str(reel), "JARVIS_MEMORY_DATABASE_PATH": str(reel / "memory.db"),
                "QT_QPA_PLATFORM": "offscreen", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


def test_unittest_cli_redirects_jarvis_data_to_a_temporary_folder(tmp_path):
    env = _env(tmp_path)
    result = subprocess.run([sys.executable, "-m", "unittest", "-q", "tests.isolation_probe"], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any((tmp_path / "donnees_reelles_simulees").iterdir()), "rien d'écrit dans les « vraies » données"


def test_plain_import_of_the_tests_package_changes_nothing(tmp_path):
    """Scripts de validation RÉELLE (``from tests.real_gemini_harness import ...``) : aucun override."""
    env = _env(tmp_path)
    code = "import os, tests; print(os.environ['JARVIS_DATA_DIR']); print(os.environ.get('JARVIS_MEMORY_DATABASE_PATH'))"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    lignes = result.stdout.split()
    assert lignes[0] == env["JARVIS_DATA_DIR"]
    assert lignes[1] == env["JARVIS_MEMORY_DATABASE_PATH"]
