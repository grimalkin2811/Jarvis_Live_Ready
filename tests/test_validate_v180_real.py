from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path


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
    architecture = [item for item in report.checks if item.category == "ARCHITECTURE"]
    api = [item for item in report.checks if item.category == "API RÉELLE"]
    assert architecture and architecture[0].status == "PASS"
    assert api and all(item.status == "NON_TESTABLE" for item in api)
    assert not report.failed()


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
