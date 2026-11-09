"""Outils world_* — enregistrement Gemini, garde-fous, headless."""

from __future__ import annotations

import unittest

from src import tools
from src.gods_eye import tools as world_tools


class RegistrationTests(unittest.TestCase):
    def test_les_six_outils_sont_declares(self):
        declared = {d["name"] for d in tools.TOOL_DECLARATIONS}
        for name in world_tools.WORLD_TOOL_FUNCTIONS:
            self.assertIn(name, declared, f"{name} absent des déclarations Gemini")

    def test_les_six_outils_sont_exposes(self):
        for name in world_tools.WORLD_TOOL_FUNCTIONS:
            self.assertIn(name, tools.TOOL_FUNCTIONS)

    def test_pas_de_collision_de_noms(self):
        names = [d["name"] for d in tools.TOOL_DECLARATIONS]
        self.assertEqual(len(names), len(set(names)))

    def test_declarations_sdk_compatibles(self):
        import sys

        pytest = __import__("pytest")
        genai_types = pytest.importorskip("google.genai.types")
        for declaration in world_tools.WORLD_TOOL_DECLARATIONS:
            genai_types.FunctionDeclaration(**declaration)

    def test_signatures_coherentes_avec_declarations(self):
        import inspect

        for declaration in world_tools.WORLD_TOOL_DECLARATIONS:
            function = world_tools.WORLD_TOOL_FUNCTIONS[declaration["name"]]
            signature = set(inspect.signature(function).parameters)
            properties = set(declaration["parameters"].get("properties", {}))
            self.assertTrue(properties <= signature, declaration["name"])


class HeadlessBehaviorTests(unittest.TestCase):
    """Sans QApplication : les outils répondent proprement, jamais d'exception."""

    def test_world_enable_layer_headless(self):
        try:
            from PySide6.QtWidgets import QApplication

            app_exists = QApplication.instance() is not None
        except Exception:
            app_exists = False
        result = world_tools.world_enable_layer("flights")
        self.assertIn("success", result)
        if not app_exists:
            self.assertFalse(result["success"])
            self.assertEqual(result["error"], "world_unavailable")
        else:  # pragma: no cover - contexte CI avec Qt
            self.assertTrue(result["success"])

    def test_world_disable_layer_headless_noop_idempotent(self):
        try:
            from PySide6.QtWidgets import QApplication

            app_exists = QApplication.instance() is not None
        except Exception:
            app_exists = False
        result = world_tools.world_disable_layer("flights")
        self.assertTrue(result["success"])
        self.assertEqual(result["changed"], False)

    def test_world_get_state_headless(self):
        result = world_tools.world_get_state()
        self.assertTrue(result["success"])
        self.assertIn("state", result)
        self.assertIn("layers", result["state"])

    def test_world_set_view_headless(self):
        result = world_tools.world_set_view("globe")
        self.assertTrue(result["success"])

    def test_world_focus_region_invalide_ne_leve_pas(self):
        result = world_tools.world_focus_region("atlantis")
        self.assertFalse(result["success"])
        self.assertIn("error", result)

    def test_world_set_camera_latitude_invalide(self):
        try:
            from PySide6.QtWidgets import QApplication

            app_exists = QApplication.instance() is not None
        except Exception:
            app_exists = False
        result = world_tools.world_set_camera(999, 2.0)
        if app_exists:  # pragma: no cover
            self.assertFalse(result["success"])
        else:
            self.assertFalse(result["success"])


class DeclarationQualityTests(unittest.TestCase):
    def test_chaque_declaration_a_nom_description_objet(self):
        for declaration in world_tools.WORLD_TOOL_DECLARATIONS:
            self.assertTrue(declaration["name"].startswith("world_"))
            self.assertTrue(declaration["description"].strip())
            self.assertEqual(declaration["parameters"]["type"], "object")

    def test_enums_fermes(self):
        enable = next(d for d in world_tools.WORLD_TOOL_DECLARATIONS if d["name"] == "world_enable_layer")
        self.assertEqual(enable["parameters"]["properties"]["layer_id"]["enum"], ["flights"])
        focus = next(d for d in world_tools.WORLD_TOOL_DECLARATIONS if d["name"] == "world_focus_region")
        self.assertIn("paris", focus["parameters"]["properties"]["region"]["enum"])


if __name__ == "__main__":
    unittest.main()
