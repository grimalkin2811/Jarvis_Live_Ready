"""Headless QtCore integration; deliberately not a GUI/widget validation."""
import os
import subprocess
import sys
import textwrap


def test_dashboard_reopen_sees_same_core_and_notification_events(tmp_path):
    script = textwrap.dedent('''
        import asyncio
        import sys
        from dataclasses import replace
        from pathlib import Path
        from unittest.mock import patch
        from PySide6.QtCore import QCoreApplication
        from UI.dashboard.config import ConfigStore
        from UI.dashboard.hub import DashboardHub
        from src import notifications
        from src.conversation import ConversationContext
        from src.core import runtime as runtime_module
        from src.core.runtime import CoreRuntime
        from src.memory import MemoryManager
        from src.runtime_status import RuntimeStatusBoard
        from src.scheduler import Scheduler
        from src.todo import TodoManager
        from tests.test_core_runtime import factory_set, config

        app = QCoreApplication([])
        root = Path(sys.argv[1])
        context = ConversationContext()
        scheduler = Scheduler(root / 'schedule.db')
        todo = TodoManager(root / 'todo.db')
        mode = type('Mode', (), {'block_for_tool': lambda *args: None,
                                  'should_suppress_notifications': lambda self: False})()
        activity = type('Activity', (), {'log': lambda *args, **kwargs: None})()
        cfg = config()
        cfg.memory_database_path = str(root / 'memory.db')
        factories = replace(factory_set([]), memory=MemoryManager,
                            conversation=lambda: context, scheduler=lambda: scheduler)
        async def scenario():
            core = CoreRuntime(cfg, factories=factories, status=RuntimeStatusBoard())
            hub = DashboardHub(ConfigStore(None), core=core)
            hub.start()
            task = asyncio.create_task(core.run())
            while core.provider is None:
                await asyncio.sleep(0)
            await core.provider.ready.wait()
            app.processEvents()
            tools = core.provider.kwargs['tool_functions']
            assert hub.conversation() is context is core.services.require('conversation')
            assert hub.memory.manager() is core.services.require('memory')
            assert hub.todo.manager() is todo is core.services.require('todo')
            assert tools['remember'](content='violet partagé')['success']
            assert 'violet partagé' in str(hub.memory.manager().list_memories())
            assert tools['add_todo'](text='relire le rapport')['success']
            assert 'relire le rapport' in str(hub.todo.manager().list_tasks())
            assert tools['set_reminder'](text='rappel partagé', when='dans 20 minutes')['success']
            assert 'rappel partagé' in str(core.services.require('scheduler').list_reminders())
            seen = []
            unsubscribe = core.events.subscribe(lambda event: seen.append(event.kind))
            core.events.publish(runtime_module.RuntimeEvent('probe'))
            assert seen == ['probe']
            unsubscribe()
            with patch('src.scheduler._notifications_suppressed_by_mode', return_value=False):
                assert scheduler.notify('Test', 'événement un')['success']
            app.processEvents()
            assert any(item.message == 'événement un' for item in hub.notifications.items())
            before = len(notifications._HOOKS)
            hub.shutdown()
            assert len(notifications._HOOKS) == before - 1
            assert core.services.require('memory') is hub.memory.manager()
            reopened = DashboardHub(ConfigStore(None), core=core)
            reopened.start()
            app.processEvents()
            assert reopened.conversation() is context
            assert reopened.memory.manager() is core.services.require('memory')
            with patch('src.scheduler._notifications_suppressed_by_mode', return_value=False):
                assert scheduler.notify('Test', 'événement deux')['success']
            app.processEvents()
            assert any(item.message == 'événement deux' for item in reopened.notifications.items())
            reopened.shutdown()
            core.request_stop()
            await asyncio.wait_for(task, 8)
        with patch.object(runtime_module, '_default_modes', lambda: mode), \\
             patch.object(runtime_module, '_default_activity', lambda: activity), \\
             patch.object(runtime_module, '_default_todo', lambda: todo):
            asyncio.run(scenario())
    ''')
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path)], text=True,
                            capture_output=True, timeout=30, env=os.environ.copy())
    assert result.returncode == 0, result.stdout + result.stderr
