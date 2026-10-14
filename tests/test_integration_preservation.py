from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from scripts import check_integration_preservation as check


@pytest.fixture
def evidence():
    baseline = json.loads((Path(__file__).resolve().parents[1] / "docs/releases/INTEGRATION_BASELINE.json").read_text())
    release = {**baseline["release"], "assets": deepcopy(baseline["assets"]), "body": "sample notes"}
    baseline["release"] = check.release_identity(release)
    return baseline, release


def test_unchanged_metadata_and_download_counters(evidence):
    base, release = evidence
    release["assets"][0]["download_count"] = 100
    assert not check.differences(base, release, base["tags"], base["notes_v180_blob"])


@pytest.mark.parametrize("what", ["asset_id", "digest", "size", "extra_asset", "missing_asset", "body", "draft", "tag", "notes"])
def test_detects_each_mutation(evidence, what):
    base, release = evidence
    tags = base["tags"][:]
    notes = base["notes_v180_blob"]
    if what in {"asset_id", "digest", "size"}:
        release["assets"][0]["id" if what == "asset_id" else what] = "changed"
    elif what == "extra_asset":
        release["assets"].append({"id": 0})
    elif what == "missing_asset":
        release["assets"].pop()
    elif what == "body":
        release["body"] += " changed"
    elif what == "draft":
        release["draft"] = True
    elif what == "tag":
        tags.pop()
    else:
        notes = "changed"
    assert check.differences(base, release, tags, notes)


def test_external_failure_is_not_reported_as_success(tmp_path, monkeypatch):
    monkeypatch.setattr(check, "_run", Mock(side_effect=OSError("offline")))
    assert check.main([]) == 2
