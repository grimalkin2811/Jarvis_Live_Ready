from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src import tools


@pytest.mark.parametrize("name", ["shutdown_pc", "restart_pc"])
def test_failed_system_command_is_not_reported_as_success(name):
    with patch.object(tools, "IS_WINDOWS", True), patch.object(
        tools, "_run", return_value=SimpleNamespace(returncode=1, stderr="access denied", stdout="")
    ):
        result = getattr(tools, name)(confirm=True)
    assert result["success"] is False
    assert "access denied" in result["error"]
