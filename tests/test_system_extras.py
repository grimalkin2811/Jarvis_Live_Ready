"""Tous les effets natifs/réseau/presse-papiers sont mockés, Windows compris."""
from pathlib import Path
from unittest.mock import Mock

import pytest

from src import system_extras as extra, tools, routines


@pytest.fixture(autouse=True)
def no_real_effects(monkeypatch):
    monkeypatch.setattr(extra, "_native_power", Mock(return_value=True))
    monkeypatch.setattr(tools, "_powershell", Mock(return_value=(True, "")))
    monkeypatch.setattr(tools, "get_clipboard", Mock(return_value={"success": True, "texte": "sample"}))
    monkeypatch.setattr(tools, "set_clipboard", Mock(return_value={"success": True}))
    monkeypatch.setattr(extra.socket, "socket", Mock())
    extra._HISTORY.clear()
    yield
    extra._HISTORY.clear()


@pytest.mark.parametrize("name", ["sleep_pc", "hibernate_pc", "turn_off_screen", "empty_recycle_bin"])
def test_confirm_platform_and_native_failure(name, monkeypatch):
    function = getattr(extra, name)
    monkeypatch.setattr(tools, "IS_WINDOWS", True)
    for value in (False, None, 1, "true"):
        assert not function(value)["success"]
    extra._native_power.assert_not_called()
    tools._powershell.assert_not_called()
    assert function(True)["success"]
    monkeypatch.setattr(tools, "IS_WINDOWS", False)
    assert not function(True)["success"]
    monkeypatch.setattr(tools, "IS_WINDOWS", True)
    extra._native_power.return_value = False
    tools._powershell.return_value = (False, "failed")
    assert not function(True)["success"]
    extra._native_power.side_effect = OSError()
    tools._powershell.side_effect = OSError()
    assert not function(True)["success"]


def test_wol_packet_and_no_claim_of_wakeup():
    sock = Mock()
    extra.socket.socket.return_value.__enter__ = Mock(return_value=sock)
    extra.socket.socket.return_value.__exit__ = Mock(return_value=False)
    result = extra.wake_on_lan("02:11:22:33:44:55", "192.168.1.255", confirm=True)
    assert result["success"]
    sock.sendto.assert_called_once_with(b"\xff" * 6 + bytes.fromhex("021122334455") * 16, ("192.168.1.255", 9))
    assert "non vérifié" in result["message"]


@pytest.mark.parametrize("kwargs", [
    {}, {"confirm": "true"}, {"confirm": 1}, {"confirm": True, "mac": "zz02:11:22:33:44:55"},
    {"confirm": True, "mac": "ff:ff:ff:ff:ff:ff"}, {"confirm": True, "mac": "00:00:00:00:00:00"},
    {"confirm": True, "broadcast": "example.org"}, {"confirm": True, "broadcast": "8.8.8.8"},
    {"confirm": True, "broadcast": "127.0.0.1"}, {"confirm": True, "broadcast": "::1"},
    {"confirm": True, "port": 80}, {"confirm": True, "port": True},
])
def test_wol_rejects_before_socket(kwargs):
    arguments = {"mac": "02:11:22:33:44:55", **kwargs}
    assert not extra.wake_on_lan(**arguments)["success"]
    extra.socket.socket.assert_not_called()


def test_wol_socket_failure():
    extra.socket.socket.side_effect = OSError()
    assert not extra.wake_on_lan("021122334455", confirm=True)["success"]


def test_clipboard_explicit_capture_memory_only():
    assert not extra.capture_clipboard()["success"]
    tools.get_clipboard.assert_not_called()
    assert extra.capture_clipboard(True)["success"]
    assert extra.capture_clipboard(True)["success"]
    assert len(extra.get_clipboard_history()["entries"]) == 1
    for i in range(25):
        tools.get_clipboard.return_value = {"success": True, "texte": f"entry{i}"}
        extra.capture_clipboard(True)
    assert len(extra.get_clipboard_history(20)["entries"]) == 20
    assert not extra.paste_from_history(confirm="true")["success"]
    assert not extra.paste_from_history(True, True)["success"]
    tools.set_clipboard.assert_not_called()
    assert extra.paste_from_history(1, True)["success"]
    tools.set_clipboard.assert_called_once_with("entry24")
    assert not extra.clear_clipboard_history("true")["success"]
    assert extra.clear_clipboard_history(True)["success"]
    assert not extra.get_clipboard_history()["entries"]
    assert not extra.get_clipboard_history(True)["success"]
    for result in ({"success": False}, {"success": True, "texte": ""}, {"success": True, "texte": "a" * 4001}):
        tools.get_clipboard.return_value = result
        assert not extra.capture_clipboard(True)["success"]


