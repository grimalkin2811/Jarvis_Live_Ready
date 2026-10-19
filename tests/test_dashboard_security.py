"""Niveaux de sécurité des actions du Dashboard."""

from __future__ import annotations

import pytest

from UI.dashboard.security import (
    DEFAULT_POLICIES,
    LEVEL_CONFIRM,
    LEVEL_DESTRUCTIVE,
    LEVEL_IMMEDIATE,
    ActionPolicy,
    SecurityPolicy,
)


def test_level3_can_never_be_lowered():
    policy = ActionPolicy("x.delete", "Supprimer", LEVEL_DESTRUCTIVE, min_level=LEVEL_IMMEDIATE)
    assert policy.min_level == LEVEL_DESTRUCTIVE
    security = SecurityPolicy.default(policies=[policy])
    assert security.set_confirmation("x.delete", False) is False
    assert security.effective_level("x.delete") == LEVEL_DESTRUCTIVE


def test_level2_lowerable_only_when_allowed():
    security = SecurityPolicy.default()
    assert security.set_confirmation("task.retry", False) is True
    assert security.effective_level("task.retry") == LEVEL_IMMEDIATE
    assert security.set_confirmation("task.retry", True) is True
    assert security.effective_level("task.retry") == LEVEL_CONFIRM
    # Modification de souvenir : confirmation imposée (min_level=2).
    assert security.set_confirmation("memory.update", False) is False
    assert security.effective_level("memory.update") == LEVEL_CONFIRM


def test_overrides_from_preferences_cannot_bypass_locks():
    security = SecurityPolicy.default({"memory.delete": False, "memory.update": False, "jarvis.quit": False,
                                       "conversation.new": False})
    assert security.effective_level("memory.delete") == LEVEL_DESTRUCTIVE
    assert security.effective_level("memory.update") == LEVEL_CONFIRM
    assert security.effective_level("jarvis.quit") == LEVEL_DESTRUCTIVE
    assert security.effective_level("conversation.new") == LEVEL_IMMEDIATE


def test_unknown_action_requires_confirmation():
    assert SecurityPolicy.default().effective_level("inconnue") == LEVEL_CONFIRM


def test_invalid_level_rejected():
    with pytest.raises(ValueError):
        ActionPolicy("x", "x", 7)


def test_specification_levels():
    levels = {p.action_id: p.level for p in DEFAULT_POLICIES}
    assert levels["memory.delete"] == LEVEL_DESTRUCTIVE
    assert levels["memory.create"] == LEVEL_IMMEDIATE
    assert levels["memory.update"] == LEVEL_CONFIRM
    assert levels["memory.restore"] == LEVEL_CONFIRM
    ids = [p.action_id for p in DEFAULT_POLICIES]
    assert len(ids) == len(set(ids))


def test_every_widget_action_has_a_policy():
    from UI.dashboard.registry import default_registry

    ids = {p.action_id for p in DEFAULT_POLICIES}
    for spec in default_registry().specs():
        for action_id in spec.actions:
            assert action_id in ids, (spec.type_id, action_id)


def test_activity_log_and_diagnostic_report_never_show_secrets(monkeypatch):
    """Tout texte affiché (journal d'activité, rapport copiable) est masqué."""
    import logging

    from src.runtime_status import RuntimeStatusBoard
    from UI.dashboard.diagnostics import build_report, collect_checks
    from UI.dashboard.log_buffer import LogRingHandler, attach, detach

    key = "AIza" + "Q" * 35
    handler = LogRingHandler(capacity=50)
    attach(handler)
    try:
        logging.getLogger("jarvis.test").warning("échec GET https://example.invalid/v1?key=%s token=abcdef123456", key)
        handler.record_event(f"Authorization: Bearer {key}", source="test")
    finally:
        detach(handler)
    shown = " ".join(entry.message for entry in handler.entries())
    assert key not in shown and "abcdef123456" not in shown
    assert shown.count("[masqué]") >= 2

    board = RuntimeStatusBoard()
    board.record_error(f"ConnectionError: wss://example.invalid?key={key}")
    monkeypatch.setenv("GEMINI_API_KEY", key)
    snapshot = board.snapshot()
    report = build_report(collect_checks(snapshot), snapshot, {"note": f"api_key={key}"})
    assert key not in report
    assert "Clé API dans l'environnement : oui" in report
