"""Outils partagés par les tests du Dashboard et son protocole visuel.

Tout est ISOLÉ : dossier de données temporaire, base mémoire temporaire,
liste de tâches temporaire, notes temporaires, Task Manager réel mais avec
routeur/exécuteur factices (aucun appel réseau, aucune clé). Les données
réelles de l'utilisateur ne sont jamais touchées.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import os
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class FakeRouter:
    def __init__(self, delay: float = 0.0, complexity: str = "simple") -> None:
        self.delay = delay
        self.complexity = complexity

    async def route(self, title, description, priority):
        from src.background_tasks.config import DEFAULT_COMPLEX_MODEL
        from src.background_tasks.models import RoutingDecision, TaskComplexity

        await asyncio.sleep(self.delay)
        complexity = TaskComplexity.COMPLEX if self.complexity == "complex" else TaskComplexity.SIMPLE
        model = DEFAULT_COMPLEX_MODEL if complexity is TaskComplexity.COMPLEX else "gemini-3-live-test"
        return RoutingDecision(complexity, model, "test isolé", False, 3, priority)


class FakeExecutor:
    """Exécuteur factice : progression réelle via ``update``, échec optionnel."""

    def __init__(self, delay: float = 0.05, fail_titles: tuple[str, ...] = (), block: threading.Event | None = None,
                 files: list[str] | None = None) -> None:
        self.delay = delay
        self.fail_titles = fail_titles
        self.block = block
        self.files = files or []

    async def execute(self, task, route, update):
        from src.background_tasks.executor import ExecutionResult

        update(35, "Analyse de la demande", 1, 3)
        if self.block is not None:
            while not self.block.is_set():
                await asyncio.sleep(0.02)
        await asyncio.sleep(self.delay)
        update(70, "Rédaction", 2, 3)
        if task.title in self.fail_titles:
            raise RuntimeError("quota dépassé (simulation de test)")
        return ExecutionResult(f"Résultat complet de « {task.title} ».", f"Résumé de {task.title}", self.files, 42)


def make_task_manager(root: str | os.PathLike, **executor_kwargs):
    from src.background_tasks.config import BackgroundModelConfig
    from src.background_tasks.manager import TaskManager

    root = Path(root)
    return TaskManager(
        "fake-key-for-tests",
        router=FakeRouter(),
        executor=FakeExecutor(**executor_kwargs),
        config=BackgroundModelConfig(),
        storage_path=root / "tasks.json",
        output_dir=root / "out",
        client_factory=lambda: object(),
    )


def wait_task(manager, task_id: str, statuses, timeout: float = 5.0):
    wanted = {getattr(s, "value", s) for s in statuses}
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = manager.get_task(task_id)
        if task is not None and task.status.value in wanted:
            return task
        time.sleep(0.01)
    raise AssertionError(f"tâche {task_id} : statut attendu {wanted}, obtenu {manager.get_task(task_id)}")


class FakeTextEngine:
    """Moteur texte factice branché sur le VRAI ``RuntimeStatusBoard``.

    ``mode`` : "accept" (accepte et répond via le contexte partagé),
    "busy" (refus occupé), "fail" (échec d'envoi), "hold" (le Future reste
    en attente jusqu'à ``release()``).
    """

    def __init__(self, context, mode: str = "accept", reply: str = "Réponse de test.") -> None:
        self.context = context
        self.mode = mode
        self.reply = reply
        self.calls: list[str] = []
        self._held: list[concurrent.futures.Future] = []

    def __call__(self, text: str) -> concurrent.futures.Future:
        from src.gemini_live import TextSubmitError

        self.calls.append(text)
        future: concurrent.futures.Future = concurrent.futures.Future()
        if self.mode == "busy":
            future.set_exception(TextSubmitError("busy", "Jarvis est en train de répondre."))
        elif self.mode == "fail":
            future.set_exception(TextSubmitError("send_failed", "connexion interrompue"))
        elif self.mode == "hold":
            self._held.append(future)
        else:
            self.context.add_user_message(text)
            future.set_result({"accepted": True})

            def _answer():
                time.sleep(0.05)
                self.context.add_assistant_message(self.reply)

            threading.Thread(target=_answer, daemon=True).start()
        return future

    def release(self, ok: bool = True) -> None:
        from src.gemini_live import TextSubmitError

        for future in self._held:
            if ok:
                future.set_result({"accepted": True})
            else:
                future.set_exception(TextSubmitError("busy", "occupé"))
        self._held.clear()


@contextlib.contextmanager
def isolated_jarvis(prefix: str = "jarvis-dashboard-"):
    """Données Jarvis isolées (mémoire, tâches perso, notes, préférences)."""
    from src import memory as memory_module
    from src import todo as todo_module
    from src import tools

    tmp = tempfile.mkdtemp(prefix=prefix)
    saved_env = {key: os.environ.get(key) for key in ("JARVIS_DATA_DIR", "JARVIS_USER_CONTENT_DIR")}
    os.environ["JARVIS_DATA_DIR"] = tmp
    os.environ["JARVIS_USER_CONTENT_DIR"] = os.path.join(tmp, "user_content")
    saved_notes = tools.NOTES_FILE
    tools.NOTES_FILE = os.path.join(tmp, "notes.json")
    manager = memory_module.MemoryManager(os.path.join(tmp, "memory.db"))
    memory_module.set_default_memory_manager(manager)
    todo = todo_module.TodoManager(os.path.join(tmp, "todo.db"))
    todo_module.set_default_todo_manager(todo)
    try:
        yield {"root": tmp, "memory": manager, "todo": todo}
    finally:
        memory_module.set_default_memory_manager(None)
        todo_module.set_default_todo_manager(None)
        tools.NOTES_FILE = saved_notes
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def pump(app, seconds: float = 0.2) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


def wait_until(app, predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    app.processEvents()
    return bool(predicate())
