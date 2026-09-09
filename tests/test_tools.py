"""Tests rapides de la boîte à outils Jarvis.

Ces tests sont volontairement multiplateformes : ils ne déclenchent aucune
action réelle sur le PC (pas d'ouverture d'application ni de navigateur).

    python -m unittest discover tests
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _ci_diag  # noqa: E402,F401  (diagnostic CI temporaire)

from src import tools  # noqa: E402


class TestRegistry(unittest.TestCase):
    def test_declarations_match_functions(self):
        declared = {d["name"] for d in tools.TOOL_DECLARATIONS}
        self.assertEqual(declared, set(tools.TOOL_FUNCTIONS))

    def test_declaration_schema(self):
        for decl in tools.TOOL_DECLARATIONS:
            self.assertTrue(decl["description"])
            params = decl["parameters"]
            self.assertEqual(params["type"], "object")
            for required in params["required"]:
                self.assertIn(required, params["properties"])

    def test_callables(self):
        for name, fn in tools.TOOL_FUNCTIONS.items():
            self.assertTrue(callable(fn), name)


class TestWhitelists(unittest.TestCase):
    def test_common_sites_present(self):
        for site in ("wikipedia", "youtube", "google", "github", "gmail", "netflix"):
            self.assertIn(site, tools.SITES)

    def test_site_urls_are_https(self):
        for name, url in tools.SITES.items():
            self.assertTrue(url.startswith("https://"), name)

    def test_aliases_point_to_known_sites(self):
        for alias, target in tools.SITE_ALIASES.items():
            self.assertIn(target, tools.SITES, alias)

    def test_lookup_is_tolerant(self):
        cases = {
            "YouTube": "youtube",
            "wikipédia": "wikipedia",
            "yt": "youtube",
            "chat gpt": "chatgpt",
            "ouvre wikipedia": "wikipedia",
        }
        for query, expected in cases.items():
            self.assertEqual(tools._lookup(tools.SITES, query, tools.SITE_ALIASES)[0], expected)

    def test_unknown_site_rejected(self):
        self.assertFalse(tools.open_website("site-inexistant-zzz")["success"])

    def test_url_domain_filter(self):
        self.assertTrue(tools._is_domain_allowed("en.wikipedia.org"))
        self.assertTrue(tools._is_domain_allowed("www.youtube.com"))
        self.assertFalse(tools._is_domain_allowed("evil.example.com"))
        self.assertFalse(tools.open_url("http://evil.example.com")["success"])
        self.assertFalse(tools.open_url("file:///etc/passwd")["success"])

    def test_unknown_application_rejected(self):
        self.assertFalse(tools.open_application("virus.exe")["success"])


class TestCalculate(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(tools.calculate("2+2")["resultat"], 4)
        self.assertEqual(tools.calculate("3 x 4")["resultat"], 12)
        self.assertEqual(tools.calculate("2^10")["resultat"], 1024)
        self.assertEqual(tools.calculate("sqrt(16)")["resultat"], 4)
        self.assertEqual(tools.calculate("max(3,9)")["resultat"], 9)
        self.assertEqual(tools.calculate("3,5 * 2")["resultat"], 7)
        self.assertEqual(tools.calculate("10 divisé par 4")["resultat"], 2.5)

    def test_rejects_code(self):
        for evil in ("__import__('os')", "open('x')", "[].__class__"):
            self.assertFalse(tools.calculate(evil)["success"])


class TestMisc(unittest.TestCase):
    def test_datetime_tools(self):
        for fn in (tools.get_local_time, tools.get_local_date, tools.get_datetime):
            self.assertTrue(fn()["success"])
        self.assertTrue(tools.days_until("25/12/2030")["success"])
        self.assertFalse(tools.days_until("pas une date")["success"])

    def test_random_tools(self):
        self.assertIn(tools.flip_coin()["resultat"], ("pile", "face"))
        self.assertEqual(len(tools.roll_dice(6, 3)["des"]), 3)
        self.assertTrue(1 <= tools.random_number(1, 6)["nombre"] <= 6)
        self.assertIn(tools.pick_random("a, b ou c")["choix"], ["a", "b", "c"])

    def test_notes_roundtrip(self):
        original = tools.NOTES_FILE
        tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_notes_test.json")
        tools.NOTES_FILE = tmp
        try:
            self.assertTrue(tools.take_note("test note")["success"])
            self.assertEqual(tools.read_notes()["notes"][-1]["texte"], "test note")
            self.assertFalse(tools.delete_notes()["success"])
            self.assertTrue(tools.delete_notes(confirm=True)["success"])
        finally:
            tools.NOTES_FILE = original
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_timers(self):
        created = tools.set_timer(seconds=30, label="test")
        self.assertTrue(created["success"])
        self.assertTrue(any(t["id"] == created["id"] for t in tools.list_timers()["minuteurs"]))
        self.assertTrue(tools.cancel_timer(created["id"])["success"])
        self.assertFalse(tools.set_timer(seconds=0)["success"])

    def test_dangerous_actions_need_confirmation(self):
        self.assertFalse(tools.shutdown_pc()["success"])
        self.assertFalse(tools.restart_pc()["success"])

    def test_results_are_dicts(self):
        for fn in (tools.list_applications, tools.list_websites, tools.get_system_info):
            result = fn()
            self.assertIsInstance(result, dict)
            self.assertIn("success", result)


if __name__ == "__main__":
    unittest.main()
