from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_v180_real.py"
SPEC = importlib.util.spec_from_file_location("validate_v180_real", SCRIPT)
validation = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = validation
assert SPEC.loader is not None
SPEC.loader.exec_module(validation)


def test_redact_never_keeps_api_key():
    secret = "AIza" + "A" * 30
    assert secret not in validation.redact(f"erreur URL key={secret}")


def test_api_without_key_is_explicitly_non_testable(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr(validation.settings, "load_file_config", lambda: {})
    report = validation.Report()
    asyncio.run(validation.run_api_validation(report))
    assert report.checks
    discovery = [item for item in report.checks if item.category == "DÉCOUVERTE LIVE"]
    assert discovery and discovery[0].status == "NON_TESTABLE"
    assert not report.failed()


def test_failure_classification_separates_provider_local_timeout_and_code():
    assert validation.classify_failure("429 RESOURCE_EXHAUSTED") == (
        "NON_TESTABLE", "EXTERNAL_QUOTA"
    )
    assert validation.classify_failure("503 UNAVAILABLE high demand") == (
        "NON_TESTABLE", "EXTERNAL_SERVICE"
    )
    assert validation.classify_failure(
        "received 1011 (internal error) You exceeded your current quota, "
        "please check your plan and billing details."
    ) == ("NON_TESTABLE", "EXTERNAL_QUOTA")
    assert validation.classify_failure(
        "received 1011 (internal error) Resource has been exhausted (e.g. check quota)."
    ) == ("NON_TESTABLE", "LIVE_RESOURCE_EXHAUSTED")
    assert validation.classify_failure(
        "received 1011 (internal error) unexpected internal condition"
    ) == ("FAIL", "CODE_OR_PROTOCOL")
    assert validation.classify_failure("Limite Gemini 3.8 atteinte (5 appels/minute)") == (
        "NON_TESTABLE", "LOCAL_QUOTA_GUARD"
    )
    assert validation.classify_failure("Timeout Gemini Live après 90s") == (
        "FAIL", "TIMEOUT"
    )
    assert validation.classify_failure("session fermée sans turn_complete") == (
        "FAIL", "LIVE_PROTOCOL"
    )
    assert validation.classify_failure("JSON invalide") == (
        "FAIL", "CODE_OR_PROTOCOL"
    )


def test_google_search_live_1011_quota_is_external_non_testable():
    error = RuntimeError(
        "Google Search grounding: received 1011; You exceeded your current quota, "
        "please check your plan and billing details."
    )
    assert validation.classify_failure(error) == (
        "NON_TESTABLE", "EXTERNAL_QUOTA"
    )


def test_document_validation_requires_completed_result_and_nonempty_files(tmp_path):
    document = tmp_path / "rapport.md"
    document.write_text("contenu", encoding="utf-8")
    valid = SimpleNamespace(
        status=validation.TaskStatus.COMPLETED,
        result="résultat réel",
        files=[str(document)],
    )
    assert validation.validate_document_artifacts(valid)[0]

    empty = tmp_path / "vide.md"
    empty.write_text("", encoding="utf-8")
    for invalid in (
        SimpleNamespace(status=validation.TaskStatus.FAILED, result="résultat", files=[str(document)]),
        SimpleNamespace(status=validation.TaskStatus.COMPLETED, result="", files=[str(document)]),
        SimpleNamespace(status=validation.TaskStatus.COMPLETED, result="résultat", files=[]),
        SimpleNamespace(status=validation.TaskStatus.COMPLETED, result="résultat", files=[str(empty)]),
        SimpleNamespace(status=validation.TaskStatus.COMPLETED, result="résultat", files=[str(tmp_path / "absent.md")]),
    ):
        assert not validation.validate_document_artifacts(invalid)[0]


def test_json_report_contains_no_secret(tmp_path):
    secret = "AIza" + "B" * 30
    report = validation.Report()
    report.add("API", "erreur", "FAIL", f"service rejected {secret}")
    destination = tmp_path / "report.json"
    report.write(destination)
    raw = destination.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert secret not in raw
    assert payload["checks"][0]["status"] == "FAIL"


def test_manual_protocol_covers_critical_concurrency_and_notification():
    text = validation.MANUAL_PROTOCOL
    assert "Pendant RUNNING" in text
    assert "trois questions" in text
    assert "notification visible" in text
    assert "aucun bip" in text
    assert "CANCELLED" in text
