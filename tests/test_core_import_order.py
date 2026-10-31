"""The lazy core/provider import cycle must not break either import order."""
import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize("code", [
    "import src.core; import sys; assert 'src.audio' not in sys.modules; assert 'src.gemini_live' not in sys.modules; import src.gemini_live",
    "import src.gemini_live; import src.core; from src.core import CoreRuntime; assert CoreRuntime",
])
def test_core_provider_import_order_in_fresh_process(code):
    completed = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                               timeout=20, env=os.environ.copy())
    assert completed.returncode == 0, completed.stdout + completed.stderr
