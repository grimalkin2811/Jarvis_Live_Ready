"""Tests du contexte conversationnel multi-tour (v1.6.0).

Couvre le gestionnaire lui-même : création, ordre, récupération, reset,
limitation (tours + budget), compactage des résultats d'outils, tours en
échec, concurrence et performance.
"""

from __future__ import annotations

import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.conversation import (  # noqa: E402
    DEFAULT_MAX_TOKENS,
    DEFAULT_MAX_TURNS,
    KIND_TOOL_CALL,
    KIND_TOOL_RESULT,
    ROLE_ASSISTANT,
    ROLE_TOOL,
    ROLE_USER,
    ConversationContext,
    estimate_tokens,
    get_default_conversation_context,
    is_new_conversation_command,
    set_default_conversation_context,
    summarize_tool_result,
)


def _roles(context: ConversationContext) -> list[str]:
    return [message.role for message in context.get_messages()]


def _texts(context: ConversationContext) -> list[str]:
    return [message.text for message in context.get_messages()]


class CreationTests(unittest.TestCase):
    def test_contexte_neuf_est_vide(self) -> None:
        context = ConversationContext()
        self.assertTrue(context.is_empty())
        self.assertEqual(context.size(), 0)
        self.assertEqual(context.turn_count(), 0)
        self.assertEqual(context.estimated_tokens(), 0)
        self.assertEqual(context.get_messages(), ())
        self.assertFalse(context.has_open_turn())

    def test_identifiant_et_valeurs_par_defaut(self) -> None:
        context = ConversationContext()
        self.assertTrue(context.conversation_id)
        self.assertEqual(context.provider, "gemini")
        self.assertEqual(context.epoch, 0)
        self.assertEqual(context.max_turns, DEFAULT_MAX_TURNS)
        self.assertEqual(context.max_tokens, DEFAULT_MAX_TOKENS)

    def test_deux_contextes_ont_des_identifiants_distincts(self) -> None:
        self.assertNotEqual(
            ConversationContext().conversation_id,
            ConversationContext().conversation_id,
        )

    def test_contexte_desactivable(self) -> None:
        context = ConversationContext(enabled=False)
        context.add_user_message("Bonjour")
        context.add_assistant_message("Salut")
        self.assertTrue(context.is_empty())


class AddAndOrderTests(unittest.TestCase):
    def test_ajout_utilisateur_puis_assistant(self) -> None:
        context = ConversationContext()
        context.add_user_message("Quelle est la capitale de l'Australie ?")
        self.assertTrue(context.has_open_turn())
        context.add_assistant_message("Canberra.")
        self.assertFalse(context.has_open_turn())
        self.assertEqual(_roles(context), [ROLE_USER, ROLE_ASSISTANT])
        self.assertEqual(context.turn_count(), 1)

    def test_ordre_strict_sur_plusieurs_tours(self) -> None:
        context = ConversationContext()
        context.add_user_message("A")
        context.add_assistant_message("B")
        context.add_user_message("C")
        context.add_assistant_message("D")
        self.assertEqual(_texts(context), ["A", "B", "C", "D"])
        self.assertEqual(
            [m.turn for m in context.get_messages()], [1, 1, 2, 2]
        )

    def test_ordre_avec_outils_dans_le_tour(self) -> None:
        context = ConversationContext()
        context.add_user_message("Cherche Daft Punk")
        context.add_tool_call("music_search", {"query": "Daft Punk"})
        context.add_tool_result("music_search", {"success": True, "message": "3 résultats"})
        context.add_assistant_message("J'ai trouvé trois morceaux.")
        kinds = [(m.role, m.kind) for m in context.get_messages()]
        self.assertEqual(
            kinds,
            [
                (ROLE_USER, "text"),
                (ROLE_TOOL, KIND_TOOL_CALL),
                (ROLE_TOOL, KIND_TOOL_RESULT),
                (ROLE_ASSISTANT, "text"),
            ],
        )
        self.assertEqual({m.turn for m in context.get_messages()}, {1})

    def test_messages_vides_ignores(self) -> None:
        context = ConversationContext()
        self.assertIsNone(context.add_user_message(""))
        self.assertIsNone(context.add_user_message("   "))
        self.assertIsNone(context.add_assistant_message(None))
        self.assertIsNone(context.add_tool_call(""))
        self.assertTrue(context.is_empty())

    def test_extension_de_la_demande_en_cours(self) -> None:
        context = ConversationContext()
        context.add_user_message("Mets Around the World")
        context.extend_user_message("de Daft Punk")
        self.assertEqual(context.size(), 1)
        self.assertEqual(
            context.get_messages()[0].text, "Mets Around the World de Daft Punk"
        )

    def test_extension_sans_message_existant_en_cree_un(self) -> None:
        context = ConversationContext()
        context.extend_user_message("Bonjour")
        self.assertEqual(_roles(context), [ROLE_USER])

    def test_extension_apres_outil_reste_sur_le_message_utilisateur(self) -> None:
        context = ConversationContext()
        context.add_user_message("Cherche Daft")
        context.add_tool_call("music_search", {"query": "Daft"})
        context.extend_user_message("Punk")
        messages = context.get_messages()
        self.assertEqual(messages[0].text, "Cherche Daft Punk")
        self.assertEqual(messages[1].kind, KIND_TOOL_CALL)

    def test_texte_tres_long_est_borne(self) -> None:
        context = ConversationContext()
        context.add_assistant_message("mot " * 5000)
        self.assertLessEqual(len(context.get_messages()[0].text), 1200)

    def test_recuperation_limitee_aux_derniers_messages(self) -> None:
        context = ConversationContext()
        for index in range(5):
            context.add_user_message(f"u{index}")
            context.add_assistant_message(f"a{index}")
        self.assertEqual([m.text for m in context.get_messages(limit=3)], ["a3", "u4", "a4"])
        self.assertEqual(context.get_messages(limit=0), ())


