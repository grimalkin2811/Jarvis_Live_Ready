"""Écouteurs de changement du contexte conversationnel (Dashboard)."""

from __future__ import annotations

import threading

from src.conversation import ConversationContext


def test_every_mutation_notifies_outside_the_lock():
    context = ConversationContext(max_turns=10, max_tokens=4000)
    events = []

    def listener(event):
        # Doit pouvoir relire le contexte (et depuis un autre thread : le
        # verrou n'est pas tenu pendant l'appel).
        result = {}

        def reader():
            result["n"] = len(context.get_messages())

        thread = threading.Thread(target=reader)
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive(), "verrou tenu pendant la notification"
        events.append((event["reason"], result["n"]))

    context.add_listener(listener)
    context.add_user_message("Bonjour")
    context.extend_user_message("Jarvis")
    context.add_tool_interaction("get_time", {}, {"success": True})
    context.add_assistant_message("Il est midi.")
    context.add_user_message("Et demain ?")
    context.fail_open_turn("coupure")
    context.start_new_conversation("test")
    reasons = [reason for reason, _n in events]
    assert reasons == [
        "user_message", "user_message_extended", "tool_call", "tool_result",
        "assistant_message", "user_message", "turn_failed", "conversation_reset",
    ]
    # La notification part APRÈS la mutation.
    assert events[0][1] == 1
    assert events[-1][1] == 0


def test_listener_errors_are_isolated_and_removal_works():
    context = ConversationContext()
    seen = []
    context.add_listener(lambda event: (_ for _ in ()).throw(RuntimeError("boom")))
    context.add_listener(seen.append)
    context.add_user_message("a")
    assert len(seen) == 1
    context.remove_listener(seen.append)
    context.add_user_message("b")
    assert len(seen) == 1
    assert context.listener_count() == 1


def test_close_turn_notifies_only_when_a_turn_was_open():
    context = ConversationContext()
    seen = []
    context.add_listener(lambda e: seen.append(e["reason"]))
    assert context.close_turn() is False
    context.add_user_message("q")
    assert context.close_turn() is True
    assert seen == ["user_message", "turn_closed"]
