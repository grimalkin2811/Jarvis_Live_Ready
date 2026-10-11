from __future__ import annotations

from scripts.emit_pytest_annotations import emit


def test_emit_pytest_annotations_reports_failure(tmp_path, capsys):
    report = tmp_path / "pytest-results.xml"
    report.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite tests="1" failures="1">
<testcase classname="tests.test_example" name="test_failure">
<failure message="assert 1 == 2">traceback</failure>
</testcase></testsuite></testsuites>
""",
        encoding="utf-8",
    )
    assert emit(report) == 0
    output = capsys.readouterr().out
    assert "::error title=tests.test_example.test_failure::assert 1 == 2" in output
