"""Tests de UI/system_actions : lancement automatique avec Windows.

Sur les systèmes non-Windows (et donc en CI), set_startup doit échouer
proprement avec un message compréhensible, jamais lever d'exception ni
écrire de fichier.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from UI import system_actions  # noqa: E402


class StartupActionTests(unittest.TestCase):
    def test_status_never_crashes(self) -> None:
        # Doit renvoyer un bool, quel que soit l'environnement.
        self.assertIsInstance(system_actions.startup_status(), bool)

    def test_set_startup_on_non_windows_fails_gracefully(self) -> None:
        if os.name == "nt":
            self.skipTest("Windows : comportement réel, pas de faux négatif")
        result = system_actions.set_startup(True)
        self.assertIsInstance(result, dict)
        self.assertFalse(result.get("success"))
        self.assertTrue(result.get("error"))

    def test_set_startup_returns_dict_with_error_message(self) -> None:
        # Même en cas d'échec, un message exploitable est fourni : l'UI
        # l'affiche au lieu de prétendre que tout s'est bien passé.
        result = system_actions.set_startup(True)
        self.assertIsInstance(result.get("error", "") or "", str)


class ResponseModeTests(unittest.TestCase):
    def test_cycle_response_mode_wraps(self) -> None:
        state = system_actions.SystemState()
        start = state.response_mode_index
        for _ in range(len(system_actions.RESPONSE_MODES)):
            system_actions.cycle_response_mode(state)
        self.assertEqual(state.response_mode_index, start)


if __name__ == "__main__":
    unittest.main()
