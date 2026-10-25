"""Aller-retour d'outil RÉEL : le ``turn_complete`` qui suit le ``tool_call``.

Défaut reproduit (validation Gemini réelle du 2026-10-25, PR #51 — scénario
S4 « OUTILS RÉELS : FAIL », note ``outils=['validation_ping'] — le code
KILO-7 n'a pas été retrouvé dans la réponse (ni dans le suivi)``) :

Protocole Live API réel : la génération qui PORTE un ``tool_call`` se termine
par son PROPRE ``turn_complete`` dès l'appel — la réponse du modèle au
``send_tool_response`` arrive ENSUITE, sur une nouvelle génération de la même
session. L'ancien client traitait ce ``turn_complete``-là comme la fin de
l'échange : le tour de l'utilisateur était clos SANS la réponse (le
``on_turn_complete`` partait avec ``assistant`` vide), et le bloc « génération
vide » pouvait relancer la question en plein aller-retour d'outil.

Le faux serveur n'émettait PAS ce ``turn_complete`` intermédiaire (il
déclenchait la continuation directement au ``send_tool_response``) : le défaut
était invisible hors ligne. Il l'émet désormais (protocole réel) — ces tests
verrouillent le comportement attendu du client.
"""

from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import gemini_live as gl  # noqa: E402
from tests.live_harness import FakeLiveServer  # noqa: E402
from tests.voice_harness import VoiceHarness  # noqa: E402

MODEL_25 = "gemini-2.5-flash-native-audio-preview-12-2025"
QUESTION = "Utilise l'outil validation_probe et répète-moi exactement son code."
CODE = "KILO-7"


def _fake_validation_probe(**kwargs):
    return {"success": True, "code": CODE, "message": "canal outil fonctionnel"}


