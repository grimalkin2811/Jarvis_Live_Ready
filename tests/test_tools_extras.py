"""Tests des nouveaux outils Jarvis (clavier, système, todo, web, sauvegarde).

Comme ``test_tools.py``, ces tests sont multiplateformes et ne déclenchent
aucune action réelle : sous Linux/macOS les outils Windows renvoient une erreur
propre, et on vérifie surtout les garde-fous (listes blanches, confirmations,
validation des entrées).

    python -m unittest discover tests
"""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import tools  # noqa: E402
from src.todo import TodoManager, set_default_todo_manager  # noqa: E402


class TestKeyboard(unittest.TestCase):
    def test_key_combo_parsing(self):
        mods, main, unknown = tools._parse_key_combo("ctrl+s")
        self.assertIsNone(unknown)
        self.assertEqual(mods, [tools.MODIFIERS["ctrl"]])
        self.assertEqual(main, tools.KEYS["s"])

    def test_key_combo_with_modifier_argument(self):
        mods, main, unknown = tools._parse_key_combo("entree", modifiers="ctrl+maj")
        self.assertIsNone(unknown)
        self.assertEqual(sorted(mods), sorted([tools.MODIFIERS["ctrl"], tools.MODIFIERS["maj"]]))
        self.assertEqual(main, tools.KEYS["entree"])

    def test_unknown_key_rejected(self):
        _, _, unknown = tools._parse_key_combo("autodestruction")
        self.assertEqual(unknown, "autodestruction")
        self.assertFalse(tools.press_key("autodestruction")["success"])

    def test_type_text_rejects_empty_and_too_long(self):
        self.assertFalse(tools.type_text("   ")["success"])
        self.assertFalse(tools.type_text("a" * (tools.MAX_TYPE_LENGTH + 1))["success"])


class TestSystemExtras(unittest.TestCase):
    def test_recycle_bin_needs_confirmation(self):
        self.assertFalse(tools.empty_recycle_bin()["success"])

    def test_folder_size_rejects_unknown_folder(self):
        self.assertFalse(tools.get_folder_size("c:/windows/system32")["success"])

    def test_folder_size_on_allowed_folder(self):
        result = tools.get_folder_size("personnel")
        self.assertIsInstance(result, dict)
        self.assertIn("success", result)
        if result["success"]:
            self.assertGreaterEqual(result["taille_mo"], 0)

    def test_notification_requires_message(self):
        self.assertFalse(tools.show_notification("Jarvis", "")["success"])

    def test_wake_on_lan_validates_mac(self):
        self.assertFalse(tools.wake_on_lan("pas-une-mac")["success"])
        result = tools.wake_on_lan("aa:bb:cc:dd:ee:ff", broadcast="127.0.0.1")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["mac"], "AA:BB:CC:DD:EE:FF")


