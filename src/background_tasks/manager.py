"""Task Manager central v1.8 : source de vérité et boucle dédiée.

La boucle et le thread de ce module ne sont jamais ceux de Gemini Live principal.
Toutes les méthodes publiques sont synchrones, brèves et protégées par verrou.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import copy
import json
import threading
import uuid
from pathlib import Path
from typing import Callable

from google import genai

from .. import notifications, paths
from .config import BackgroundModelConfig
from .discovery import LiveModelResolver
from .executor import TaskExecutor
from .gateway import BackgroundModelGateway
from .models import BackgroundTask, TERMINAL_STATUSES, TaskPriority, TaskStatus, utc_now
from .quota import ComplexModelQuota
from .router import TaskRouter

TaskHook = Callable[[BackgroundTask], None]


class TaskManager:
    def __init__(
        self,
        api_key: str,
        *,
        config: BackgroundModelConfig | None = None,
        storage_path: str | Path | None = None,
        output_dir: str | Path | None = None,
        router=None,
        executor=None,
        client=None,
        client_factory=None,
        auto_start: bool = True,
    ):
        self.config = config or BackgroundModelConfig.from_env()
        self.storage_path = Path(storage_path) if storage_path else paths.background_tasks_file()
        self.output_dir = Path(output_dir) if output_dir else paths.background_task_results_dir()
        self._lock = threading.RLock()
        self._persistence_lock = threading.Lock()
        self._tasks: dict[str, BackgroundTask] = {}
        self._hooks: list[TaskHook] = []
        self._futures: dict[str, concurrent.futures.Future] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._closing = False
        self._init_error: BaseException | None = None
        self._api_key = api_key
        if (
            client is not None
            and client_factory is None
            and client.__class__.__module__.startswith("google.genai")
        ):
            raise ValueError(
                "Un client GenAI réel ne peut pas être transféré entre boucles; "
                "laisse TaskManager le créer dans son worker."
            )
        # `client` reste injectable pour les doubles synchrones historiques.
        # Un vrai client est toujours construit par la factory dans le worker.
        self._client_factory = client_factory or (
            (lambda: client) if client is not None else lambda: genai.Client(api_key=self._api_key)
        )
        self._provided_router = router
        self._provided_executor = executor
        self.client = None
        self.quota = None
        self.resolver = None
        self.router = None
        self.executor = None
        self._quota_path = (
            self.storage_path.with_name("background_quota.json")
            if storage_path else paths.background_quota_file()
        )
        self._load()
        if auto_start:
            self.start()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._closing = False
            self._ready.clear()
            self._thread = threading.Thread(target=self._run_loop, name="jarvis-background-tasks", daemon=True)
            self._thread.start()
        if not self._ready.wait(5.0):
            raise RuntimeError("La boucle des tâches d'arrière-plan n'a pas démarré.")
        if self._init_error is not None:
            raise RuntimeError(f"Initialisation du Task Manager impossible: {self._init_error}") from self._init_error

    async def _initialize_loop_resources(self) -> None:
        # Cette coroutine est exécutée par run_until_complete :
        # get_running_loop() retourne donc bien la boucle propriétaire.
        self.client = self._client_factory()
        self.quota = ComplexModelQuota(
            rpm=self.config.complex_rpm,
            rpd=self.config.complex_rpd,
            tpm=self.config.complex_tpm,
            concurrency=self.config.complex_concurrency,
            storage_path=self._quota_path,
        )
        self.resolver = LiveModelResolver(
            self.client,
            api_key_fingerprint=LiveModelResolver.fingerprint(self._api_key),
            hint=self.config.live_model_hint,
            retry_attempts=self.config.retry_attempts,
            retry_base_delay=self.config.retry_base_delay,
        )
        gateway = BackgroundModelGateway(
            self.client,
            retry_attempts=self.config.retry_attempts,
            retry_base_delay=self.config.retry_base_delay,
        )
        self.router = self._provided_router or TaskRouter(
            self.client, self.config, self.resolver, gateway=gateway
        )
        self.executor = self._provided_executor or TaskExecutor(
            self.client, self.config, self.quota, self.output_dir,
            self.resolver, gateway=gateway,
        )
        self._task_slots = asyncio.Semaphore(self.config.max_concurrent_tasks)

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._initialize_loop_resources())
        except BaseException as exc:
            self._init_error = exc
            self._ready.set()
            loop.close()
            return
        self._ready.set()
        try:
            loop.run_forever()
        finally:
            pending = asyncio.all_tasks(loop)
            for item in pending:
                item.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            try:
                close_client = getattr(getattr(self.client, "aio", None), "aclose", None)
                if close_client is not None:
                    loop.run_until_complete(close_client())
            except Exception:
                pass
            loop.close()

    def close(self, wait: bool = False) -> None:
        self._closing = True
        loop = self._loop
        if loop and loop.is_running():
            for future in list(self._futures.values()):
                future.cancel()
            loop.call_soon_threadsafe(loop.stop)
        if wait and self._thread:
            self._thread.join(timeout=3.0)

    def add_hook(self, hook: TaskHook) -> None:
        with self._lock:
            if hook not in self._hooks:
                self._hooks.append(hook)

    def remove_hook(self, hook: TaskHook) -> None:
        with self._lock:
            if hook in self._hooks:
                self._hooks.remove(hook)

    @staticmethod
    def _clone(task: BackgroundTask) -> BackgroundTask:
        return BackgroundTask.from_dict(copy.deepcopy(task.to_dict()))

    def _emit(self, task: BackgroundTask) -> None:
        snapshot = self._clone(task)
        with self._lock:
            hooks = list(self._hooks)
        for hook in hooks:
            try:
                hook(snapshot)
            except Exception:
                pass

    def _save(self) -> None:
        with self._lock:
            payload = {"version": 1, "tasks": [task.to_dict() for task in self._tasks.values()]}
        try:
            with self._persistence_lock:
                self.storage_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
                temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                temporary.replace(self.storage_path)
        except Exception as exc:
            print(f"[Tasks] Persistance indisponible : {exc}")

    def _load(self) -> None:
        if not self.storage_path.exists():
            return
        try:
            payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
            for raw in payload.get("tasks", []):
                task = BackgroundTask.from_dict(raw)
                if task.status not in TERMINAL_STATUSES:
                    task.status = TaskStatus.FAILED
                    task.finished_at = utc_now()
                    task.error = "Jarvis a été arrêté avant la fin de cette tâche. Tu peux la relancer."
                    task.current_step = "Interrompue par l'arrêt précédent"
                self._tasks[task.id] = task
        except Exception as exc:
            print(f"[Tasks] Historique illisible, ignoré : {exc}")

    def create_task(self, title: str, description: str, priority: str | TaskPriority = TaskPriority.NORMAL) -> BackgroundTask:
        description = str(description or "").strip()
        if not description:
            raise ValueError("La description de la tâche ne peut pas être vide.")
        title = str(title or "").strip() or description.split(".", 1)[0][:80]
        priority_value = priority if isinstance(priority, TaskPriority) else TaskPriority(str(priority).lower())
        task = BackgroundTask(id=str(uuid.uuid4()), title=title[:120], description=description, priority=priority_value)
        with self._lock:
            if self._closing:
                raise RuntimeError("Le gestionnaire de tâches est en cours d'arrêt.")
            self._tasks[task.id] = task
        self._save()
        self._emit(task)
        self.start()
        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(self._run_task(task.id), self._loop)
        with self._lock:
            self._futures[task.id] = future
        return self._clone(task)

    def retry_task(self, task_id: str) -> BackgroundTask:
        old = self.get_task(task_id)
        if old is None:
            raise KeyError(task_id)
        if old.status not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            raise ValueError("Seule une tâche échouée ou annulée peut être relancée.")
        return self.create_task(f"{old.title} (nouvel essai)", old.description, old.priority)

    async def _process_task(self, task_id: str) -> None:
        task = self._tasks[task_id]
        assert self.router is not None and self.executor is not None
        self._mutate(task_id, status=TaskStatus.RUNNING, started_at=utc_now(), progress=5, current_step="Routage de la tâche")
        route = await self.router.route(task.title, task.description, task.priority)
        self._mutate(
            task_id, complexity=route.complexity, task_type=route.task_type,
            model=route.model, priority=route.priority, routing_reason=route.reason,
            total_steps=route.estimated_steps, progress=10, current_step="Plan validé",
        )

        def update(progress, step, number, total, waiting=False):
            self._mutate(
                task_id, status=TaskStatus.WAITING_FOR_TOOL if waiting else TaskStatus.RUNNING,
                progress=max(0, min(99, int(progress))), current_step=str(step),
                current_step_number=int(number), total_steps=int(total),
            )

        result = await self.executor.execute(self._tasks[task_id], route, update)
        completed = self._mutate(
            task_id, status=TaskStatus.COMPLETED, finished_at=utc_now(), progress=100,
            current_step="Terminée", current_step_number=route.estimated_steps,
            result=result.text, summary=result.summary, files=result.files,
            token_usage=result.tokens, seen=False,
        )
        try:
            notifications.publish("Tâche terminée", f"J'ai terminé : {completed.title}", silent=True)
        except Exception:
            pass

    async def _run_task(self, task_id: str) -> None:
        async def bounded_lifecycle() -> None:
            # Le timeout englobe aussi l'attente d'un slot, pas seulement les
            # appels modèle, afin qu'aucune tâche ne reste QUEUED indéfiniment.
            async with self._task_slots:
                await self._process_task(task_id)

        try:
            await asyncio.wait_for(
                bounded_lifecycle(), timeout=self.config.task_timeout_seconds
            )
        except asyncio.CancelledError:
            self._mutate(task_id, status=TaskStatus.CANCELLED, finished_at=utc_now(), current_step="Annulée", error="Tâche annulée.")
        except asyncio.TimeoutError:
            self._mutate(
                task_id, status=TaskStatus.FAILED, finished_at=utc_now(), current_step="Échec",
                error=f"Timeout global de la tâche après {self.config.task_timeout_seconds:.0f}s; ressources annulées.",
            )
        except Exception as exc:
            self._mutate(
                task_id, status=TaskStatus.FAILED, finished_at=utc_now(), current_step="Échec",
                error=str(exc) or exc.__class__.__name__,
            )
        finally:
            with self._lock:
                self._futures.pop(task_id, None)

    def _mutate(self, task_id: str, **changes) -> BackgroundTask:
        with self._lock:
            task = self._tasks[task_id]
            for name, value in changes.items():
                setattr(task, name, value)
            snapshot = self._clone(task)
        self._save()
        self._emit(snapshot)
        return snapshot

    def get_task(self, task_id: str) -> BackgroundTask | None:
        with self._lock:
            task = self._tasks.get(str(task_id))
            return self._clone(task) if task else None

    def list_tasks(self, *, status: TaskStatus | str | None = None) -> list[BackgroundTask]:
        status_value = TaskStatus(status) if status else None
        with self._lock:
            values = [self._clone(item) for item in self._tasks.values() if status_value is None or item.status is status_value]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    def list_active_tasks(self) -> list[BackgroundTask]:
        return [item for item in self.list_tasks() if item.status not in TERMINAL_STATUSES]

    def list_completed_tasks(self) -> list[BackgroundTask]:
        return self.list_tasks(status=TaskStatus.COMPLETED)

    def list_unread_completed_tasks(self) -> list[BackgroundTask]:
        return [item for item in self.list_completed_tasks() if not item.seen]

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(str(task_id))
            if task is None or task.status in TERMINAL_STATUSES:
                return False
            future = self._futures.get(task.id)
        if future is not None:
            future.cancel()
        else:
            self._mutate(task.id, status=TaskStatus.CANCELLED, finished_at=utc_now(), current_step="Annulée")
        return True

    def mark_task_as_seen(self, task_id: str) -> bool:
        task = self.get_task(task_id)
        if task is None or task.status is not TaskStatus.COMPLETED:
            return False
        self._mutate(task.id, seen=True)
        return True

    def get_task_result(self, task_id: str, *, mark_seen: bool = True) -> dict | None:
        task = self.get_task(task_id)
        if task is None:
            return None
        if mark_seen and task.status is TaskStatus.COMPLETED and not task.seen:
            self.mark_task_as_seen(task.id)
            task.seen = True
        return {
            "id": task.id, "title": task.title, "status": task.status.value,
            "summary": task.summary, "result": task.result, "files": task.files,
            "error": task.error, "partial_errors": task.partial_errors,
            "model": task.model, "complexity": task.complexity.value if task.complexity else None,
            "task_type": task.task_type, "routing_reason": task.routing_reason,
            "steps": task.total_steps, "seen": task.seen,
        }

    def quota_status(self) -> dict:
        if self.quota is None:
            raise RuntimeError("Le quota background n'est pas initialisé.")
        return self.quota.snapshot().__dict__.copy()

    def live_model_status(self) -> dict:
        resolution = self.resolver.resolution if self.resolver is not None else None
        return {
            "model": resolution.model if resolution else None,
            "transport": resolution.transport if resolution else "Live/BidiGenerateContent",
            "validated": bool(resolution and resolution.validated),
        }

    def resolve_live_model(self, timeout: float = 60.0) -> dict:
        """Déclenche la découverte sur la boucle propriétaire, thread-safe."""
        self.start()
        assert self._loop is not None and self.resolver is not None
        future = asyncio.run_coroutine_threadsafe(self.resolver.resolve(), self._loop)
        resolution = future.result(timeout=timeout)
        return {
            "model": resolution.model,
            "transport": resolution.transport,
            "validated": resolution.validated,
        }


_DEFAULT_LOCK = threading.RLock()
_DEFAULT: TaskManager | None = None


def configure_default_task_manager(api_key: str, **kwargs) -> TaskManager:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None or _DEFAULT._closing:
            _DEFAULT = TaskManager(api_key, **kwargs)
        return _DEFAULT


def get_default_task_manager() -> TaskManager:
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            raise RuntimeError("Le gestionnaire de tâches d'arrière-plan n'est pas initialisé.")
        return _DEFAULT


def reset_default_task_manager_for_tests() -> None:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is not None:
            _DEFAULT.close(wait=True)
        _DEFAULT = None