class ToolSummaryTests(unittest.TestCase):
    def test_resultat_de_recherche_reste_numerote(self) -> None:
        payload = {
            "success": True,
            "message": "J'ai trouvé plusieurs résultats.",
            "results": {
                "tracks": [
                    {"id": "1", "title": "Around the World", "artist": "Daft Punk"},
                    {"id": "2", "title": "Harder Better", "artist": "Daft Punk"},
                    {"id": "3", "title": "One More Time", "artist": "Daft Punk"},
                ]
            },
        }
        summary = summarize_tool_result("music_search", payload)
        self.assertIn("1. Around the World", summary)
        self.assertIn("2. Harder Better", summary)
        self.assertTrue(summary.startswith("music_search ->"))

    def test_liste_tronquee_mais_comptee(self) -> None:
        payload = {"success": True, "items": [{"title": f"t{i}"} for i in range(12)]}
        summary = summarize_tool_result("x", payload, max_items=3, max_chars=500)
        self.assertIn("3. t2", summary)
        self.assertNotIn("4. t3", summary)
        self.assertIn("(+9)", summary)

    def test_payload_volumineux_est_borne(self) -> None:
        payload = {"success": True, "message": "ok", "blob": [{"title": "x" * 200} for _ in range(50)]}
        summary = summarize_tool_result("gros", payload)
        self.assertLessEqual(len(summary), 460)

    def test_echec_outil_est_explicite(self) -> None:
        summary = summarize_tool_result("music_play", {"success": False, "error": "Deezer injoignable"})
        self.assertIn("échec", summary)
        self.assertIn("Deezer injoignable", summary)

    def test_resultat_non_dictionnaire(self) -> None:
        self.assertIn("42", summarize_tool_result("calc", 42))

    def test_le_contexte_ne_contient_jamais_le_json_brut(self) -> None:
        context = ConversationContext()
        context.add_user_message("Cherche Daft Punk")
        context.add_tool_result(
            "music_search",
            {"success": True, "results": {"tracks": [{"title": "t", "raw": {"x": "y" * 5000}}]}},
        )
        self.assertLess(len(context.get_messages()[-1].text), 500)


