"""Exercise actual UI method bodies without pretending QtWidgets is available."""
import ast
import threading
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

from src.core.activity_boundary import ActivityBoundary


def load_methods(path, class_name, names):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name)
    methods = [node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {m.name for m in methods} == set(names)
    module = ast.Module(body=[ast.ClassDef(name="Subject", bases=[], keywords=[], body=methods,
                                          decorator_list=[])], type_ignores=[])
    namespace = {"threading": threading, "nullcontext": nullcontext}
    exec(compile(ast.fix_missing_locations(module), path, "exec"), namespace)
    return namespace["Subject"]


def test_orb_does_not_create_routine_worker_after_shutdown():
    subject = load_methods("UI/jarvis_menu.py", "MorphingOrbWidget", {"_run_routine_async"})()
    boundary = ActivityBoundary()
    subject.core = SimpleNamespace(activity_boundary=boundary)
    subject._closed = False
    messages = []
    subject._flash = messages.append
    boundary.reject_new()
    subject._run_routine_async("routine")
    assert messages and "arrêt" in messages[0]
    assert boundary.drain(0) and not boundary.snapshot()["active"]


def test_dialog_does_not_mutate_routine_after_shutdown():
    subject = load_methods("UI/routines_dialog.py", "RoutinesDialog", {"_activity", "_set_enabled"})()
    boundary = ActivityBoundary()
    subject._activity_boundary = boundary
    calls, messages = [], []
    subject.manager = SimpleNamespace(update_routine=lambda *a, **kw: calls.append((a, kw)))
    subject.feedback = SimpleNamespace(setText=messages.append)
    subject.rows = {}
    subject.refresh = lambda **kwargs: None
    subject.routines_changed = SimpleNamespace(emit=lambda: calls.append("signal"))
    boundary.reject_new()
    subject._set_enabled("routine", True)
    assert calls == [] and messages and "non enregistrée" in messages[0]
    assert boundary.drain(0) and not boundary.snapshot()["active"]
