"""Tests de la persistance JSON locale partagée (`src/json_state.py`).

Couvre les contrats sur lesquels reposent les quatre appelants (état du menu,
de l'apparence, du système et des modes) : lecture tolérante, remplacement
atomique, jamais d'exception remontée.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.json_state import load_dict, save_dict  # noqa: E402


class LoadDictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, name: str, content: str) -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(content)
        return path

    def test_missing_file_and_empty_path_return_empty_dict(self):
        self.assertEqual(load_dict(os.path.join(self.tmp.name, "absent.json")), {})
        self.assertEqual(load_dict(""), {})
        self.assertEqual(load_dict(None), {})

    def test_corrupt_file_returns_empty_dict(self):
        self.assertEqual(load_dict(self._write("corrupt.json", "{pas du json")), {})

    def test_non_object_root_returns_empty_dict(self):
        self.assertEqual(load_dict(self._write("list.json", "[1, 2, 3]")), {})
        self.assertEqual(load_dict(self._write("str.json", '"texte"')), {})

    def test_directory_instead_of_file_returns_empty_dict(self):
        self.assertEqual(load_dict(self.tmp.name), {})

    def test_valid_object_is_returned(self):
        path = self._write("ok.json", '{"a": 1, "b": "accentué"}')
        self.assertEqual(load_dict(path), {"a": 1, "b": "accentué"})


class SaveDictTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_creates_parent_directory_and_round_trips(self):
        path = os.path.join(self.tmp.name, "sous", "dossier", "state.json")
        self.assertTrue(save_dict(path, {"volume": 42, "voix": "Féminine"}))
        with open(path, "r", encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"volume": 42, "voix": "Féminine"})
        self.assertEqual(load_dict(path), {"volume": 42, "voix": "Féminine"})

    def test_no_temporary_file_left_behind(self):
        path = os.path.join(self.tmp.name, "state.json")
        self.assertTrue(save_dict(path, {"a": 1}))
        self.assertFalse(os.path.exists(path + ".tmp"))

    def test_existing_content_is_replaced_not_merged(self):
        path = os.path.join(self.tmp.name, "state.json")
        save_dict(path, {"a": 1, "b": 2})
        save_dict(path, {"a": 1})
        self.assertEqual(load_dict(path), {"a": 1})

    def test_unwritable_target_returns_false_without_raising(self):
        # Un dossier à la place du fichier : l'écriture doit échouer proprement.
        self.assertFalse(save_dict(self.tmp.name, {"a": 1}))
        self.assertFalse(save_dict("", {"a": 1}))

    def test_non_serialisable_payload_returns_false_without_raising(self):
        path = os.path.join(self.tmp.name, "state.json")
        self.assertFalse(save_dict(path, {"objet": object()}))


if __name__ == "__main__":
    unittest.main()
