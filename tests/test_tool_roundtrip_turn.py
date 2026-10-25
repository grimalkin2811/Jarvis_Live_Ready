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

import asyncio
import os
import sys
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import gemini_live as gl  # noqa: E402
from src.conversation import ConversationContext  # noqa: E402
from tests import sdk_live_fake as fake  # noqa: E402
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


# ---------------------------------------------------------------------------
# Séquences précises du protocole réel (idiome sdk_live_fake, cf.
# test_live_turn_lifecycle.py) : cycle de vie du drapeau d'aller-retour
# d'outil sur les chemins de clôture inhabituels.
# ---------------------------------------------------------------------------


def _fake_validation_probe(**_kwargs):
    return {"success": True, "code": CODE, "message": "canal outil fonctionnel"}


class _ScriptedCase(unittest.IsolatedAsyncioTestCase):
    """Une ``GeminiLive`` réelle branchée sur une session scriptée du SDK."""

    def setUp(self) -> None:
        os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")
        self.events: list[str] = []
        self.turns: list[dict] = []
        self.current: dict = {"user": "", "assistant": ""}
        self.conversation = ConversationContext(max_turns=40, max_tokens=8192)
        patcher = mock.patch.dict(
            gl.TOOL_FUNCTIONS, {"validation_probe": _fake_validation_probe}
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def asyncSetUp(self) -> None:
        self.gemini = gl.GeminiLive(
            "cle-de-test", MODEL_25, "Test",
            on_audio=lambda pcm: None,
            on_turn_complete=self._complete,
            on_interrupted=lambda: self.events.append("interrupted"),
            on_turn_open=lambda: self.events.append("open"),
            on_turn_resolved=lambda: self.events.append("resolved"),
            on_user_transcript=lambda text, final=False: self.current.__setitem__("user", text),
            on_assistant_transcript=lambda text: self.current.__setitem__("assistant", text),
            conversation=self.conversation,
        )
        self.session = fake.SdkLikeSession()
        self.gemini.session = self.session
        self.gemini._loop = asyncio.get_running_loop()
        self.task = asyncio.create_task(self.gemini._receive_loop())

    async def asyncTearDown(self) -> None:
        self.session.close()
        try:
            await asyncio.wait_for(self.task, 2)
        except Exception:
            self.task.cancel()
        await self.gemini.close()

    def _complete(self) -> None:
        self.events.append("complete")
        self.turns.append(dict(self.current))
        self.current = {"user": "", "assistant": ""}

    def kinds(self) -> list[str]:
        return [e["kind"] for e in self.gemini.trace_events()]

    async def settle(self) -> None:
        for _ in range(20):
            await asyncio.sleep(0.005)


class ToolRoundtripFlagLifecycleTests(_ScriptedCase):
    async def _tool_roundtrip_started(self) -> None:
        self.session.push(fake.user("Utilise l'outil validation_probe et dis le code."))
        await self.settle()
        self.session.push(fake.tool_call("validation_probe", {}, "call-1"))
        await self.settle()
        # L'outil a tourné et sa réponse est partie : l'aller-retour est armé.
        self.assertIn("tool_response", self.session.kinds())
        self.assertTrue(self.gemini._tool_roundtrip_open)

    async def test_interruption_pendant_aller_retour_ne_contamine_pas_le_tour_suivant(self):
        """Barge-in pendant l'aller-retour d'outil : l'interruption clôt le
        tour et doit retomber le drapeau — sinon le ``turn_complete`` LÉGITIME
        du tour suivant serait absorbé (tour jamais signalé)."""
        await self._tool_roundtrip_started()
        self.gemini.request_interrupt()
        self.session.push(fake.interrupted())
        await self.settle()
        # Le tour est clôturé par l'interruption ; le drapeau doit être retombé.
        self.assertTrue(self.gemini._turn_closed)
        self.assertFalse(
            self.gemini._tool_roundtrip_open,
            "drapeau d'aller-retour orphelin après interruption (clôture du tour)",
        )
        # Fin de la génération annulée, puis un tour normal complet.
        self.session.push(fake.turn_complete())
        await self.settle()
        self.session.push(fake.user("Quel est mon prénom ?"))
        await self.settle()
        self.session.push(*fake.answer("Simon."))
        await self.settle()
        self.assertIn("complete", self.events, "le tour suivant doit être signalé normalement")
        self.assertEqual(self.turns[-1]["assistant"], "Simon.")
        kept_after_interrupt = [
            e for e in self.gemini.trace_events()
            if e.get("kind") == "TURN_KEPT_OPEN"
            and str(e.get("reason", "")).startswith("turn_complete de la génération porteuse")
        ]
        self.assertEqual(kept_after_interrupt, [], "aucune absorption après l'interruption")
        self.assertNotIn("EMPTY_GENERATION_RETRY", self.kinds())

    async def test_tool_call_tardif_sur_tour_cloture_narme_pas_le_drapeau(self):
        """Un ``tool_call`` hors tour (tour déjà clôturé) ne doit armer aucun
        drapeau orphelin : le tour suivant se referme normalement."""
        self.session.push(fake.user("Bonjour Jarvis."))
        await self.settle()
        self.session.push(*fake.answer("Bonjour."))
        await self.settle()
        self.assertIn("complete", self.events)
        self.assertTrue(self.gemini._turn_closed)
        # ``tool_call`` tardif, hors de tout tour ouvert.
        self.session.push(fake.tool_call("validation_probe", {}, "call-late"))
        await self.settle()
        self.assertIn("tool_response", self.session.kinds())
        self.assertFalse(
            self.gemini._tool_roundtrip_open,
            "drapeau armé sur un tour clôturé (absorption future injustifiée)",
        )
        self.session.push(fake.turn_complete())
        await self.settle()
        self.session.push(fake.user("Quel est mon prénom ?"))
        await self.settle()
        self.session.push(*fake.answer("Simon."))
        await self.settle()
        # NB : on ne compte PAS les ``on_turn_complete`` ici — un
        # ``turn_complete`` poussé sur un tour clôturé re-déclenche le callback
        # avec le ``_turn_model_content_seen`` périmé du tour précédent
        # (comportement préexistant « traîne tardive », hors périmètre). Ce qui
        # compte : le drapeau n'était PAS armé, aucune absorption n'a eu lieu,
        # et le tour suivant s'est refermé normalement avec SA réponse.
        kept = [
            e for e in self.gemini.trace_events()
            if e.get("kind") == "TURN_KEPT_OPEN"
            and str(e.get("reason", "")).startswith("turn_complete de la génération porteuse")
        ]
        self.assertEqual(kept, [], "aucune absorption ne doit suivre un tool_call hors tour")
        self.assertNotIn("EMPTY_GENERATION_RETRY", self.kinds())
        self.assertEqual(self.turns[-1]["assistant"], "Simon.")
        self.assertEqual(self.turns[-1]["user"], "Quel est mon prénom ?")

    async def test_clotures_de_tour_et_de_session_retombent_le_drapeau(self):
        """Filets de clôture (stall, fin de session) : aucun drapeau résiduel
        ne doit survivre à la session ou au tour (état hérité interdit)."""
        await self._tool_roundtrip_started()
        self.gemini._close_stalled_turn("le serveur ne répondait plus (test)")
        self.assertFalse(self.gemini._tool_roundtrip_open, "drapeau résiduel après stall")
        await self._tool_roundtrip_started()
        self.gemini._fail_open_turn("erreur de session (test)")
        self.assertFalse(self.gemini._tool_roundtrip_open, "drapeau résiduel après fin de session")


if __name__ == "__main__":
    unittest.main()
