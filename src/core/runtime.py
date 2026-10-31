"""Application runtime shared by console and graphical front ends.

The runtime owns the existing voice pipeline and local services, but knows
nothing about Qt. Provider/audio factories are injected so it can be exercised
without hardware or a network. The existing status board remains the single
source consumed by the Dashboard.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from .events import EventBus, RuntimeEvent
from .services import ServiceRegistry
from ..runtime_status import RUNTIME, EngineState, redact_secrets

log = logging.getLogger("jarvis.core.runtime")


@dataclass
class RuntimeHooks:
    presence: Callable[[str], None] | None = None
    voice_level: Callable[[float], None] | None = None
    desktop: Callable[[str, dict], None] | None = None
    controls: Any = None
    voice_options: dict[str, Any] = field(default_factory=dict)
    reason: str = "démarrage"


@dataclass(frozen=True)
class RuntimeFactories:
    audio: Callable[..., Any]
    provider: Callable[..., Any]
    memory: Callable[..., Any]
    set_memory: Callable[..., Any]
    conversation: Callable[..., Any]
    scheduler: Callable[..., Any]
    tasks: Callable[..., Any]
    auth_error: type[Exception]
    recovery_error: type[Exception]


def default_factories() -> RuntimeFactories:
    # Lazy: importing the core alone never requires Qt, audio drivers or GenAI.
    from ..audio import AudioIO
    from ..background_tasks import configure_default_task_manager
    from ..conversation import get_default_conversation_context
    from ..gemini_live import AuthError, GeminiLive, SessionRecoveryExhausted
    from ..memory import MemoryManager, set_default_memory_manager
    from ..scheduler import start_default_scheduler

    return RuntimeFactories(AudioIO, GeminiLive, MemoryManager, set_default_memory_manager,
                            get_default_conversation_context, start_default_scheduler,
                            configure_default_task_manager, AuthError, SessionRecoveryExhausted)


class CoreRuntime:
    """Owns a single voice backend and its resources for one application run."""

    _owner_lock = threading.Lock()
    _owner: CoreRuntime | None = None

    def __init__(self, config: Any, hooks: RuntimeHooks | None = None, *,
                 factories: RuntimeFactories | None = None, status=RUNTIME) -> None:
        self.config = config
        self.hooks = hooks or RuntimeHooks()
        self.factories = factories
        self.status = status
        self.events = EventBus()
        self.services = ServiceRegistry()
        self.audio = None
        self.provider = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._stop_requested = threading.Event()
        self._started = False
        self._text_handler = None
        self._cleanup_failed = False

    def _notify(self, callback, *args) -> None:
        if callback is not None:
            try:
                callback(*args)
            except Exception:
                log.exception("Runtime callback failed")

    def _desktop(self, event: str, **payload) -> None:
        self._notify(self.hooks.desktop, event, payload)

    def _mic(self, pcm: bytes) -> None:
        provider, loop = self.provider, self._loop
        if provider is None or loop is None or self._stop_requested.is_set() or not provider.can_send():
            return
        # Capture the generation and epoch on the audio thread, not later on
        # the asyncio thread. This preserves stale-audio protection.
        coro = provider.send_audio(pcm, capture_generation=provider.session_generation,
                                   capture_turn_epoch=provider.turn_epoch)
        try:
            future = asyncio.run_coroutine_threadsafe(coro, loop)
        except RuntimeError:
            coro.close()
            log.warning("Audio dropped because the runtime loop is closed")
            return

        def completed(done) -> None:
            if not done.cancelled():
                try:
                    done.result()
                except Exception:
                    log.exception("Microphone audio submission failed")
        future.add_done_callback(completed)

    def _barge_in(self) -> None:
        if self.provider is not None:
            self.provider.request_interrupt()

    def stop_speaking(self) -> None:
        if self.audio is not None:
            self.audio.stop_speaking()
        if self.provider is not None:
            self.provider.request_interrupt(manual=True)

    def request_stop(self) -> None:
        """Request shutdown from any thread, including while receive() waits."""
        self._stop_requested.set()
        loop, task = self._loop, self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                log.debug("Runtime loop already closed")

    async def run(self) -> None:
        if self._started:
            raise RuntimeError("A CoreRuntime instance can only be run once")
        self._started = True
        with CoreRuntime._owner_lock:
            if CoreRuntime._owner is not None:
                raise RuntimeError("A Jarvis runtime is already active in this process")
            CoreRuntime._owner = self
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()
        self.status.set_engine_state(EngineState.STARTING, "chargement des composants")
        self.events.publish(RuntimeEvent("starting"))
        try:
            if self._stop_requested.is_set():
                return
            factories = self.factories or default_factories()
            await self._run_pipeline(factories)
        except asyncio.CancelledError:
            if not self._stop_requested.is_set():
                raise
        except Exception as exc:
            self.status.record_error(redact_secrets(f"{type(exc).__name__}: {exc}", limit=300))
            self.status.set_engine_state(EngineState.FAILED, "initialisation ou moteur indisponible")
            self.events.publish(RuntimeEvent("failed", type(exc).__name__))
            log.exception("Runtime failed")
            raise
        finally:
            if self._text_handler is not None:
                self.status.unregister_text_handler(self._text_handler)
            controls = self.hooks.controls
            if controls is not None:
                self._notify(controls.set_stop_speaking_handler, None)
            failures = self.services.close()
            if failures or self._cleanup_failed:
                self.status.record_error("Nettoyage incomplet du runtime : " + ", ".join(failures or ["provider"]))
                self.status.set_engine_state(EngineState.FAILED, "nettoyage incomplet")
            if self.status.snapshot().get("engine_state") not in (EngineState.AUTH_ERROR, EngineState.FAILED):
                self.status.set_engine_state(EngineState.STOPPED, "runtime arrêté")
            self.events.publish(RuntimeEvent("stopped"))
            self.events.clear()
            self._task = None
            self._loop = None
            with CoreRuntime._owner_lock:
                if CoreRuntime._owner is self:
                    CoreRuntime._owner = None

    async def _run_pipeline(self, factories: RuntimeFactories) -> None:
        cfg, hooks, services = self.config, self.hooks, self.services
        self._notify(hooks.presence, "loading")
        memory = services.register("memory", factories.memory(
            cfg.memory_database_path, enabled=cfg.memory_enabled,
            max_results=cfg.memory_max_results, min_importance=cfg.memory_min_importance))
        factories.set_memory(memory)
        self.status.update(memory_state="disabled" if not memory.enabled else "ok" if memory.available else "failed")
        conversation = services.register("conversation", factories.conversation())
        conversation.start_new_conversation(reason=hooks.reason)
        try:
            scheduler = factories.scheduler()
            services.register("scheduler", scheduler, close=scheduler.stop)
        except Exception as exc:
            log.warning("Scheduler unavailable: %s", redact_secrets(str(exc), limit=300))
        try:
            manager = factories.tasks(cfg.api_key)
            services.register("tasks", manager, close=lambda: manager.close(wait=True))
            self.status.update(tasks_state="ok", tasks_detail="")
        except Exception as exc:
            log.warning("Background tasks unavailable: %s", redact_secrets(str(exc), limit=300))
            self.status.update(tasks_state="failed", tasks_detail=str(exc))

        controls = hooks.controls
        def control(name):
            return getattr(controls, name, None) if controls is not None else None

        self.audio = services.register("audio", factories.audio(
            self._mic, presence_hook=hooks.presence, voice_hook=hooks.voice_level,
            mic_enabled=control("get_mic_enabled"), wake_threshold=control("get_wake_threshold"),
            volume_provider=control("get_tts_volume"), listen_mode_provider=control("get_listen_mode"),
            barge_in_provider=control("get_barge_in"), post_response_provider=control("get_post_response_listen"),
            on_barge_in=self._barge_in,
            output_level_hook=lambda level: self._desktop("level", value=level, source="output")),
            close=lambda: self.audio.stop())
        audio = self.audio
        if controls is not None:
            controls.set_stop_speaking_handler(self.stop_speaking)
        self.status.update(wake_word="loaded" if getattr(audio, "wake_model", None) is not None else "unavailable",
                           wake_word_detail=getattr(audio, "wake_framework", "") or "")

        def interrupted():
            self._desktop("interrupted")
            audio.clear_output()

        def speaking():
            audio.begin_speaking()
            self._notify(hooks.presence, "speaking")

        provider = factories.provider(
            cfg.api_key, cfg.model, cfg.user, on_audio=audio.play,
            on_turn_complete=audio.extend_listening, on_interrupted=interrupted,
            on_turn_open=audio.note_turn_open, on_turn_resolved=audio.note_turn_resolved,
            on_speaking=speaking, on_thinking=lambda: self._notify(hooks.presence, "thinking"),
            on_tool_start=lambda name: self._desktop("tool_start", name=name),
            on_tool_end=lambda name, ok=True: self._desktop("tool_end", name=name, success=ok),
            on_user_transcript=lambda text, final=False: self._desktop("transcript", text=text, final=final),
            on_assistant_transcript=lambda text: self._desktop("response", text=text),
            memory_manager=memory, conversation=conversation, **hooks.voice_options)
        self.provider = services.register("provider", provider)
        audio.mic_pause_hook = provider.notify_mic_paused
        self._text_handler = provider.submit_text_threadsafe
        self.status.register_text_handler(self._text_handler)
        self.status.update(model=str(cfg.model or ""))
        try:
            audio.start()
        except Exception as exc:
            self.status.update(audio_state="failed", audio_detail=f"démarrage audio impossible : {exc}")
            try:
                await provider.close()
            except Exception:
                self._cleanup_failed = True
                log.exception("Provider close after audio startup failure failed")
            raise
        self.status.update(audio_state="ok", audio_detail="flux micro et haut-parleurs démarrés")
        self.events.publish(RuntimeEvent("audio_started"))

        while not self._stop_requested.is_set():
            provider.reconnect_requested = False
            try:
                self.status.set_engine_state(EngineState.CONNECTING, "ouverture de la session")
                await provider.connect()
                self.status.set_engine_state(EngineState.READY, "", model=str(provider.model or ""),
                                             session_generation=int(getattr(provider, "session_generation", 0) or 0))
                self.events.publish(RuntimeEvent("connected"))
                await provider.receive_loop()
            except asyncio.CancelledError:
                raise
            except factories.auth_error as exc:
                self.status.record_error(str(exc))
                self.status.set_engine_state(EngineState.AUTH_ERROR, "clé API refusée")
                break
            except factories.recovery_error as exc:
                self.status.record_error(str(exc))
                self.status.set_engine_state(EngineState.FAILED, "budget de reconnexion épuisé")
                break
            except Exception as exc:
                if not provider.reconnect_requested:
                    log.warning("Connection lost: %s", redact_secrets(str(exc), limit=300))
                    self.status.record_error(redact_secrets(f"{type(exc).__name__}: {exc}", limit=300))
                    self.status.note_reconnect_attempt()
                    self.status.set_engine_state(EngineState.RECONNECTING, "reconnexion")
                    self.events.publish(RuntimeEvent("reconnecting", type(exc).__name__))
                    audio.awake = False
                    self._desktop("error", reason="connexion")
                    self._notify(hooks.presence, "hidden")
                    if audio.wake_model is not None:
                        try:
                            audio.wake_model.reset()
                        except Exception:
                            log.exception("Wake word reset failed")
                    delay = max(1.0, min(30.0, provider.session_recovery_backoff_seconds()))
                    await asyncio.sleep(delay)
            else:
                delay = provider.session_recovery_backoff_seconds()
                await asyncio.sleep(delay if delay > 0 else 0.1)
            finally:
                try:
                    await provider.close()
                except Exception:
                    self._cleanup_failed = True
                    log.exception("Provider session close failed")
