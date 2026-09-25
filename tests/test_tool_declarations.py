"""Garde-fou : tout outil implémenté doit être DÉCLARÉ à Gemini.

Un outil présent dans ``TOOL_FUNCTIONS`` mais absent de ``TOOL_DECLARATIONS``
n'existe tout simplement pas pour le modèle : il ne peut jamais être appelé à
la voix, alors que son code fonctionne et que ses tests directs passent.

Ce test rend ce type d'oubli impossible : la parité entre implémentation et
déclaration est vérifiée, ainsi que la cohérence des paramètres. Un outil
implémenté mais non déclaré est invisible de l'assistant : aucun test
unitaire ne peut le détecter puisqu'il appelle la fonction directement.
"""

from __future__ import annotations

import inspect
import unittest

from src import tools

# Les 9 outils ajoutés en 1.3.0 : ils doivent rester exposés au modèle.
TOOLS_1_3_0 = frozenset(
    {
        "show_blob",
        "hide_blob",
        "show_menu",
        "hide_menu",
        "get_ui_state",
        "list_mode_applications",
        "set_mode_applications",
        "toggle_mode_application",
        "reset_mode_applications",
    }
)


def _declared_names() -> set[str]:
    return {declaration["name"] for declaration in tools.TOOL_DECLARATIONS}


class ToolDeclarationParityTests(unittest.TestCase):
    """Parité implémentation ↔ déclaration."""

    def test_tout_outil_implemente_est_declare(self) -> None:
        """Aucun outil ne doit rester invisible du modèle."""
        manquants = sorted(set(tools.TOOL_FUNCTIONS) - _declared_names())
        self.assertEqual(
            manquants,
            [],
            f"Outils implémentés mais non déclarés à Gemini (donc jamais "
            f"appelables à la voix) : {manquants}",
        )

    def test_tout_outil_declare_est_implemente(self) -> None:
        """Aucune déclaration ne doit pointer vers une fonction inexistante."""
        inconnus = sorted(_declared_names() - set(tools.TOOL_FUNCTIONS))
        self.assertEqual(
            inconnus,
            [],
            f"Outils déclarés à Gemini mais non implémentés : {inconnus}",
        )

    def test_noms_declares_uniques(self) -> None:
        noms = [declaration["name"] for declaration in tools.TOOL_DECLARATIONS]
        doublons = sorted({nom for nom in noms if noms.count(nom) > 1})
        self.assertEqual(doublons, [], f"Outils déclarés en double : {doublons}")

    def test_outils_1_3_0_toujours_declares(self) -> None:
        """Les commandes d'affichage et la configuration des modes restent exposées."""
        manquants = sorted(TOOLS_1_3_0 - _declared_names())
        self.assertEqual(manquants, [], f"Outils 1.3.0 disparus des déclarations : {manquants}")


class ToolDeclarationSchemaTests(unittest.TestCase):
    """Cohérence des schémas de paramètres envoyés à l'API."""

    def test_parametres_declares_conformes_aux_signatures(self) -> None:
        for declaration in tools.TOOL_DECLARATIONS:
            nom = declaration["name"]
            fonction = tools.TOOL_FUNCTIONS.get(nom)
            if fonction is None:  # couvert par le test de parité
                continue
            parametres = set(inspect.signature(fonction).parameters)
            proprietes = set(declaration.get("parameters", {}).get("properties", {}))
            with self.subTest(outil=nom):
                inconnus = sorted(proprietes - parametres)
                self.assertEqual(
                    inconnus,
                    [],
                    f"{nom} : paramètres déclarés absents de la signature {inconnus}",
                )

    def test_requis_sont_des_parametres_declares(self) -> None:
        for declaration in tools.TOOL_DECLARATIONS:
            nom = declaration["name"]
            parametres = declaration.get("parameters", {})
            requis = set(parametres.get("required", []))
            proprietes = set(parametres.get("properties", {}))
            with self.subTest(outil=nom):
                self.assertTrue(
                    requis <= proprietes,
                    f"{nom} : paramètres requis non décrits : {sorted(requis - proprietes)}",
                )

    def test_chaque_declaration_a_un_nom_et_une_description(self) -> None:
        for declaration in tools.TOOL_DECLARATIONS:
            with self.subTest(outil=declaration.get("name", "<sans nom>")):
                self.assertTrue(declaration.get("name"))
                self.assertTrue(
                    str(declaration.get("description", "")).strip(),
                    "une description vide empêche le modèle de savoir quand appeler l'outil",
                )
                self.assertEqual(declaration.get("parameters", {}).get("type"), "object")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
