"""Console entry point delegates composition to the shared runtime."""
import asyncio
from unittest.mock import patch

from src import main


def test_console_delegates_to_runtime():
    config = object()
    captured = {}

    class FakeRuntime:
        def __init__(self, actual_config, hooks):
            captured["config"] = actual_config
            captured["hooks"] = hooks

        async def run(self):
            captured["run"] = True

    with patch("src.config.load_config", return_value=config), patch("src.core.CoreRuntime", FakeRuntime), \
            patch.object(main, "_load_menu_bridge"), patch.object(main, "MENU_LIVE", None):
        asyncio.run(main.run_headless())
    assert captured["config"] is config
    assert captured["run"] is True
    assert captured["hooks"].reason == "démarrage console"
