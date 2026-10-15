"""Préférences du Dashboard : versions, migrations, validation, reprise."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from UI.dashboard import CONFIG_SCHEMA_VERSION
from UI.dashboard.config import (
    ConfigStore,
    DashboardConfig,
    add_custom_page,
    apply_preset,
    delete_custom_page,
    load_config,
    move_page,
    rename_page,
    save_config,
    save_custom_snapshot,
)
from UI.dashboard.layout_model import MAX_CUSTOM_PAGES
from UI.dashboard.presets import BUILTIN_PAGE_ORDER, template_widget_types


def test_missing_file_gives_defaults_with_all_builtin_pages(tmp_path):
    result = load_config(tmp_path / "absent.json")
    assert result.status == "defaults"
    assert [p.id for p in result.config.layout.pages] == list(BUILTIN_PAGE_ORDER)
    assert result.config.appearance.theme == "jarvis"
    assert result.config.close.mode == "persistent"


def test_round_trip_preserves_everything(tmp_path):
    path = tmp_path / "state.json"
    config = DashboardConfig.defaults()
    config.appearance.theme = "edith"
    config.appearance.accent = "#ff8800"
    config.appearance.animation_level = "pousse"
    config.layout.view_mode = "onepage"
    config.close.mode = "interactive"
    config.monitoring.interval_s = 5
    config.notifications.categories["task_failed"]["native"] = False
    config.security.confirm["task.retry"] = False
    config.general.open_on_launch = True
    page, _ = add_custom_page(config, "Projets")
    assert page is not None
    assert save_config(config, path)
    loaded = load_config(path)
    assert loaded.status == "loaded"
    c = loaded.config
    assert c.appearance.theme == "edith" and c.appearance.accent == "#ff8800"
    assert c.appearance.animation_level == "pousse"
    assert c.layout.view_mode == "onepage"
    assert c.close.mode == "interactive"
    assert c.monitoring.interval_s == 5
    assert c.notifications.wants("task_failed", "native") is False
    assert c.security.confirm == {"task.retry": False}
    assert c.general.open_on_launch is True
    assert c.layout.page(page.id).title == "Projets"


def test_unknown_keys_are_preserved_everywhere(tmp_path):
    path = tmp_path / "state.json"
    config = DashboardConfig.defaults()
    raw = config.to_dict()
    raw["future_root"] = {"x": 1}
    raw["appearance"]["future_field"] = "keep"
    raw["layout"]["pages"][0]["future_page_key"] = [1, 2]
    raw["layout"]["pages"][0]["widgets"][0]["future_widget_key"] = True
    raw["layout"]["pages"][0]["widgets"][0]["settings"]["unknown_setting"] = 7
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_config(path).config
    assert save_config(loaded, path)
    again = json.loads(path.read_text(encoding="utf-8"))
    assert again["future_root"] == {"x": 1}
    assert again["appearance"]["future_field"] == "keep"
    assert again["layout"]["pages"][0]["future_page_key"] == [1, 2]
    assert again["layout"]["pages"][0]["widgets"][0]["future_widget_key"] is True
    assert again["layout"]["pages"][0]["widgets"][0]["settings"]["unknown_setting"] == 7


def test_corrupted_file_is_backed_up_never_silently_deleted(tmp_path):
    path = tmp_path / "dashboard_state.json"
    path.write_text("{ ceci n'est pas du JSON", encoding="utf-8")
    result = load_config(path)
    assert result.status == "recovered"
    assert result.backup_path and os.path.exists(result.backup_path)
    assert Path(result.backup_path).read_text(encoding="utf-8") == "{ ceci n'est pas du JSON"
    assert [p.id for p in result.config.layout.pages] == list(BUILTIN_PAGE_ORDER)
    assert any("corrompu" in note for note in result.notes)


def test_non_object_root_is_treated_as_corruption(tmp_path):
    path = tmp_path / "dashboard_state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    result = load_config(path)
    assert result.status == "recovered"
    assert result.backup_path


def test_invalid_values_are_repaired_without_blocking(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "appearance": {"theme": "matrix", "glow": 999, "panel_opacity": -5, "accent": "rouge", "density": "x"},
        "close": {"mode": "explode"},
        "monitoring": {"interval_s": "abc", "cpu_threshold": 10},
        "layout": {"view_mode": "grid", "pages": "pas une liste"},
        "window": {"geometry": "<script>"},
    }), encoding="utf-8")
    result = load_config(path)
    c = result.config
    assert result.status == "repaired"
    assert c.appearance.theme == "jarvis"
    assert c.appearance.glow == 100 and c.appearance.panel_opacity == 40
    assert c.appearance.accent is None and c.appearance.density == "normal"
    assert c.close.mode == "persistent"
    assert c.monitoring.interval_s == 2 and c.monitoring.cpu_threshold == 50
    assert c.layout.view_mode == "multipage"
    assert c.window.geometry == ""
    assert [p.id for p in c.layout.pages] == list(BUILTIN_PAGE_ORDER)


def test_migration_from_v0_moves_flat_keys(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"theme": "friday", "view_mode": "onepage", "custom": 1}), encoding="utf-8")
    result = load_config(path)
    assert result.config.appearance.theme == "friday"
    assert result.config.layout.view_mode == "onepage"
    assert result.config.extras["custom"] == 1
    assert any("migrées" in note for note in result.notes)
    assert result.config.to_dict()["schema_version"] == CONFIG_SCHEMA_VERSION


def test_newer_schema_is_loaded_and_version_kept(tmp_path):
    path = tmp_path / "state.json"
    raw = DashboardConfig.defaults().to_dict()
    raw["schema_version"] = CONFIG_SCHEMA_VERSION + 5
    raw["nouvelle_section"] = {"a": 1}
    path.write_text(json.dumps(raw), encoding="utf-8")
    result = load_config(path)
    assert result.status == "newer"
    assert save_config(result.config, path)
    again = json.loads(path.read_text(encoding="utf-8"))
    assert again["schema_version"] == CONFIG_SCHEMA_VERSION + 5
    assert again["nouvelle_section"] == {"a": 1}


def test_missing_builtin_pages_are_restored_custom_pages_kept(tmp_path):
    config = DashboardConfig.defaults()
    add_custom_page(config, "Perso")
    raw = config.to_dict()
    raw["layout"]["pages"] = [p for p in raw["layout"]["pages"] if p["id"] not in ("memoire", "systeme")]
    path = tmp_path / "state.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    result = load_config(path)
    ids = [p.id for p in result.config.layout.pages]
    assert "memoire" in ids and "systeme" in ids
    assert any(not p.builtin and p.title == "Perso" for p in result.config.layout.pages)


def test_unknown_widget_types_are_never_dropped(tmp_path):
    config = DashboardConfig.defaults()
    raw = config.to_dict()
    raw["layout"]["pages"][0]["widgets"].append({"id": "w-x", "type": "future.widget", "x": 0, "y": 30, "w": 3, "h": 3})
    path = tmp_path / "state.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    loaded = load_config(path).config
    assert any(w.type == "future.widget" for w in loaded.layout.pages[0].widgets)


def test_presets_apply_and_custom_snapshot_restores(tmp_path):
    config = DashboardConfig.defaults()
    home = config.layout.page("accueil")
    home.title = "Maison"
    home.widgets = home.widgets[:2]
    save_custom_snapshot(config)
    assert config.layout.preset == "personnalisee"
    custom_page, _ = add_custom_page(config, "Projets")
    ok, _ = apply_preset(config, "diagnostic")
    assert ok and config.layout.preset == "diagnostic"
    assert config.layout.page("accueil").title == "Maison"  # titre utilisateur conservé
    assert any(w.type == "system.diagnostics" for w in config.layout.page("accueil").widgets)
    assert config.layout.page(custom_page.id) is not None  # page perso intacte
    ok, _ = apply_preset(config, "personnalisee")
    assert ok
    assert len(config.layout.page("accueil").widgets) == 2
    assert config.layout.page(custom_page.id) is not None


def test_custom_preset_without_snapshot_is_refused():
    config = DashboardConfig.defaults()
    ok, message = apply_preset(config, "personnalisee")
    assert not ok and "Aucune" in message


def test_custom_pages_crud_and_limits():
    config = DashboardConfig.defaults()
    page, _ = add_custom_page(config, "  Mon   tableau  ")
    assert page.title == "Mon tableau"
    assert add_custom_page(config, "   ")[0] is None
    assert rename_page(config, page.id, "Renommée")[0]
    assert config.layout.page(page.id).title == "Renommée"
    assert move_page(config, page.id, -1)
    ok, message = delete_custom_page(config, "accueil")
    assert not ok and "intégrées" in message
    assert delete_custom_page(config, page.id)[0]
    assert config.layout.page(page.id) is None
    for index in range(MAX_CUSTOM_PAGES):
        assert add_custom_page(config, f"P{index}")[0] is not None
    assert add_custom_page(config, "Trop")[0] is None


def test_store_is_read_only_when_corrupt_file_cannot_be_backed_up(tmp_path, monkeypatch):
    path = tmp_path / "dashboard_state.json"
    path.write_text("corrompu", encoding="utf-8")
    monkeypatch.setattr("UI.dashboard.config.shutil.copy2", lambda *a, **k: (_ for _ in ()).throw(OSError("disque")))
    store = ConfigStore(path)
    assert store.read_only
    assert store.save() is False
    assert path.read_text(encoding="utf-8") == "corrompu"


def test_store_save_failure_keeps_preferences_in_memory(tmp_path, monkeypatch):
    store = ConfigStore(tmp_path / "state.json")
    store.config.appearance.theme = "friday"
    monkeypatch.setattr("UI.dashboard.config.save_dict", lambda *a, **k: False)
    assert store.save() is False
    assert store.last_save_ok is False
    assert store.config.appearance.theme == "friday"


def test_presets_only_reference_registered_widgets():
    from UI.dashboard.registry import default_registry

    registry = default_registry()
    missing = sorted(t for t in template_widget_types() if registry.spec(t) is None)
    assert missing == []


@pytest.mark.parametrize("preset", ["cockpit", "productivite", "diagnostic"])
def test_presets_have_no_overlap(preset):
    from UI.dashboard.layout_model import has_overlap
    from UI.dashboard.presets import builtin_pages

    for page in builtin_pages(preset):
        assert not has_overlap(page.widgets), (preset, page.id)
        for placement in page.widgets:
            assert 0 <= placement.x and placement.x + placement.w <= 12, (preset, page.id, placement.type)
