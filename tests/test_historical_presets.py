"""Macros historiques : aucune application, notification, minuterie ou requête réelle."""
from unittest.mock import Mock

import pytest

from src import modes, routines, tools
from src.routine_presets import builtin_routines

HISTORICAL_IDS = {
    "work_setup", "morning_dashboard", "pomodoro_timer", "meeting_timer", "coffee_timer", "music_break",
    "news_reading", "system_review", "leaving_desk", "cinema_setup", "quiet_night", "guided_maintenance",
}


def historical():
    return [p for p in builtin_routines() if p["preset_id"] in HISTORICAL_IDS]


@pytest.fixture
def registry(monkeypatch):
    calls = []
    for name in {step["tool"] for p in historical() for step in p["steps"]}:
        def callback(_name=name, **kwargs):
            calls.append((_name, kwargs))
            return {"success": True}
        monkeypatch.setitem(tools.TOOL_FUNCTIONS, name, tools._guard_tool_function(name, callback))
    manager = Mock()
    manager.block_for_tool.return_value = None
    monkeypatch.setattr(tools, "get_default_mode_manager", lambda: manager)
    routines.set_tool_registry(tools.TOOL_FUNCTIONS, tools.TOOL_DECLARATIONS)
    yield calls, manager
    routines.set_tool_registry(tools.TOOL_FUNCTIONS, tools.TOOL_DECLARATIONS)


def test_catalogue_has_twelve_unique_new_complete_macros():
    presets = historical()
    assert len(presets) == len(HISTORICAL_IDS) == 12
    assert len({str(p["steps"]) for p in presets}) == len(presets)
    for p in presets:
        assert p["description"] and not p["enabled"] and p["schedule"] is None
        for step in p["steps"]:
            if step["tool"] == "open_website":
                assert step["args"]["site"] in tools.SITES
            if step["tool"] == "open_application":
                assert step["args"]["application"] in tools.APPS
            assert step["tool"] not in routines.FORBIDDEN_TOOLS


@pytest.mark.parametrize("preset", historical(), ids=lambda p: p["preset_id"])
def test_disabled_activation_execution_and_non_resurrection(tmp_path, registry, preset):
    calls, _ = registry
    manager = routines.RoutineManager(tmp_path / "routines.json")
    assert not manager.run_routine(preset["name"])["success"]
    assert calls == []
    assert manager.update_routine(preset["name"], enabled=True)["success"]
    assert calls == []  # Un clic d'activation ne lance jamais d'effet système.
    assert manager.scheduled_routines() == []
    assert manager.run_routine(preset["name"])["success"]
    assert calls == [(s["tool"], s["args"]) for s in preset["steps"]]
    assert manager.delete_routine(preset["name"], confirm=True)["success"]
    reopened = routines.RoutineManager(manager.path)
    assert not reopened.describe_routine(preset["name"])["success"]


def test_tool_failure_is_reported_and_mode_guard_blocks_all_steps(tmp_path, registry, monkeypatch):
    calls, mode = registry
    manager = routines.RoutineManager(tmp_path / "routines.json")
    manager.update_routine("Poste de travail", enabled=True)
    mode.block_for_tool.return_value = {"success": False, "error": "game mode"}
    assert not manager.run_routine("Poste de travail")["success"]
    assert calls == []
    mode.block_for_tool.return_value = None
    monkeypatch.setitem(tools.TOOL_FUNCTIONS, "open_application", lambda **kw: {"success": False, "error": "absent"})
    result = manager.run_routine("Poste de travail")
    assert not result["success"]
    assert result["echecs"][0]["outil"] == "open_application"
    # Le moteur actuel continue les étapes indépendantes et rapporte les échecs.
    assert [name for name, _ in calls] == ["open_website", "set_volume"]


def test_existing_custom_name_is_not_replaced_and_old_removals_survive(tmp_path):
    import json
    path = tmp_path / "routines.json"
    payload = {"version": 1, "installed_presets": ["hydration"], "routines": [
        {"name": "Poste de travail", "enabled": False, "steps": [{"tool": "flip_coin", "args": {}}]},
    ]}
    path.write_text(json.dumps(payload), encoding="utf-8")
    manager = routines.RoutineManager(path)
    stored = next(p for p in json.loads(path.read_text(encoding="utf-8"))["routines"] if p["name"] == "Poste de travail")
    assert stored["steps"] == payload["routines"][0]["steps"]
    assert not manager.describe_routine("Hydratation")["success"]
    before = path.read_bytes()
    manager.install_presets()
    assert path.read_bytes() == before
