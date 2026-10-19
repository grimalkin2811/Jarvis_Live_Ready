"""Notes, documents, tâches personnelles et mises à jour : comportement réel.

Données isolées (``isolated_jarvis``) : mêmes fichiers/bases que les outils
vocaux, mais dans un dossier temporaire.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.dashboard_support import isolated_jarvis, pump, wait_until  # noqa: E402
from UI.dashboard.config import ConfigStore  # noqa: E402
from UI.dashboard.hub import DashboardHub  # noqa: E402
from UI.dashboard.layout_model import Placement  # noqa: E402
from UI.dashboard.registry import default_registry  # noqa: E402
from UI.dashboard.theme import ThemeManager  # noqa: E402
from UI.dashboard.widget_api import WidgetContext  # noqa: E402


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
        toasts: list[tuple[str, str]] = []
        opened: list[str] = []

        def make(type_id: str):
            spec = default_registry().spec(type_id)
            placement = Placement(f"w-{type_id}", type_id, 0, 0, *spec.default_size,
                                  settings=spec.validate_settings({}))
            context = WidgetContext(hub=hub, theme_manager=theme, instance_id=placement.id,
                                    confirm=lambda *a: True,
                                    open_path=lambda path: opened.append(path) or True,
                                    toast=lambda message, level="info": toasts.append((message, level)))
            widget = default_registry().create(placement, context)
            widget.activate()
            return widget

        yield {"app": app, "hub": hub, "data": data, "make": make, "toasts": toasts, "opened": opened}
        hub.shutdown()


def _settle(env, predicate, timeout=3.0):
    env["hub"].jobs.wait_idle(5)
    return wait_until(env["app"], predicate, timeout)


def test_note_written_in_dashboard_is_the_voice_tool_note(env):
    from src import tools

    widget = env["make"]("productivity.notes")
    assert _settle(env, lambda: widget.state in ("ready", "empty"))
    widget.entry.setText("Acheter du café")
    widget._add()
    assert _settle(env, lambda: widget.message.text() == "Note enregistrée.")
    assert widget.entry.text() == ""
    # Même fichier, mêmes fonctions que « prends note » à la voix.
    notes = tools.read_notes(limit=5)["notes"]
    assert any(n.get("texte") == "Acheter du café" for n in notes)
    assert _settle(env, lambda: widget.list.count() >= 1 and "Acheter du café" in widget.list.item(0).text())
    widget.dispose()


def test_empty_or_too_long_note_is_refused_without_writing(env):
    from src import tools
    from UI.dashboard.notes_service import add_note

    assert add_note("   ")["success"] is False
    assert add_note("x" * 2001)["success"] is False
    assert tools.read_notes(limit=5).get("notes", []) == []


def test_documents_lists_only_real_files_and_opens_them(env):
    root = os.environ["JARVIS_USER_CONTENT_DIR"]
    os.makedirs(os.path.join(root, "tasks"), exist_ok=True)
    with open(os.path.join(root, "Lettre.txt"), "w", encoding="utf-8") as handle:
        handle.write("Bonjour")
    with open(os.path.join(root, "tasks", "rapport.md"), "w", encoding="utf-8") as handle:
        handle.write("# Rapport")
    with open(os.path.join(root, ".cache.tmp"), "w", encoding="utf-8") as handle:
        handle.write("ignoré")
    widget = env["make"]("productivity.documents")
    assert _settle(env, lambda: widget.list.count() == 2)
    names = sorted(widget.list.item(i).text().split("\n")[0] for i in range(widget.list.count()))
    assert names == ["Lettre.txt", "rapport.md"]
    widget.list.setCurrentRow(0)
    widget._open_item(widget.list.currentItem())
    assert env["opened"] and os.path.exists(env["opened"][-1])
    # Fichier supprimé entre-temps : signalé, jamais « ouvert ».
    path = widget.list.item(1).data(256)  # Qt.UserRole
    os.remove(path)
    widget._open_item(widget.list.item(1))
    assert env["toasts"][-1][1] == "warning"
    widget.dispose()


def test_empty_documents_folder_is_explained(env):
    widget = env["make"]("productivity.documents")
    assert _settle(env, lambda: widget.state == "empty")
    assert "Aucun document" in widget.info.text()
    widget.dispose()


def test_personal_todo_add_and_complete_use_the_voice_database(env):
    todo = env["data"]["todo"]
    widget = env["make"]("tasks.todo")
    assert _settle(env, lambda: widget.state in ("ready", "empty"))
    widget.entry.setText("Appeler le garage")
    widget._add()
    assert _settle(env, lambda: widget.message.text() == "Tâche ajoutée.")
    listed = todo.list_tasks(status="pending", limit=50)  # base de l'outil vocal add_todo
    assert any((t.get("tache") or t.get("label")) == "Appeler le garage" for t in listed.get("taches", []))
    assert _settle(env, lambda: widget.list.count() == 1 and widget.list.itemWidget(widget.list.item(0)) is not None)
    box = widget.list.itemWidget(widget.list.item(0))
    box.setChecked(True)  # terminer la tâche depuis le Dashboard
    assert _settle(env, lambda: widget.message.text() == "Tâche terminée.")
    assert todo.list_tasks(status="pending", limit=50).get("taches", []) == []
    # Liste vide : état explicite dans la liste, retour d'action conservé.
    assert _settle(env, lambda: widget.state == "empty" and widget.list.count() == 1)
    assert widget.list.itemWidget(widget.list.item(0)) is None
    assert widget.message.text() == "Tâche terminée."
    widget.dispose()


# ---------------------------------------------------------------------------
# Mises à jour (mécanisme existant src.updater) : jamais de succès supposé
# ---------------------------------------------------------------------------


@pytest.fixture
def controller(app, tmp_path):
    from UI.dashboard.controller import DashboardController

    with isolated_jarvis():
        ctrl = DashboardController(state_path=str(tmp_path / "dashboard_state.json"), attach_bridge=False,
                                   dialog_executor=lambda dialog: True, open_path=lambda path: True,
                                   native_sender=lambda title, message: None,
                                   reduced_motion_probe=lambda: False)
        ctrl.open()
        toasts: list[tuple[str, str]] = []
        ctrl.window.toast = lambda message, level="info": toasts.append((message, level))
        ctrl.toasts = toasts
        yield ctrl
        ctrl.shutdown()
        pump(app, 0.05)


def _run_check(app, ctrl, monkeypatch, plan=None, error=None):
    from src import updater

    def fake_plan(*args, **kwargs):
        if error is not None:
            raise error
        return dict(plan)

    monkeypatch.setattr(updater, "build_update_plan", fake_plan)
    ctrl.check_for_update(manual=True)
    ctrl.hub.jobs.wait_idle(5)
    pump(app, 0.1)


def test_update_available_notifies_once(app, controller, monkeypatch):
    plan = {"current": "1.8.2", "latest": "1.9.0", "update_available": True}
    _run_check(app, controller, monkeypatch, plan)
    items = [n for n in controller.hub.notifications.items() if n.category == "update"]
    assert len(items) == 1 and "1.9.0" in items[0].message
    assert controller.store.config.general.last_notified_version == "1.9.0"
    _run_check(app, controller, monkeypatch, plan)
    assert len([n for n in controller.hub.notifications.items() if n.category == "update"]) == 1


def test_up_to_date_is_claimed_only_with_a_known_published_version(app, controller, monkeypatch):
    _run_check(app, controller, monkeypatch, {"current": "1.8.2", "latest": "1.8.2", "update_available": False})
    assert controller.toasts[-1] == ("Jarvis est à jour (1.8.2).", "success")
    # Release sans numéro exploitable : statut inconnu, jamais « à jour ».
    _run_check(app, controller, monkeypatch, {"current": "1.8.2", "latest": "", "update_available": False})
    message, level = controller.toasts[-1]
    assert level == "warning" and "à jour" not in message


def test_update_check_failure_is_reported_never_as_success(app, controller, monkeypatch):
    from src.updater import UpdateError

    before = controller.store.config.general.last_update_check
    _run_check(app, controller, monkeypatch, error=UpdateError("GitHub injoignable"))
    message, level = controller.toasts[-1]
    assert level == "warning" and "GitHub injoignable" in message
    assert controller.store.config.general.last_update_check == before
    assert not [n for n in controller.hub.notifications.items() if n.category == "update"]