class TrimmingTests(unittest.TestCase):
    def test_conversation_courte_conserve_tout(self) -> None:
        context = ConversationContext(max_turns=5, max_tokens=10_000)
        for index in range(3):
            context.add_user_message(f"question {index}")
            context.add_assistant_message(f"réponse {index}")
        self.assertEqual(context.turn_count(), 3)
        self.assertEqual(context.size(), 6)

    def test_depassement_du_nombre_de_tours(self) -> None:
        context = ConversationContext(max_turns=3, max_tokens=100_000)
        for index in range(6):
            context.add_user_message(f"question {index}")
            context.add_assistant_message(f"réponse {index}")
        self.assertEqual(context.turn_count(), 3)
        self.assertEqual(
            _texts(context),
            ["question 3", "réponse 3", "question 4", "réponse 4", "question 5", "réponse 5"],
        )

    def test_depassement_du_budget_de_tokens(self) -> None:
        context = ConversationContext(max_turns=100, max_tokens=200)
        for index in range(30):
            context.add_user_message(f"question {index} " + "détail " * 20)
            context.add_assistant_message(f"réponse {index} " + "texte " * 20)
        self.assertLessEqual(context.estimated_tokens(), 200 + 100)
        self.assertLess(context.turn_count(), 30)
        joined = " ".join(_texts(context))
        # Les échanges récents survivent, les anciens ont disparu.
        self.assertIn("question 29", joined)
        self.assertNotIn("question 0 ", joined)
        self.assertNotIn("question 15", joined)

    def test_rognage_par_tours_entiers(self) -> None:
        context = ConversationContext(max_turns=1, max_tokens=100_000)
        context.add_user_message("premier")
        context.add_tool_call("music_search", {"query": "a"})
        context.add_tool_result("music_search", {"success": True})
        context.add_assistant_message("ok")
        context.add_user_message("second")
        # Le tour 1 disparaît en bloc : aucun résultat d'outil orphelin.
        self.assertEqual(_texts(context), ["second"])

    def test_un_tour_unique_survit_meme_au_dela_du_budget(self) -> None:
        context = ConversationContext(max_turns=10, max_tokens=200)
        context.add_user_message("mot " * 2000)
        self.assertEqual(context.size(), 1)

    def test_rognage_deterministe(self) -> None:
        def build() -> list[str]:
            context = ConversationContext(max_turns=4, max_tokens=300)
            for index in range(12):
                context.add_user_message(f"question {index} " + "bla " * 10)
                context.add_assistant_message(f"réponse {index} " + "bla " * 10)
            return _texts(context)

        self.assertEqual(build(), build())

    def test_compteur_de_rognage_dans_le_snapshot(self) -> None:
        context = ConversationContext(max_turns=2, max_tokens=100_000)
        for index in range(5):
            context.add_user_message(f"q{index}")
            context.add_assistant_message(f"a{index}")
        self.assertGreater(context.snapshot()["trimmed_messages"], 0)


class ResetTests(unittest.TestCase):
    def test_reset_vide_le_contexte(self) -> None:
        context = ConversationContext()
        context.add_user_message("A")
        context.add_assistant_message("B")
        info = context.start_new_conversation(reason="test")
        self.assertTrue(context.is_empty())
        self.assertEqual(context.turn_count(), 0)
        self.assertEqual(info["previous_turns"], 1)
        self.assertEqual(info["reason"], "test")

    def test_reset_change_identifiant_et_epoque(self) -> None:
        context = ConversationContext()
        first = context.conversation_id
        context.add_user_message("A")
        context.reset()
        self.assertNotEqual(context.conversation_id, first)
        self.assertEqual(context.epoch, 1)

    def test_alias_clear_et_reset(self) -> None:
        context = ConversationContext()
        context.add_user_message("A")
        context.clear()
        self.assertTrue(context.is_empty())
        context.add_user_message("B")
        context.reset()
        self.assertTrue(context.is_empty())
        self.assertEqual(context.epoch, 2)

    def test_apres_reset_le_message_suivant_est_seul(self) -> None:
        context = ConversationContext()
        context.add_user_message("Quelle est la capitale du Japon ?")
        context.add_assistant_message("Tokyo.")
        context.start_new_conversation(reason="nouvelle conversation")
        context.add_user_message("Quelle est sa population ?")
        self.assertEqual(_texts(context), ["Quelle est sa population ?"])
        self.assertNotIn("Tokyo.", _texts(context))

    def test_reset_notifie_la_session_liee(self) -> None:
        context = ConversationContext()
        seen: list[dict] = []
        context.bind_session(seen.append)
        context.add_user_message("A")
        context.start_new_conversation(reason="outil")
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]["reason"], "outil")

    def test_handler_en_echec_ne_casse_pas_le_reset(self) -> None:
        context = ConversationContext()

        def boom(info):
            raise RuntimeError("session morte")

        context.bind_session(boom)
        context.add_user_message("A")
        context.start_new_conversation()
        self.assertTrue(context.is_empty())

    def test_unbind_session(self) -> None:
        context = ConversationContext()
        seen: list[dict] = []
        context.bind_session(seen.append)
        context.unbind_session()
        context.start_new_conversation()
        self.assertEqual(seen, [])

    def test_une_seule_session_liee_a_la_fois(self) -> None:
        context = ConversationContext()
        first: list[dict] = []
        second: list[dict] = []
        context.bind_session(first.append)
        context.bind_session(second.append)
        context.start_new_conversation()
        self.assertEqual(first, [])
        self.assertEqual(len(second), 1)


