"""Machine d'états visuelle du Desktop Mode (v1.7.0).

Aucune dépendance Qt : la machine est pure Python, ce qui permet de couvrir
**tous** les états et transitions sans interface, rapidement et sans écran.
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from UI.desktop.state import (  # noqa: E402
    ALL_STATES,
    CONFIGURABLE_STATES,
    PRESENCE_TO_EVENT,
    STATE_LABELS,
    STATE_TIMEOUTS,
    VISIBLE_STATES,
    DesktopEvent,
    DesktopState,
    DesktopStateMachine,
    normalize_state,
)


class StateVocabularyTests(unittest.TestCase):
    """Le vocabulaire est explicite, complet et documenté."""

    def test_all_states_have_a_label(self) -> None:
        for state in ALL_STATES:
            self.assertIn(state, STATE_LABELS)

    def test_hidden_is_the_only_invisible_state(self) -> None:
        self.assertNotIn(DesktopState.HIDDEN, VISIBLE_STATES)
        self.assertEqual(len(VISIBLE_STATES), len(ALL_STATES) - 1)

    def test_hidden_has_no_timeout(self) -> None:
        # HIDDEN ne doit jamais armer de minuteur : coût CPU nul.
        self.assertIsNone(STATE_TIMEOUTS[DesktopState.HIDDEN])

    def test_configurable_states_are_real_states(self) -> None:
        for state in CONFIGURABLE_STATES:
            self.assertIn(state, ALL_STATES)

    def test_normalize_accepts_legacy_aliases(self) -> None:
        self.assertEqual(normalize_state("idle"), DesktopState.HIDDEN)
        self.assertEqual(normalize_state("listening_after_reply"), DesktopState.FOLLOW_UP)
        self.assertEqual(normalize_state("TOOL"), DesktopState.TOOL_USE)
        self.assertEqual(normalize_state("n'importe quoi"), DesktopState.HIDDEN)


class NominalSequenceTests(unittest.TestCase):
    """Le parcours complet d'une interaction, état par état."""

    def setUp(self) -> None:
        self.seen: list[str] = []
        self.machine = DesktopStateMachine(on_transition=lambda t: self.seen.append(t.current))

    def test_full_conversation_cycle(self) -> None:
        self.assertEqual(self.machine.state, DesktopState.HIDDEN)
        self.machine.handle(DesktopEvent.HOTWORD)
        self.assertEqual(self.machine.state, DesktopState.LISTENING)
        self.machine.handle(DesktopEvent.TRANSCRIPT, {"text": "quelle heure est-il"})
        self.assertEqual(self.machine.state, DesktopState.LISTENING)
        self.machine.handle(DesktopEvent.USER_TURN_END)
        self.assertEqual(self.machine.state, DesktopState.THINKING)
        self.machine.handle(DesktopEvent.TOOL_START, {"name": "get_time"})
        self.assertEqual(self.machine.state, DesktopState.TOOL_USE)
        self.assertEqual(self.machine.tool_name, "get_time")
        self.machine.handle(DesktopEvent.TOOL_END, {"name": "get_time"})
        self.assertEqual(self.machine.state, DesktopState.THINKING)
        self.machine.handle(DesktopEvent.TTS_START)
        self.assertEqual(self.machine.state, DesktopState.SPEAKING)
        self.machine.handle(DesktopEvent.TTS_END)
        self.assertEqual(self.machine.state, DesktopState.FOLLOW_UP)
        self.machine.handle(DesktopEvent.SLEEP)
        self.assertEqual(self.machine.state, DesktopState.HIDDEN)

    def test_every_transition_is_reported(self) -> None:
        self.machine.handle(DesktopEvent.HOTWORD)
        self.machine.handle(DesktopEvent.TTS_START)
        self.machine.handle(DesktopEvent.SLEEP)
        self.assertEqual(
            self.seen,
            [DesktopState.LISTENING, DesktopState.SPEAKING, DesktopState.HIDDEN],
        )

    def test_listening_after_reply_is_a_distinct_state(self) -> None:
        """« listening » après une réponse est FOLLOW_UP, pas LISTENING.

        C'est ce qui remplace le minuteur arbitraire de la 1.5.3 : l'état
        vient d'un fait (le tour est terminé), pas d'une supposition.
        """
        self.machine.handle(DesktopEvent.TTS_START)
        self.machine.handle(DesktopEvent.LISTENING)
        self.assertEqual(self.machine.state, DesktopState.FOLLOW_UP)

    def test_first_listening_is_not_follow_up(self) -> None:
        self.machine.handle(DesktopEvent.LISTENING)
        self.assertEqual(self.machine.state, DesktopState.LISTENING)

    def test_nested_tools_keep_tool_state(self) -> None:
        self.machine.handle(DesktopEvent.TOOL_START, {"name": "a"})
        self.machine.handle(DesktopEvent.TOOL_START, {"name": "b"})
        self.machine.handle(DesktopEvent.TOOL_END, {"name": "b"})
        self.assertEqual(self.machine.state, DesktopState.TOOL_USE)
        self.machine.handle(DesktopEvent.TOOL_END, {"name": "a"})
        self.assertEqual(self.machine.state, DesktopState.THINKING)

    def test_tool_name_is_cleared_when_finished(self) -> None:
        self.machine.handle(DesktopEvent.TOOL_START, {"name": "music_play"})
        self.assertEqual(self.machine.tool_name, "music_play")
        self.machine.handle(DesktopEvent.TOOL_END, {"name": "music_play"})
        self.assertEqual(self.machine.tool_name, "")


class InterruptionAndErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = DesktopStateMachine()

    def test_interruption_during_speech(self) -> None:
        self.machine.handle(DesktopEvent.TTS_START)
        self.machine.handle(DesktopEvent.INTERRUPTED)
        self.assertEqual(self.machine.state, DesktopState.INTERRUPTED)
        # Le backend rouvre le micro juste après : on repasse en écoute de suivi.
        self.machine.handle(DesktopEvent.LISTENING)
        self.assertEqual(self.machine.state, DesktopState.FOLLOW_UP)

    def test_interruption_while_hidden_is_ignored(self) -> None:
        self.assertIsNone(self.machine.handle(DesktopEvent.INTERRUPTED))
        self.assertEqual(self.machine.state, DesktopState.HIDDEN)

    def test_error_is_reachable_from_any_state(self) -> None:
        for state_event in (
            DesktopEvent.HOTWORD,
            DesktopEvent.THINKING,
            DesktopEvent.TTS_START,
        ):
            machine = DesktopStateMachine()
            machine.handle(state_event)
            machine.handle(DesktopEvent.ERROR, {"reason": "test"})
            self.assertEqual(machine.state, DesktopState.ERROR)

    def test_error_auto_returns_to_hidden(self) -> None:
        self.machine.handle(DesktopEvent.ERROR)
        self.assertEqual(self.machine.timeout_seconds(), STATE_TIMEOUTS[DesktopState.ERROR])
        self.machine.timeout()
        self.assertEqual(self.machine.state, DesktopState.HIDDEN)

    def test_follow_up_auto_returns_to_hidden(self) -> None:
        self.machine.handle(DesktopEvent.TTS_END)
        self.machine.handle(DesktopEvent.FOLLOW_UP)
        self.machine.timeout()
        self.assertEqual(self.machine.state, DesktopState.HIDDEN)

    def test_timeout_without_target_does_nothing(self) -> None:
        self.machine.handle(DesktopEvent.TTS_START)
        self.assertIsNone(self.machine.timeout())
        self.assertEqual(self.machine.state, DesktopState.SPEAKING)


class RobustnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.machine = DesktopStateMachine()

    def test_unknown_event_is_ignored(self) -> None:
        self.machine.handle(DesktopEvent.HOTWORD)
        self.assertIsNone(self.machine.handle("évènement inconnu"))
        self.assertEqual(self.machine.state, DesktopState.LISTENING)

    def test_empty_event_is_ignored(self) -> None:
        self.assertIsNone(self.machine.handle(""))

    def test_reset_returns_to_hidden_and_clears_tools(self) -> None:
        self.machine.handle(DesktopEvent.TOOL_START, {"name": "x"})
        self.machine.reset()
        self.assertEqual(self.machine.state, DesktopState.HIDDEN)
        self.assertEqual(self.machine.tool_name, "")

    def test_callback_failure_never_breaks_the_machine(self) -> None:
        def _boom(_transition):
            raise RuntimeError("interface cassée")

        machine = DesktopStateMachine(on_transition=_boom)
        machine.handle(DesktopEvent.HOTWORD)
        self.assertEqual(machine.state, DesktopState.LISTENING)

    def test_history_is_bounded(self) -> None:
        for _ in range(200):
            self.machine.handle(DesktopEvent.HOTWORD)
            self.machine.handle(DesktopEvent.SLEEP)
        self.assertLessEqual(len(self.machine.history()), DesktopStateMachine.HISTORY)

    def test_determinism(self) -> None:
        sequence = [
            DesktopEvent.HOTWORD,
            DesktopEvent.TRANSCRIPT,
            DesktopEvent.USER_TURN_END,
            DesktopEvent.TOOL_START,
            DesktopEvent.TOOL_END,
            DesktopEvent.TTS_START,
            DesktopEvent.TTS_END,
        ]
        runs = []
        for _ in range(3):
            machine = DesktopStateMachine()
            states = []
            for event in sequence:
                machine.handle(event)
                states.append(machine.state)
            runs.append(states)
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[1], runs[2])


class LegacyPresenceCompatibilityTests(unittest.TestCase):
    """Le pipeline 1.6.0 (chaînes de présence) continue de piloter l'overlay."""

    def setUp(self) -> None:
        self.machine = DesktopStateMachine()

    def test_presence_values_are_all_mapped(self) -> None:
        for presence in ("loading", "listening", "thinking", "speaking", "hidden"):
            self.assertIn(presence, PRESENCE_TO_EVENT)

    def test_legacy_sequence(self) -> None:
        self.machine.handle_presence("loading")
        self.assertEqual(self.machine.state, DesktopState.LOADING)
        self.machine.handle_presence("listening")
        self.assertEqual(self.machine.state, DesktopState.LISTENING)
        self.machine.handle_presence("thinking")
        self.assertEqual(self.machine.state, DesktopState.THINKING)
        self.machine.handle_presence("speaking")
        self.assertEqual(self.machine.state, DesktopState.SPEAKING)
        self.machine.handle_presence("hidden")
        self.assertEqual(self.machine.state, DesktopState.HIDDEN)

    def test_unknown_presence_is_ignored(self) -> None:
        self.machine.handle_presence("listening")
        self.assertIsNone(self.machine.handle_presence("zzz"))
        self.assertEqual(self.machine.state, DesktopState.LISTENING)


if __name__ == "__main__":
    unittest.main()
