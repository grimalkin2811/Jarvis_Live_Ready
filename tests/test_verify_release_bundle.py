"""Tests du vérificateur de contenu de bundle publié.

Ces tests verrouillent le mécanisme qui a manqué lors de la publication de
la v1.5.0 : savoir lire l'archive PYZ d'un binaire PyInstaller et détecter
l'absence des symboles du contrat UI / Writing Mode / Deezer.
"""

from __future__ import annotations

import importlib.util
import marshal
import os
import struct
import sys
import unittest
import zlib

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "verify_release_bundle.py")

_spec = importlib.util.spec_from_file_location("verify_release_bundle", SCRIPT)
verify_release_bundle = importlib.util.module_from_spec(_spec)
sys.modules["verify_release_bundle"] = verify_release_bundle
_spec.loader.exec_module(verify_release_bundle)


def build_pyz(modules: dict[str, str]) -> bytes:
    """Construit une archive PYZ minimale au format PyInstaller."""
    payloads: dict[str, bytes] = {}
    for name, source in modules.items():
        payloads[name] = zlib.compress(marshal.dumps(compile(source, name, "exec")))

    body = b""
    toc: dict[str, tuple[int, int, int]] = {}
    offset = 16
    for name, blob in payloads.items():
        toc[name] = (0, offset + len(body), len(blob))
        body += blob
    toc_offset = 16 + len(body)
    header = b"PYZ\0" + b"\x00" * 4 + struct.pack("!i", toc_offset) + b"\x00" * 4
    return header + body + marshal.dumps(toc)


class ParsePyzTests(unittest.TestCase):
    def test_lit_les_modules_et_leur_contenu(self) -> None:
        blob = build_pyz(
            {
                "UI.jarvis_menu": "def _draw_hover_background():\n    pass\n",
                "src.version": '__version__ = "1.5.1"\n',
            }
        )
        entries = verify_release_bundle._parse_pyz(blob)
        self.assertEqual(sorted(entries), ["UI.jarvis_menu", "src.version"])

    def test_rejette_un_entete_inattendu(self) -> None:
        with self.assertRaises(RuntimeError):
            verify_release_bundle._parse_pyz(b"NOPE" + b"\x00" * 32)


class CodeStringsTests(unittest.TestCase):
    def _strings(self, source: str) -> set[str]:
        data = marshal.dumps(compile(source, "<test>", "exec"))
        return verify_release_bundle._code_strings(data)

    def test_trouve_les_fonctions_imbriquees(self) -> None:
        strings = self._strings(
            "class Menu:\n"
            "    def _draw_hover_background(self):\n"
            "        label = 'Active Field'\n"
            "        return label\n"
        )
        self.assertIn("_draw_hover_background", strings)
        self.assertIn("Active Field", strings)

    def test_trouve_les_attributs_et_constantes(self) -> None:
        strings = self._strings("def f(cfg):\n    return cfg.item_bg_opacity\n")
        self.assertIn("item_bg_opacity", strings)

    def test_signale_un_symbole_absent(self) -> None:
        strings = self._strings("def autre():\n    pass\n")
        self.assertNotIn("_solve_layout_spacings", strings)

    def test_donnees_illisibles_ne_levent_pas(self) -> None:
        self.assertEqual(verify_release_bundle._code_strings(b"\x00\x01\x02"), set())


class ContractTests(unittest.TestCase):
    def test_le_contrat_couvre_les_trois_regressions_de_la_v150(self) -> None:
        required = verify_release_bundle.REQUIRED_MODULES
        self.assertIn("UI.jarvis_menu", required)
        self.assertIn("src.writing.active_field", required)
        self.assertIn("src.music.providers.deezer", required)

        symbols = verify_release_bundle.REQUIRED_SYMBOLS["UI.jarvis_menu"]
        for marker in (
            "_draw_hover_background",
            "item_bg_opacity",
            "_solve_layout_spacings",
        ):
            self.assertIn(marker, symbols)

    def test_les_symboles_attendus_existent_dans_la_source_courante(self) -> None:
        """Le contrat doit rester aligné avec le code réel de l'arbre."""
        for module, symbols in verify_release_bundle.REQUIRED_SYMBOLS.items():
            path = os.path.join(REPO_ROOT, module.replace(".", os.sep) + ".py")
            self.assertTrue(os.path.isfile(path), f"{module} introuvable")
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            for symbol in symbols:
                self.assertIn(symbol, source, f"{symbol} absent de {module}")

    def test_tous_les_modules_requis_existent_dans_l_arbre(self) -> None:
        for module in verify_release_bundle.REQUIRED_MODULES:
            base = os.path.join(REPO_ROOT, module.replace(".", os.sep))
            self.assertTrue(
                os.path.isfile(base + ".py") or os.path.isfile(os.path.join(base, "__init__.py")),
                f"{module} introuvable dans l'arbre source",
            )


if __name__ == "__main__":
    unittest.main()