class FailedTurnTests(unittest.TestCase):
    def test_echec_conserve_la_demande_et_marque_le_tour(self) -> None:
        context = ConversationContext()
        context.add_user_message("Explique-moi les trous noirs")
        self.assertTrue(context.fail_open_turn("connexion perdue"))
        message = context.get_messages()[0]
        self.assertTrue(message.failed)
        self.assertEqual(message.text, "Explique-moi les trous noirs")
        self.assertFalse(context.has_open_turn())

    def test_echec_sans_tour_ouvert_ne_fait_rien(self) -> None:
        context = ConversationContext()
        context.add_user_message("A")
        context.add_assistant_message("B")
        self.assertFalse(context.fail_open_turn("rien"))
        self.assertFalse(context.get_messages()[0].failed)

    def test_tour_suivant_fonctionne_apres_un_echec(self) -> None:
        context = ConversationContext()
        context.add_user_message("A")
        context.fail_open_turn("timeout")
        context.add_user_message("B")
        context.add_assistant_message("C")
        self.assertEqual(_texts(context), ["A", "B", "C"])
        self.assertEqual(context.turn_count(), 2)

    def test_outil_en_echec_reste_visible_dans_le_contexte(self) -> None:
        context = ConversationContext()
        context.add_user_message("Mets Around the World")
        context.add_tool_interaction(
            "music_play", {"track": "Around the World"}, {"success": False, "error": "Deezer indisponible"}
        )
        self.assertIn("échec", context.get_messages()[-1].text)


class SnapshotAndProviderTests(unittest.TestCase):
    def test_snapshot_ne_contient_pas_le_texte(self) -> None:
        context = ConversationContext()
        context.add_user_message("mon secret bancaire")
        snapshot = context.snapshot()
        self.assertNotIn("secret", repr(snapshot))
        self.assertEqual(snapshot["messages"], 1)
        self.assertEqual(snapshot["turns"], 1)
        self.assertIn("estimated_tokens", snapshot)

    def test_describe_est_lisible(self) -> None:
        context = ConversationContext()
        context.add_user_message("A")
        self.assertIn("1 tour", context.describe())

    def test_changement_de_provider_conserve_la_conversation(self) -> None:
        context = ConversationContext()
        context.add_user_message("A")
        context.add_assistant_message("B")
        previous = context.set_provider("ollama")
        self.assertEqual(previous, "gemini")
        self.assertEqual(context.provider, "ollama")
        self.assertEqual(_texts(context), ["A", "B"])
        context.set_provider("gemini")
        self.assertEqual(_texts(context), ["A", "B"])

    def test_provider_inconnu_est_ignore(self) -> None:
        context = ConversationContext()
        context.set_provider("inexistant")
        self.assertEqual(context.provider, "gemini")

    def test_estimation_de_tokens(self) -> None:
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abcd"), 1)
        self.assertEqual(estimate_tokens("a" * 9), 3)


