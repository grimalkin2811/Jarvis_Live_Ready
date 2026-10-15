"""Tableau d'état runtime (faits publiés par la boucle vocale)."""

from __future__ import annotations

import concurrent.futures
import threading

import pytest

from src.runtime_status import (
    HEALTH_DEGRADED,
    HEALTH_OPERATIONAL,
    HEALTH_UNAVAILABLE,
    HEALTH_UNKNOWN,
    EngineState,
    EngineUnavailableError,
    RuntimeStatusBoard,
    redact_secrets,
)


def test_operational_requires_ready_engine_and_audio():
    board = RuntimeStatusBoard()
    assert board.snapshot()["health"] == HEALTH_UNKNOWN
    board.set_engine_state(EngineState.CONNECTING)
    assert board.snapshot()["health"] == HEALTH_UNKNOWN
    board.set_engine_state(EngineState.READY)
    assert board.snapshot()["health"] == HEALTH_UNKNOWN  # audio non confirmé : aucune déduction
    board.update(audio_state="ok", wake_word="loaded", memory_state="ok", tasks_state="ok")
    assert board.snapshot()["health"] == HEALTH_OPERATIONAL
    board.update(wake_word="unavailable")
    assert board.snapshot()["health"] == HEALTH_DEGRADED
    board.set_engine_state(EngineState.RECONNECTING, "nouvelle tentative")
    assert board.snapshot()["health"] == HEALTH_UNAVAILABLE


def test_hooks_called_only_on_change_and_isolated():
    board = RuntimeStatusBoard()
    seen = []
    board.add_hook(lambda snap: (_ for _ in ()).throw(RuntimeError("UI cassée")))
    board.add_hook(seen.append)
    board.update(model="m")
    board.update(model="m")
    assert len(seen) == 1
    assert seen[0]["model"] == "m"


def test_secrets_are_redacted_in_published_errors():
    board = RuntimeStatusBoard()
    key = "AIza" + "B" * 35
    board.record_error(f"POST https://host/v1?key={key} failed api_key={key} Bearer abcdefghijkl")
    text = board.snapshot()["last_error"]
    assert key not in text
    assert "abcdefghijkl" not in text


@pytest.mark.parametrize("raw", [
    "token: supersecretvalue123",
    "password=hunter2hunter2",
    "Authorization: Bearer xyz.abc.def123",
    "https://x/y?access_token=abcdef123456&b=2",
])
def test_redact_patterns(raw):
    assert "[masqué]" in redact_secrets(raw)


def test_submit_text_without_handler_explains_reason():
    board = RuntimeStatusBoard()
    with pytest.raises(EngineUnavailableError, match="pas démarré"):
        board.submit_text("bonjour")
    board.set_engine_state(EngineState.AUTH_ERROR)
    board.register_text_handler(lambda text: concurrent.futures.Future())
    with pytest.raises(EngineUnavailableError, match="Clé"):
        board.submit_text("bonjour")


def test_submit_text_routes_to_handler_and_unregister_is_safe():
    board = RuntimeStatusBoard()
    calls = []

    def handler(text):
        calls.append(text)
        future = concurrent.futures.Future()
        future.set_result({"accepted": True})
        return future

    board.set_engine_state(EngineState.READY)
    board.register_text_handler(handler)
    assert board.snapshot()["text_input"] is True
    assert board.submit_text("salut").result(1) == {"accepted": True}
    board.unregister_text_handler(lambda t: None)  # pas le bon : ignoré
    assert board.snapshot()["text_input"] is True
    board.unregister_text_handler(handler)
    assert board.snapshot()["text_input"] is False
    assert calls == ["salut"]


def test_thread_safety_of_concurrent_updates():
    board = RuntimeStatusBoard()
    counts = []
    board.add_hook(lambda snap: counts.append(snap["revision"]))

    def worker(n):
        for i in range(200):
            board.update(session_generation=n * 1000 + i)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert board.snapshot()["revision"] == len(counts)
