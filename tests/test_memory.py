import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.memory import MemoryManager, set_default_memory_manager  # noqa: E402
from src import tools  # noqa: E402


class TestMemoryManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "memory.db")
        self.manager = MemoryManager(self.db_path, enabled=True, max_results=5, min_importance=1)

    def tearDown(self):
        set_default_memory_manager(None)
        self.tmp.cleanup()

    def test_create_and_read_memory(self):
        created = self.manager.add_memory("L'utilisateur s'appelle Simon.", category="identity", importance=5)
        self.assertTrue(created["success"])
        fetched = self.manager.get_memory(created["id"])
        self.assertTrue(fetched["success"])
        self.assertEqual(fetched["memory"]["content"], "L'utilisateur s'appelle Simon.")
        self.assertEqual(fetched["memory"]["category"], "identity")

    def test_search_memory(self):
        self.manager.add_memory("L'utilisateur développe Jarvis avec Python.", category="project", importance=4)
        result = self.manager.search_memories("Python Jarvis")
        self.assertTrue(result["success"])
        self.assertGreaterEqual(result["count"], 1)
        self.assertIn("Python", result["memories"][0]["content"])

    def test_update_memory(self):
        created = self.manager.add_memory("L'utilisateur utilise Python 3.12.", category="configuration", importance=4)
        updated = self.manager.update_memory(created["id"], content="L'utilisateur utilise Python 3.13.9.", importance=5)
        self.assertTrue(updated["success"])
        fetched = self.manager.get_memory(created["id"])
        self.assertIn("3.13.9", fetched["memory"]["content"])
        self.assertEqual(fetched["memory"]["importance"], 5)

    def test_delete_memory(self):
        created = self.manager.add_memory("Souvenir à supprimer.")
        self.assertTrue(self.manager.delete_memory(created["id"])["success"])
        self.assertFalse(self.manager.get_memory(created["id"])["success"])

    def test_extract_important_information(self):
        candidates = self.manager.extract_candidate_memories(
            "Peux-tu ouvrir YouTube ? Souviens-toi que je préfère les réponses en français."
        )
        self.assertEqual(len(candidates), 1)
        self.assertTrue(candidates[0]["explicit"])
        self.assertEqual(candidates[0]["importance"], 5)
        self.assertIn("français", candidates[0]["content"])

    def test_contradiction_updates_existing_subject(self):
        first = self.manager.add_memory("L'utilisateur utilise Python 3.12.", category="configuration", importance=4)
        second = self.manager.add_memory("L'utilisateur utilise maintenant Python 3.13.9.", category="configuration", importance=4)
        self.assertTrue(first["success"])
        self.assertTrue(second["success"])
        self.assertEqual(second["action"], "updated")
        all_memories = self.manager.list_memories()
        self.assertEqual(all_memories["count"], 1)
        self.assertIn("3.13.9", all_memories["memories"][0]["content"])

    def test_relevant_memories_for_prompt(self):
        self.manager.add_memory("L'utilisateur travaille sur un projet domotique.", category="project", importance=4)
        prompt = self.manager.relevant_memories_for_prompt("domotique")
        self.assertIn("MEMORY", prompt)
        self.assertIn("domotique", prompt)

    def test_empty_database(self):
        self.assertEqual(self.manager.list_memories()["count"], 0)
        self.assertEqual(self.manager.search_memories("rien")["count"], 0)
        self.assertEqual(self.manager.relevant_memories_for_prompt("rien"), "")

    def test_disabled_memory(self):
        disabled = MemoryManager(self.db_path, enabled=False)
        result = disabled.add_memory("Ne doit pas être écrit.")
        self.assertFalse(result["success"])
        self.assertTrue(result["disabled"])

    def test_inaccessible_database(self):
        blocker = os.path.join(self.tmp.name, "not_a_dir")
        with open(blocker, "w", encoding="utf-8") as handle:
            handle.write("blocage")
        broken = MemoryManager(os.path.join(blocker, "memory.db"), enabled=True)
        self.assertFalse(broken.available)
        result = broken.list_memories()
        self.assertFalse(result["success"])
        self.assertEqual(result["count"], 0)

    def test_memory_tools_wrappers(self):
        set_default_memory_manager(self.manager)
        created = tools.remember("L'utilisateur préfère un ton concis.", category="preference", importance=4)
        self.assertTrue(created["success"])
        listed = tools.list_memories()
        self.assertEqual(listed["count"], 1)
        found = tools.recall("ton concis")
        self.assertTrue(found["success"])
        self.assertGreaterEqual(found["count"], 1)


if __name__ == "__main__":
    unittest.main()