class _ToolRoundtripCase(unittest.IsolatedAsyncioTestCase):
    def make_server(self) -> FakeLiveServer:
        server = FakeLiveServer("2.x")
        server.next_tool_script = (r"validation_probe", "validation_probe", {})
        patcher = mock.patch.dict(
            gl.TOOL_FUNCTIONS, {"validation_probe": _fake_validation_probe}
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        return server

    async def make_harness(self, server: FakeLiveServer) -> VoiceHarness:
        harness = VoiceHarness(server, model=MODEL_25)
        await harness.start()
        self.addAsyncCleanup(harness.stop)
        return harness


class ToolRoundtripTurnTests(_ToolRoundtripCase):
    async def test_la_reponse_d_outil_arrive_dans_le_tour_de_la_question(self) -> None:
        """Le tour qui contient la question contient AUSSI la réponse post-outil."""
        server = self.make_server()
        server.tool_continuation = f"Le code retourné par l'outil est {CODE}."
        harness = await self.make_harness(server)

        answer = await harness.speak(QUESTION)

        self.assertIn(CODE, answer, "la réponse au résultat d'outil a été perdue")
        self.assertEqual(harness.last_turn.tools, ["validation_probe"])
        # UN SEUL tour interface pour tout l'échange : le ``turn_complete``
        # intermédiaire (fin de la génération porteuse de l'appel) n'a PAS clos
        # le tour ni émis un ``on_turn_complete`` sans réponse.
        self.assertEqual(len(harness.turns), 1)
        # Et le tour clôturé côté moteur porte bien la question (preuve trace :
        # le harness VoiceHarness ne recopie pas user_text par conception).
        completes = [
            e for e in harness.gemini.trace_events()
            if e.get("kind") == "TURN_COMPLETE"
        ]
        self.assertEqual(len(completes), 1)
        self.assertTrue(completes[0].get("has_user_text"))

    async def test_aucune_relance_et_aucune_repetition_de_la_question(self) -> None:
        """Le ``turn_complete`` de l'appel d'outil ne déclenche ni la relance
        « génération vide », ni un renvoi de la question sur le câble."""
        server = self.make_server()
        server.tool_continuation = f"Le code retourné par l'outil est {CODE}."
        harness = await self.make_harness(server)

        answer = await harness.speak(QUESTION)

        self.assertIn(CODE, answer)
        kinds = [e["kind"] for e in harness.gemini.trace_events()]
        self.assertNotIn("EMPTY_GENERATION_RETRY", kinds)
        # Le câble ne doit contenir la question QU'UNE fois (la VAD du serveur
        # la reçoit en audio) — aucun ``clientContent`` de relance.
        question_sends = [
            e for e in server.wire
            if e.kind == "client_content" and QUESTION in e.all_text()
        ]
        self.assertEqual(
            len(question_sends), 0,
            "la question a été renvoyée en clientContent pendant l'aller-retour d'outil",
        )
        # La réponse d'outil est bien partie, une fois.
        tool_responses = [e for e in server.wire if e.kind == "tool_response"]
        self.assertEqual(len(tool_responses), 1)
        self.assertEqual(tool_responses[0].tool_names, ["validation_probe"])

    async def test_le_contexte_local_garde_l_ordre_user_outil_assistant(self) -> None:
        """Le tour clos une seule fois verse question + outil + réponse dans le
        contexte, dans cet ordre (exigence des anaphores « quel était le
        code, déjà ? »)."""
        server = self.make_server()
        server.tool_continuation = f"Le code retourné par l'outil est {CODE}."

        def _answer_code(pairs):
            visible = " ".join(t for _role, t in pairs)
            if f"code={CODE}" in visible or CODE in visible:
                return f"Le code retourné par l'outil était {CODE}."
            return "Je ne dispose pas de ce code."

        server.oracle.register(r"code", _answer_code)
        harness = await self.make_harness(server)

        await harness.speak(QUESTION)
        follow = await harness.speak("Quel était le code retourné par l'outil, déjà ?")

        self.assertIn(CODE, follow)
        texts = [t for _role, t in harness.context_texts()]
        # Ordre exact du contexte : question -> appel outil -> résultat ->
        # réponse (le ``turn_complete`` intermédiaire n'a créé AUCUN tour
        # parasite ni inversé l'ordre).
        self.assertEqual(
            texts[:4],
            [
                QUESTION,
                "validation_probe()",
                "validation_probe -> ok | canal outil fonctionnel",
                f"Le code retourné par l'outil est {CODE}.",
            ],
        )


class ToolRoundtripEmptyContinuationTests(_ToolRoundtripCase):
    async def test_une_reponse_vide_apres_outil_est_relancee_puis_repondue(self) -> None:
        """Une génération réellement VIDE APRÈS l'outil (bug serveur) retombe
        dans la relance bornée normale : l'absorption du ``turn_complete``
        intermédiaire ne doit pas l'avaler."""
        server = self.make_server()
        harness = await self.make_harness(server)
        server.empty_tool_continuations_remaining = 1
        # La relance (clientContent texte) doit obtenir une VRAIE réponse du
        # modèle (comme sur l'API réelle), pas un récapitulatif : l'oracle
        # répond donc aux tours texte, sur la question renvoyée.
        server.text_turns_use_oracle = True
        server.oracle.register(
            r"validation_probe",
            lambda pairs: f"Le code retourné par l'outil est {CODE}.",
        )

        answer = await harness.speak(QUESTION)

        self.assertIn(CODE, answer, "la relance après génération vide n'a pas abouti")
        retries = [
            e for e in harness.gemini.trace_events()
            if e.get("kind") == "EMPTY_GENERATION_RETRY"
        ]
        self.assertEqual(len(retries), 1, "une seule relance bornée attendue")
        # L'aller-retour d'outil a bien eu lieu malgré la continuation vide.
        self.assertEqual(harness.last_turn.tools, ["validation_probe"])


class ToolRoundtripInterruptionTests(_ToolRoundtripCase):
    async def test_un_tour_sans_outil_n_est_pas_modifie(self) -> None:
        """Non-régression : sans tool_call, un tour normal se referme comme
        avant (aucune absorption parasite)."""
        server = FakeLiveServer("2.x")
        harness = await self.make_harness(server)

        first = await harness.speak("Mon prénom est Simon.")
        self.assertTrue(first)
        second = await harness.speak("Quel est mon prénom ?")
        self.assertIn("Simon", second)
        kinds = [e["kind"] for e in harness.gemini.trace_events()]
        self.assertNotIn("EMPTY_GENERATION_RETRY", kinds)
        kept_open = [
            e for e in harness.gemini.trace_events()
            if e.get("kind") == "TURN_KEPT_OPEN"
        ]
        self.assertEqual(
            len(kept_open), 0,
            "aucun tour sans outil ne doit être « gardé ouvert »",
        )


if __name__ == "__main__":
    unittest.main()