class TestClipboardHistory(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self._original = tools.CLIPBOARD_HISTORY_FILE
        tools.CLIPBOARD_HISTORY_FILE = os.path.join(self.directory.name, "clipboard.json")

    def tearDown(self):
        tools.CLIPBOARD_HISTORY_FILE = self._original
        self.directory.cleanup()

    def test_record_and_list(self):
        tools._record_clipboard("premier texte")
        tools._record_clipboard("second texte")
        tools._record_clipboard("second texte")  # doublon consécutif ignoré

        history = tools.get_clipboard_history()
        self.assertTrue(history["success"])
        self.assertEqual(history["count"], 2)
        self.assertEqual(history["historique"][0]["apercu"], "second texte")

    def test_history_is_capped(self):
        for index in range(tools.CLIPBOARD_HISTORY_MAX + 10):
            tools._record_clipboard(f"texte {index}")
        history = tools.get_clipboard_history(limit=50)
        self.assertEqual(history["total"], tools.CLIPBOARD_HISTORY_MAX)

    def test_empty_entries_ignored(self):
        self.assertIsNone(tools._record_clipboard("   "))

    def test_clear_requires_confirmation(self):
        tools._record_clipboard("à effacer")
        self.assertFalse(tools.clear_clipboard_history()["success"])
        self.assertTrue(tools.clear_clipboard_history(confirm=True)["success"])
        self.assertEqual(tools.get_clipboard_history()["count"], 0)


class TestTodoTools(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        set_default_todo_manager(TodoManager(os.path.join(self.directory.name, "todo.db")))

    def tearDown(self):
        set_default_todo_manager(None)
        self.directory.cleanup()

    def test_todo_workflow(self):
        created = tools.add_todo("réviser la présentation", priority="haute")
        self.assertTrue(created["success"])
        self.assertEqual(tools.list_todos()["count"], 1)

        done = tools.complete_todo(text="présentation")
        self.assertTrue(done["success"])
        self.assertEqual(tools.list_todos()["count"], 0)
        self.assertEqual(tools.list_todos(status="done")["count"], 1)

        self.assertFalse(tools.clear_todos()["success"])
        self.assertTrue(tools.clear_todos(confirm=True)["success"])


class TestReadFileAloud(unittest.TestCase):
    def test_rejects_file_outside_allowed_folders(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as handle:
            handle.write("secret")
            path = handle.name
        try:
            result = tools.read_file_aloud(path)
            if result["success"]:  # le dossier temporaire peut être dans le home
                self.skipTest("Fichier temporaire situé dans un dossier autorisé.")
            self.assertFalse(result["success"])
        finally:
            os.remove(path)

    def test_reads_allowed_text_file(self):
        home = os.path.expanduser("~")
        documents = os.path.join(home, "Documents")
        try:
            os.makedirs(documents, exist_ok=True)
        except Exception:
            self.skipTest("Dossier Documents indisponible.")

        path = os.path.join(documents, "_jarvis_test_lecture.txt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("Bonjour, ceci est un test de lecture.")
        try:
            result = tools.read_file_aloud(path)
            self.assertTrue(result["success"], result)
            self.assertIn("test de lecture", result["contenu"])
            self.assertFalse(result["tronque"])
        finally:
            os.remove(path)

    def test_rejects_unknown_file(self):
        self.assertFalse(tools.read_file_aloud("fichier-qui-nexiste-pas-zzz.txt")["success"])


class TestEmailAndWeb(unittest.TestCase):
    def test_draft_email_validates_address(self):
        self.assertFalse(tools.draft_email("pas-une-adresse", "Objet", "Corps")["success"])

    def test_draft_email_requires_content(self):
        self.assertFalse(tools.draft_email("paul@example.com", "", "")["success"])

    def test_watch_providers_are_known(self):
        key, code = tools._lookup(tools.WATCH_PROVIDERS, "netflix")
        self.assertEqual(key, "netflix")
        self.assertEqual(code, "nfx")

    def test_wmo_codes_translated(self):
        self.assertEqual(tools.WMO_CODES[0], "ciel dégagé")
        self.assertIn(95, tools.WMO_CODES)


class TestRegistryStaysConsistent(unittest.TestCase):
    def test_new_tools_declared(self):
        declared = {d["name"] for d in tools.TOOL_DECLARATIONS}
        for name in (
            "type_text",
            "press_key",
            "sleep_pc",
            "hibernate_pc",
            "turn_off_screen",
            "empty_recycle_bin",
            "get_folder_size",
            "show_notification",
            "wake_on_lan",
            "get_clipboard_history",
            "paste_from_history",
            "clear_clipboard_history",
            "add_todo",
            "list_todos",
            "complete_todo",
            "reopen_todo",
            "delete_todo",
            "clear_todos",
            "read_file_aloud",
            "draft_email",
            "get_forecast",
            "find_something_to_watch",
            "get_activity_log",
            "clear_activity_log",
            "backup_data",
            "list_backups",
        ):
            self.assertIn(name, declared, name)
            self.assertIn(name, tools.TOOL_FUNCTIONS, name)

    def test_destructive_tools_forbidden_in_routines(self):
        from src.routines import FORBIDDEN_TOOLS, available_tools

        for name in ("empty_recycle_bin", "hibernate_pc", "clear_todos", "clear_activity_log"):
            self.assertIn(name, FORBIDDEN_TOOLS)
            self.assertNotIn(name, available_tools())

    def test_useful_tools_allowed_in_routines(self):
        from src.routines import available_tools

        allowed = available_tools()
        for name in ("show_notification", "add_todo", "type_text", "turn_off_screen"):
            self.assertIn(name, allowed, name)


if __name__ == "__main__":
    unittest.main()
