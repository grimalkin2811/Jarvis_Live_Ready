"""Conversion du contexte conversationnel vers les formats des fournisseurs.

Le contexte appartient à Jarvis ; chaque fournisseur n'en reçoit qu'une
projection dans SON format natif :

* Gemini  -> ``[{"role": "user"|"model", "parts": [{"text": ...}]}]``
* Ollama  -> ``[{"role": "system"|"user"|"assistant"|"tool", "content": ...}]``

Ces tests vérifient la fidélité de la conversion, la stabilité entre les deux
fournisseurs et le comportement lors d'un changement de fournisseur.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.conversation import (  # noqa: E402
    PROVIDER_ADAPTERS,
    ConversationContext,
    normalize_provider,
    to_gemini_contents,
    to_ollama_messages,
)


def _conversation() -> ConversationContext:
    context = ConversationContext()
    context.add_user_message("Cherche Daft Punk")
    context.add_tool_call("music_search", {"query": "Daft Punk", "limit": 8})
    context.add_tool_result(
        "music_search",
        {
            "success": True,
            "message": "J'ai trouvé plusieurs résultats.",
            "results": {
                "tracks": [
                    {"title": "Around the World", "artist": "Daft Punk"},
                    {"title": "One More Time", "artist": "Daft Punk"},
                ]
            },
        },
    )
    context.add_assistant_message("J'ai trouvé deux morceaux.")
    context.add_user_message("Lance le deuxième")
    return context


class GeminiAdapterTests(unittest.TestCase):
    def test_roles_natifs_user_et_model(self) -> None:
        context = ConversationContext()
        context.add_user_message("Bonjour")
        context.add_assistant_message("Bonsoir")
        contents = to_gemini_contents(context.get_messages())
        self.assertEqual([c["role"] for c in contents], ["user", "model"])
        self.assertEqual(contents[0]["parts"][0]["text"], "Bonjour")
        self.assertEqual(contents[1]["parts"][0]["text"], "Bonsoir")

    def test_structure_parts_et_non_concatenation(self) -> None:
        contents = to_gemini_contents(_conversation().get_messages())
        for content in contents:
            self.assertIn("role", content)
            self.assertIsInstance(content["parts"], list)
            for part in content["parts"]:
                self.assertIsInstance(part, dict)
                self.assertIsInstance(part["text"], str)

    def test_alternance_des_roles(self) -> None:
        contents = to_gemini_contents(_conversation().get_messages())
        roles = [content["role"] for content in contents]
        self.assertEqual(roles, ["user", "model", "user", "model", "user"])
        for previous, current in zip(roles, roles[1:]):
            self.assertNotEqual(previous, current)

    def test_appel_outil_cote_model_et_resultat_cote_user(self) -> None:
        context = ConversationContext()
        context.add_user_message("Cherche X")
        context.add_tool_call("music_search", {"query": "X"})
        context.add_tool_result("music_search", {"success": True, "message": "ok"})
        contents = to_gemini_contents(context.get_messages())
        self.assertEqual(contents[1]["role"], "model")
        self.assertIn("[appel outil]", contents[1]["parts"][0]["text"])
        self.assertEqual(contents[2]["role"], "user")
        self.assertIn("[résultat outil]", contents[2]["parts"][0]["text"])

    def test_messages_consecutifs_de_meme_role_fusionnes_en_parts(self) -> None:
        context = ConversationContext()
        context.add_user_message("Cherche X")
        context.add_tool_result("music_search", {"success": True, "message": "ok"})
        context.add_user_message("Et maintenant ?")
        contents = to_gemini_contents(context.get_messages())
        self.assertEqual([c["role"] for c in contents], ["user"])
        self.assertEqual(len(contents[0]["parts"]), 3)

    def test_contexte_vide_donne_une_liste_vide(self) -> None:
        self.assertEqual(to_gemini_contents(ConversationContext().get_messages()), [])

    def test_ordre_preserve(self) -> None:
        contents = to_gemini_contents(_conversation().get_messages())
        textes = [part["text"] for content in contents for part in content["parts"]]
        self.assertEqual(textes[0], "Cherche Daft Punk")
        self.assertEqual(textes[-1], "Lance le deuxième")
        self.assertTrue(any("1. Around the World" in texte for texte in textes))
        self.assertTrue(any("2. One More Time" in texte for texte in textes))


class OllamaAdapterTests(unittest.TestCase):
    def test_prompt_systeme_en_tete(self) -> None:
        messages = to_ollama_messages(
            _conversation().get_messages(), system_prompt="Tu es Jarvis."
        )
        self.assertEqual(messages[0], {"role": "system", "content": "Tu es Jarvis."})

    def test_sans_prompt_systeme(self) -> None:
        messages = to_ollama_messages(_conversation().get_messages())
        self.assertNotEqual(messages[0]["role"], "system")

    def test_roles_natifs(self) -> None:
        messages = to_ollama_messages(_conversation().get_messages())
        self.assertEqual(
            [message["role"] for message in messages],
            ["user", "assistant", "tool", "assistant", "user"],
        )

    def test_appel_outil_structure(self) -> None:
        messages = to_ollama_messages(_conversation().get_messages())
        call = messages[1]
        self.assertIn("tool_calls", call)
        function = call["tool_calls"][0]["function"]
        self.assertEqual(function["name"], "music_search")
        self.assertEqual(function["arguments"]["query"], "Daft Punk")
        self.assertEqual(function["arguments"]["limit"], 8)

    def test_resultat_outil_role_tool(self) -> None:
        messages = to_ollama_messages(_conversation().get_messages())
        result = messages[2]
        self.assertEqual(result["role"], "tool")
        self.assertEqual(result["name"], "music_search")
        self.assertIn("1. Around the World", result["content"])

    def test_contexte_vide(self) -> None:
        self.assertEqual(to_ollama_messages(ConversationContext().get_messages()), [])
        self.assertEqual(
            to_ollama_messages(ConversationContext().get_messages(), system_prompt="S"),
            [{"role": "system", "content": "S"}],
        )


class ProviderSwitchTests(unittest.TestCase):
    def test_meme_conversation_dans_les_deux_formats(self) -> None:
        context = _conversation()
        gemini = context.messages_for_provider("gemini")
        ollama = context.messages_for_provider("ollama")
        gemini_text = " ".join(
            part["text"] for content in gemini for part in content["parts"]
        )
        ollama_text = " ".join(str(message.get("content", "")) for message in ollama)
        for extrait in ("Cherche Daft Punk", "Lance le deuxième", "1. Around the World"):
            self.assertIn(extrait, gemini_text)
            self.assertIn(extrait, ollama_text)

    def test_changement_de_provider_ne_perd_pas_le_contexte(self) -> None:
        context = _conversation()
        avant = len(context.get_messages())
        context.set_provider("ollama")
        self.assertEqual(len(context.get_messages()), avant)
        self.assertEqual(len(context.messages_for_provider()), 5)
        context.set_provider("gemini")
        self.assertEqual(len(context.get_messages()), avant)
        self.assertEqual(
            [content["role"] for content in context.messages_for_provider()],
            ["user", "model", "user", "model", "user"],
        )

    def test_conversion_explicite_independante_du_provider_courant(self) -> None:
        context = _conversation()
        self.assertEqual(context.provider, "gemini")
        ollama = context.messages_for_provider("ollama")
        self.assertEqual(context.provider, "gemini")
        self.assertEqual(ollama[0]["role"], "user")

    def test_provider_inconnu_retombe_sur_le_defaut(self) -> None:
        self.assertEqual(normalize_provider("inconnu"), "gemini")
        self.assertEqual(normalize_provider(None), "gemini")
        self.assertEqual(normalize_provider("OLLAMA"), "ollama")

    def test_table_des_adaptateurs(self) -> None:
        self.assertEqual(sorted(PROVIDER_ADAPTERS), ["gemini", "ollama"])


if __name__ == "__main__":
    unittest.main()