class ResetCommandTests(unittest.TestCase):
    def test_formulations_reconnues(self) -> None:
        for phrase in (
            "Nouvelle conversation",
            "nouvelle conversation, s'il te plaît",
            "Efface le contexte",
            "Réinitialise la conversation",
            "on repart de zéro",
            "Commence une nouvelle discussion",
            "vide le contexte",
            "reset conversation",
            "new chat",
            "table rase",
        ):
            self.assertTrue(is_new_conversation_command(phrase), phrase)

    def test_formulations_non_reconnues(self) -> None:
        for phrase in (
            "",
            "Mets Around the World",
            "Quelle est la capitale du Japon ?",
            "Crée une nouvelle routine",
            "Efface la note",
            "nouvelle playlist",
            "reprends",
            "reprenons la conversation",
            "continue la discussion",
            "Ne réinitialise pas la conversation",
            "Lance le deuxième",
        ):
            self.assertFalse(is_new_conversation_command(phrase), phrase)

    def test_insensible_aux_accents_et_a_la_ponctuation(self) -> None:
        self.assertTrue(is_new_conversation_command("REINITIALISE LA CONVERSATION !!!"))
        self.assertTrue(is_new_conversation_command("réinitialise, la conversation."))


class DefaultInstanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous = get_default_conversation_context()

    def tearDown(self) -> None:
        set_default_conversation_context(self._previous)

    def test_instance_partagee(self) -> None:
        self.assertIs(get_default_conversation_context(), get_default_conversation_context())

    def test_remplacement_pour_les_tests(self) -> None:
        custom = ConversationContext(max_turns=2)
        set_default_conversation_context(custom)
        self.assertIs(get_default_conversation_context(), custom)

    def test_reinitialisation_globale(self) -> None:
        from src.conversation import start_new_conversation

        custom = ConversationContext()
        set_default_conversation_context(custom)
        custom.add_user_message("A")
        start_new_conversation(reason="test")
        self.assertTrue(custom.is_empty())


class ConcurrencyTests(unittest.TestCase):
    def test_ajouts_concurrents_sans_perte_ni_desordre(self) -> None:
        context = ConversationContext(max_turns=1000, max_tokens=10_000_000)
        errors: list[BaseException] = []

        def worker(index: int) -> None:
            try:
                for step in range(20):
                    context.add_user_message(f"u{index}-{step}")
                    context.add_tool_call("t", {"i": index})
                    context.add_tool_result("t", {"success": True})
                    context.add_assistant_message(f"a{index}-{step}")
            except BaseException as exc:  # pragma: no cover - diagnostic
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(context.size(), 4 * 20 * 4)
        # Chaque message utilisateur a bien ouvert un tour distinct.
        self.assertEqual(context.turn_count(), 4 * 20)

    def test_reset_concurrent_laisse_un_etat_coherent(self) -> None:
        context = ConversationContext()
        stop = threading.Event()

        def adder() -> None:
            while not stop.is_set():
                context.add_user_message("ping")
                context.add_assistant_message("pong")

        def resetter() -> None:
            for _ in range(50):
                context.start_new_conversation()
                time.sleep(0.001)

        thread_a = threading.Thread(target=adder)
        thread_b = threading.Thread(target=resetter)
        thread_a.start()
        thread_b.start()
        thread_b.join()
        stop.set()
        thread_a.join()

        # Aucun message orphelin : tous les tours existants sont numérotés.
        turns = [message.turn for message in context.get_messages()]
        self.assertEqual(turns, sorted(turns))


class PerformanceTests(unittest.TestCase):
    def test_conversation_longue_reste_rapide(self) -> None:
        context = ConversationContext(max_turns=12, max_tokens=3000)
        start = time.perf_counter()
        for index in range(2000):
            context.add_user_message(f"question {index} " + "détail " * 10)
            context.add_tool_call("music_search", {"query": f"q{index}"})
            context.add_tool_result("music_search", {"success": True, "items": [{"title": "x"} for _ in range(20)]})
            context.add_assistant_message(f"réponse {index} " + "texte " * 10)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 10.0, f"contexte trop lent : {elapsed:.2f}s")
        self.assertLessEqual(context.turn_count(), 12)

    def test_conversion_provider_rapide(self) -> None:
        context = ConversationContext(max_turns=12, max_tokens=3000)
        for index in range(50):
            context.add_user_message(f"question {index}")
            context.add_assistant_message(f"réponse {index}")
        start = time.perf_counter()
        for _ in range(500):
            context.messages_for_provider("gemini")
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 5.0, f"conversion trop lente : {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
