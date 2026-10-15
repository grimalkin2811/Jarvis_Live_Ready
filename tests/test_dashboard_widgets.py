"""Contrat des widgets : registre, cycle de vie, libération, état partagé."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.dashboard_support import isolated_jarvis, pump, wait_until  # noqa: E402
from UI.dashboard.config import ConfigStore  # noqa: E402
from UI.dashboard.hub import DashboardHub  # noqa: E402
from UI.dashboard.layout_model import Placement  # noqa: E402
from UI.dashboard.registry import WidgetRegistry, default_registry, describe  # noqa: E402
from UI.dashboard.theme import ThemeManager  # noqa: E402
from UI.dashboard.widget_api import STATES, SettingField, WidgetContext, WidgetSpec  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def env(app, tmp_path):
    with isolated_jarvis() as data:
        store = ConfigStore(tmp_path / "state.json")
        hub = DashboardHub(store)
        hub.start()
        theme = ThemeManager(store, reduced_motion_probe=lambda: False)
        yield {"app": app, "store": store, "hub": hub, "theme": theme, "data": data}
        hub.shutdown()


def _context(env, instance_id="w-test"):
    return WidgetContext(hub=env["hub"], theme_manager=env["theme"], instance_id=instance_id,
                         confirm=lambda *a: True, open_path=lambda p: True)


def test_registry_contract_is_valid_and_complete():
    registry = default_registry()
    specs = registry.specs()
    assert len(specs) >= 20
    ids = [s.type_id for s in specs]
    assert len(ids) == len(set(ids))
    for spec in specs:
        assert spec.contract_errors() == [], spec.type_id
    required = {"jarvis.state", "jarvis.conversation", "tasks.background", "system.activity", "jarvis.models",
                "system.resources", "productivity.notes", "productivity.documents", "jarvis.quick_actions",
                "jarvis.audio", "memory.browser", "system.notifications", "system.diagnostics", "custom.panel"}
    assert required <= set(ids)
    assert describe(registry)[0]["type_id"]


def test_invalid_specs_are_refused():
    registry = WidgetRegistry()
    bad = WidgetSpec("sans-point", "", "", "inconnue", lambda c, s: None, (5, 5), (3, 3))
    with pytest.raises(ValueError):
        registry.register(bad)


def test_settings_validation_keeps_unknown_keys():
    spec = default_registry().spec("system.activity")
    data = spec.validate_settings({"level": "bizarre", "futur": 1})
    assert data["level"] == "info" and data["futur"] == 1
    field = SettingField("n", "N", "int", 5, minimum=1, maximum=9)
    assert field.validate("x") == 5 and field.validate(99) == 9


def test_every_widget_builds_activates_and_releases_everything(env):
    registry = default_registry()
    app = env["app"]
    for spec in registry.specs():
        placement = Placement(f"w-{spec.type_id}", spec.type_id, 0, 0, *spec.default_size,
                              settings=spec.validate_settings({}))
        widget = registry.create(placement, _context(env, placement.id))
        assert widget is not None, spec.type_id
        widget.resize(600, 420)
        widget.show()
        assert widget.active_timer_count() == 0 and widget.connection_count() == 0, "construction légère"
        widget.activate()
        env["hub"].jobs.wait_idle(5)
        pump(app, 0.05)
        assert widget.state in STATES, spec.type_id
        widget.deactivate()
        assert widget.active_timer_count() == 0, f"{spec.type_id} : minuteur non libéré"
        assert widget.connection_count() == 0, f"{spec.type_id} : abonnement non libéré"
        widget.dispose()
        widget.dispose()  # idempotent
        widget.close()
        widget.deleteLater()
    pump(app, 0.05)
    assert env["hub"].resource_consumers() == 0, "aucune mesure de ressources ne doit rester active"


def test_same_widget_twice_shares_one_source_of_truth(env):
    from src.conversation import get_default_conversation_context

    registry = default_registry()
    context = get_default_conversation_context()
    context.start_new_conversation("test")
    widgets = []
    for index in range(2):
        placement = Placement(f"w-conv-{index}", "jarvis.conversation", 0, 0, 5, 9)
        widget = registry.create(placement, _context(env, placement.id))
        widget.show()
        widget.activate()
        widgets.append(widget)
    context.add_user_message("Message partagé")
    assert wait_until(env["app"], lambda: all("Message partagé" in w.view.toPlainText() for w in widgets), 3)
    for widget in widgets:
        widget.dispose()
        widget.deleteLater()


def test_custom_panel_composes_real_metrics_and_releases_monitoring(env):
    registry = default_registry()
    spec = registry.spec("custom.panel")
    settings = spec.validate_settings({"title": "Mon cockpit", "m_cpu": True, "m_health": True,
                                       "m_tasks_active": False, "m_notifications": False})
    widget = registry.create(Placement("w-custom", "custom.panel", 0, 0, 4, 4, settings=settings),
                             _context(env, "w-custom"))
    widget.show()
    widget.activate()
    env["hub"].jobs.wait_idle(3)
    assert [m.key for m in widget.selected()] == ["health", "cpu"]
    assert set(widget._rows) == {"health", "cpu"}
    assert env["hub"].resource_consumers() == 1
    widget.deactivate()
    assert env["hub"].resource_consumers() == 0
    widget.apply_settings(spec.validate_settings({"m_health": False, "m_tasks_active": False,
                                                  "m_notifications": False}))
    widget.activate()
    assert widget.selected() == []
    assert "Aucun indicateur" in widget.note.text()
    widget.dispose()


def test_widget_failure_during_activation_is_contained(env):
    from UI.dashboard.widget_api import DashboardWidget

    class Broken(DashboardWidget):
        def on_activate(self):
            raise RuntimeError("panne simulée")

    widget = Broken(_context(env))
    widget.activate()
    assert widget.state == "error" and "panne simulée" in widget.state_message
    widget.dispose()


def test_unknown_widget_type_shows_placeholder_frame(env):
    from UI.dashboard.frame import WidgetFrame

    registry = default_registry()
    placement = Placement("w-x", "futur.widget", 0, 0, 3, 3)
    assert registry.create(placement, _context(env)) is None
    frame = WidgetFrame(placement, None, None, env["theme"])
    frame.show()
    pump(env["app"], 0.02)
    assert frame.widget is None
    frame.close()
