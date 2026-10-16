"""Cohérence PySide6 / Qt entre tests, build et binaire (PR #50).

La borne ``PySide6<6.12`` (arrêt « none_dealloc » de PySide6 6.12.0 sous
Linux/Python<3.12) est conservée par précaution : le binaire publié doit
embarquer la version que la CI teste. ``scripts/check_qt_runtime.py`` refuse un
build incohérent ; le Dashboard signale un binaire hors plage testée (comme
l'installation quotidienne construite avant la borne, Qt 6.12.0).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src import qt_versions  # noqa: E402


def test_validated_range_matches_requirements():
    spec = qt_versions.requirement_spec((ROOT / "requirements.txt").read_text(encoding="utf-8"))
    assert spec == qt_versions.VALIDATED_PYSIDE6_SPEC, "src/qt_versions.py et requirements.txt doivent évoluer ensemble"


@pytest.mark.parametrize("version,spec,expected", [
    ("6.11.2", "<6.12", True),
    ("6.12.0", "<6.12", False),
    ("6.12", "<6.12", False),
    ("6.11.2.1", "<6.12", True),
    ("6.9.0", ">=6.5,<6.12", True),
    ("6.4.3", ">=6.5,<6.12", False),
    ("6.11.2", "", True),
])
def test_version_comparison(version, spec, expected):
    assert qt_versions.satisfies(version, spec) is expected


def test_requirement_parsing_is_strict():
    assert qt_versions.requirement_spec("numpy\nPySide6<6.12  # borne\npsutil\n") == "<6.12"
    assert qt_versions.requirement_spec("PySide6-Addons<7\nPySide6\n") == ""
    assert qt_versions.requirement_spec("numpy\n") is None
    with pytest.raises(ValueError):
        qt_versions.requirement_spec('PySide6<6.12; python_version < "3.12"\n')


def test_check_flags_out_of_range_and_mixed_installations():
    assert qt_versions.check(versions={"pyside6": "6.11.2", "shiboken6": "6.11.2", "qt_runtime": "6.11.2"})["ok"]
    hors = qt_versions.check(versions={"pyside6": "6.12.0", "shiboken6": "6.12.0", "qt_runtime": "6.12.0"})
    assert not hors["ok"] and "hors de la plage testée" in hors["problems"][0]
    melange = qt_versions.check(versions={"pyside6": "6.11.2", "shiboken6": "6.11.2", "qt_runtime": "6.12.0"})
    assert not melange["ok"] and "installation mélangée" in melange["problems"][0]
    assert qt_versions.describe(hors).startswith("À VÉRIFIER : PySide6 6.12.0")


def test_current_environment_is_consistent():
    """L'environnement de test (même requirements que le build) est conforme."""
    result = qt_versions.check()
    assert result["ok"], result["problems"]


def test_build_check_refuses_a_requirement_that_differs_from_the_tested_range(tmp_path):
    script = ROOT / "scripts" / "check_qt_runtime.py"
    ok = subprocess.run([sys.executable, str(script), "--requirements", str(ROOT / "requirements.txt"),
                         "--out", str(tmp_path / "ok.json")], capture_output=True, text=True)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert json.loads((tmp_path / "ok.json").read_text(encoding="utf-8"))["ok"] is True
    autre = tmp_path / "requirements.txt"
    autre.write_text("PySide6\n", encoding="utf-8")  # borne retirée sans mettre à jour Jarvis
    ko = subprocess.run([sys.executable, str(script), "--requirements", str(autre)], capture_output=True, text=True)
    assert ko.returncode == 1
    assert "build refusé" in ko.stdout


def test_dashboard_diagnostics_report_the_validation_line():
    from UI.dashboard import diagnostics

    info = diagnostics.versions()
    assert info["pyside6_validation"].startswith("conforme")
