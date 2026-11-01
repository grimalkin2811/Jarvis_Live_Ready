"""Behavioral GUI wiring test, requiring a working Qt GUI platform."""
from types import SimpleNamespace


def test_orb_desktop_dashboard_share_injected_core(monkeypatch, tmp_path):
    # Imports intentionally happen inside the test: a missing native Qt
    # dependency is a visible failure, not an artificial skip.
    from PySide6.QtWidgets import QApplication
    from UI import appearance_actions
    from UI.dashboard.config import ConfigStore
    from UI.dashboard.hub import DashboardHub
    from src.core import runtime as runtime_module
    from src.runtime_status import RuntimeStatusBoard
    from src.ui import InterfaceModeController, SessionVisibility

    monkeypatch.setenv('JARVIS_DATA_DIR', str(tmp_path))
    app = QApplication.instance() or QApplication([])
    mode = SimpleNamespace(should_suppress_visuals=lambda: False)
    routines = SimpleNamespace(mtime=lambda: 0, list_routine_names=lambda: {'success': True, 'routines': []})
    monkeypatch.setattr(runtime_module, '_default_modes', lambda: mode)
    monkeypatch.setattr(runtime_module, '_default_routines', lambda: routines)
    core = runtime_module.CoreRuntime(SimpleNamespace(), status=RuntimeStatusBoard())
    memory, tasks, scheduler = object(), object(), object()
    core.services.register('memory', memory)
    core.services.register('tasks', tasks)
    core.services.register('scheduler', scheduler)
    hub = DashboardHub(ConfigStore(None), core=core)
    controller = InterfaceModeController(appearance_actions.AppearanceState(), SessionVisibility(),
                                         initial_mode='blob', config_path=tmp_path / 'config.json', core=core)
    try:
        blob = controller._ensure_blob()
        controller._ensure_overlay()
        assert blob.core is controller.core is controller._presence_router.core is hub.core is core
        assert blob._service('memory') is hub.memory.manager() is memory
        assert blob._service('tasks') is hub.tasks.manager() is tasks
        assert blob._service('scheduler') is scheduler
        assert blob._service('modes') is mode
        assert not controller._presence_router._suppressed()
        assert blob._service('routines') is routines
        core.services.close()
        assert controller._presence_router._suppressed()
        try:
            blob._service('memory')
        except RuntimeError:
            pass
        else:
            raise AssertionError('Closed services must not be used')
    finally:
        hub.shutdown()
        controller.close()
        app.processEvents()
