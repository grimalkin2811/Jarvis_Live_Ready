"""Exercise the controller's actual method body without claiming QtWidgets validation."""
from types import SimpleNamespace

from tests.test_routines_ui_boundary_without_widgets import load_methods


def test_update_timer_and_completions_do_nothing_after_controller_shutdown():
    controller = load_methods("UI/dashboard/controller.py", "DashboardController", {"check_for_update"})()
    submitted = []
    controller.hub = SimpleNamespace(jobs=SimpleNamespace(submit=lambda *a, **kw: submitted.append((a, kw))))
    controller._quitting = True
    controller.check_for_update()
    assert not submitted
    controller._quitting = False
    controller.check_for_update(manual=True)
    assert len(submitted) == 1
    callbacks = submitted[0][1]
    controller._quitting = True
    # No store/window/logs are installed: any late access would fail.
    callbacks["on_done"]({"latest": "2", "update_available": True})
    callbacks["on_error"](RuntimeError("late"))
    assert len(submitted) == 1
