"""Verify Dashboard's actual non-widget bridge against the runtime singletons."""
import os
import subprocess
import sys
import textwrap


def test_dashboard_hub_observes_runtime_services_and_detaches(tmp_path):
    # Keep QCoreApplication in a child: a suite which later needs QApplication
    # must not inherit a process-global, non-GUI Qt application.
    script = textwrap.dedent('''
        import asyncio
        import os
        import sys
        from pathlib import Path
        from unittest.mock import patch
        from PySide6.QtCore import QCoreApplication
        from src import conversation, memory, scheduler
        from src.background_tasks import manager as tasks
        from src.core.runtime import CoreRuntime, RuntimeFactories
        from src.runtime_status import RuntimeStatusBoard
        from UI.dashboard.config import ConfigStore
        from UI.dashboard.hub import DashboardHub
        from tests.test_core_runtime import Audio, Provider, AuthFailure, RecoveryFailure, config

        app = QCoreApplication([])
        root = Path(sys.argv[1])
        os.environ['JARVIS_SCHEDULE_DATABASE_PATH'] = str(root / 'schedule.db')
        cfg = config()
        cfg.memory_database_path = str(root / 'memory.db')
        status = RuntimeStatusBoard()
        factories = RuntimeFactories(
            Audio, Provider, memory.MemoryManager, memory.set_default_memory_manager,
            conversation.get_default_conversation_context, scheduler.start_default_scheduler,
            lambda key: tasks.configure_default_task_manager(
                key, storage_path=root / 'tasks.json', output_dir=root / 'out'),
            AuthFailure, RecoveryFailure,
        )
        async def scenario():
            hub = DashboardHub(ConfigStore(None), runtime=status)
            hub.start()
            context = hub.conversation()
            before = context.listener_count()
            runtime = CoreRuntime(cfg, factories=factories, status=status)
            task = asyncio.create_task(runtime.run())
            while runtime.provider is None:
                await asyncio.sleep(0)
            await runtime.provider.ready.wait()
            hub._attach_tasks()
            manager = runtime.services.require('tasks')
            scheduled = runtime.services.require('scheduler')
            assert hub.runtime is runtime.status
            assert hub.conversation() is runtime.services.require('conversation') is context
            assert hub.memory.manager() is runtime.services.require('memory')
            assert hub.tasks.manager() is manager is hub._task_manager
            assert runtime.provider.kwargs['memory_manager'] is hub.memory.manager()
            assert runtime.provider.kwargs['conversation'] is context
            assert status.submit_text('hello') == 'hello'
            assert scheduled.running
            runtime.request_stop()
            await asyncio.wait_for(task, 8)
            assert not manager._thread.is_alive()
            assert not scheduled.running
            hub.shutdown()
            assert status.hook_count() == 0
            assert context.listener_count() == before - 1
            assert hub._task_manager is None
        with patch.object(conversation, '_DEFAULT', None), patch.object(memory, '_DEFAULT_MANAGER', None), \\
             patch.object(tasks, '_DEFAULT', None), patch.object(scheduler, '_DEFAULT_SCHEDULER', None):
            asyncio.run(scenario())
    ''')
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)], text=True,
                            capture_output=True, timeout=30, env=os.environ.copy())
    assert result.returncode == 0, result.stdout + result.stderr
