"""Bus local et fallback Windows : aucun processus natif n'est réellement lancé."""

import base64
import json
import re
import unittest
from unittest.mock import Mock, patch

from src import notifications


class NotificationTests(unittest.TestCase):
    def test_successful_hook_prevents_duplicate_native_notification(self):
        hook = Mock()
        with (
            patch.object(notifications, "_HOOKS", []),
            patch.object(notifications, "_beep") as beep,
        ):
            notifications.add_hook(hook)
            notifications.add_hook(hook)
            result = notifications.publish("Jarvis", "Bonjour")
            self.assertEqual(result, "interface")
            hook.assert_called_once_with("Jarvis", "Bonjour")
            beep.assert_not_called()
            notifications.remove_hook(hook)
            self.assertEqual(notifications._HOOKS, [])

    def test_broken_hook_does_not_break_console_fallback(self):
        with (
            patch.object(
                notifications, "_HOOKS", [Mock(side_effect=RuntimeError("absent"))]
            ),
            patch.object(notifications.os, "name", "posix"),
            patch.object(notifications, "_beep") as beep,
        ):
            self.assertEqual(
                notifications.publish("Jarvis", "Toujours visible"), "console"
            )
            beep.assert_called_once()

    def test_windows_notification_keeps_user_text_out_of_powershell_code(self):
        message = "Une apostrophe ' ; $(commande) <b>bonjour</b>"
        with patch.object(notifications.subprocess, "run") as run:
            notifications._windows_balloon("Échéance", message)
        args = run.call_args.args[0]
        self.assertEqual(args[0], "powershell.exe")
        self.assertNotIn("shell", run.call_args.kwargs)
        self.assertEqual(run.call_args.kwargs["timeout"], 12)
        script = base64.b64decode(args[-1]).decode("utf-16le")
        self.assertNotIn(message, script)
        payload = re.search(r"FromBase64String\('([^']+)'\)", script).group(1)
        data = json.loads(base64.b64decode(payload))
        self.assertEqual(data, {"title": "Échéance", "message": message})


if __name__ == "__main__":
    unittest.main()


import _ci_diag  # noqa: E402,F401  (diagnostic CI temporaire)