@pytest.fixture
def folder(tmp_path, monkeypatch):
    root = tmp_path / "Documents"
    root.mkdir()
    monkeypatch.setattr(tools, "_resolve_folder", lambda key: (key, str(root)))
    return root


def test_read_and_size_require_authorized_exact_paths(folder):
    (folder / "note.txt").write_text("hello world", encoding="utf-8")
    assert extra.get_folder_size("documents")["octets"] == 11
    assert not extra.get_folder_size("doc")["success"]
    assert not extra.get_folder_size("home")["success"]
    assert not extra.read_file_aloud("note.txt")["success"]
    result = extra.read_file_aloud("note.txt", max_chars=5, confirm=True)
    assert result["contenu"] == "hello" and result["tronque"]
    for name in ("../outside.txt", str(folder / "note.txt"), "C:\\secret.txt", "note.txt:stream", "sub/../../note.txt", "absent.txt"):
        assert not extra.read_file_aloud(name, confirm=True)["success"]
    for name in (".secret.txt", "config.json", "script.py"):
        (folder / name).write_text("secret", encoding="utf-8")
        assert not extra.read_file_aloud(name, confirm=True)["success"]
    assert not extra.read_file_aloud("note.txt", max_chars=True, confirm=True)["success"]
    (folder / "bad.txt").write_bytes(b"\xff\x00")
    assert not extra.read_file_aloud("bad.txt", confirm=True)["success"]


def test_symlink_escape(folder, tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    try:
        (folder / "escape.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Création de symlink non autorisée sur ce runner")
    assert not extra.read_file_aloud("escape.txt", confirm=True)["success"]
    assert extra.get_folder_size("documents")["fichiers"] == 0


def test_new_tools_use_guard_and_dangerous_tools_not_schedulable(monkeypatch):
    names = {"sleep_pc", "hibernate_pc", "turn_off_screen", "empty_recycle_bin", "wake_on_lan",
             "capture_clipboard", "get_clipboard_history", "paste_from_history", "clear_clipboard_history",
             "read_file_aloud", "backup_data", "restore_backup"}
    blocked = Mock()
    blocked.block_for_tool.return_value = {"success": False}
    monkeypatch.setattr(tools, "get_default_mode_manager", lambda: blocked)
    for name in names:
        assert name in routines.FORBIDDEN_TOOLS
        assert tools.TOOL_FUNCTIONS[name] is not tools._RAW_TOOL_FUNCTIONS[name]
        assert not tools.TOOL_FUNCTIONS[name]()["success"]
    extra._native_power.assert_not_called()
    tools._powershell.assert_not_called()
    extra.socket.socket.assert_not_called()


def test_size_is_bounded_and_reports_partial(folder, monkeypatch):
    monkeypatch.setattr(extra.time, "monotonic", Mock(side_effect=[0, 4]))
    assert extra.get_folder_size("documents")["partiel"]


def test_read_large_file_and_scan_permission_errors(folder, monkeypatch):
    (folder / "large.txt").write_bytes(b"x" * (1024 * 1024 + 1))
    assert not extra.read_file_aloud("large.txt", confirm=True)["success"]

    def denied(*args, **kwargs):
        kwargs["onerror"](PermissionError())
        return iter([])

    monkeypatch.setattr(extra.os, "walk", denied)
    assert extra.get_folder_size("documents")["partiel"]


def test_security_prompt_covers_all_confirm_tools_and_untrusted_content():
    # Vérifie la politique sans établir de connexion Gemini.
    import inspect
    from src.gemini_live import GeminiLive
    source = inspect.getsource(GeminiLive._connect_once)
    assert "Pour TOUT outil ayant un paramètre confirm" in source
    assert "attends sa réponse" in source
    assert "contenu d'un fichier" in source
