"""Publie les échecs du rapport JUnit pytest comme annotations GitHub Actions."""
from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path


def _escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def emit(report: Path) -> int:
    if not report.is_file():
        print(f"::error title=pytest::rapport JUnit absent: {_escape(str(report))}")
        return 0
    root = ET.parse(report).getroot()
    failures = 0
    for case in root.iter("testcase"):
        problem = case.find("failure")
        if problem is None:
            problem = case.find("error")
        if problem is None:
            continue
        failures += 1
        test_id = ".".join(filter(None, (case.get("classname"), case.get("name"))))
        message = problem.get("message") or (problem.text or "échec pytest")
        summary = " | ".join(line.strip() for line in message.splitlines() if line.strip())
        print(f"::error title={_escape(test_id[:180])}::{_escape(summary[-1500:])}")
    if not failures:
        print("::error title=pytest::pytest a échoué sans testcase JUnit en échec")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", nargs="?", type=Path, default=Path("pytest-results.xml"))
    return emit(parser.parse_args().report)


if __name__ == "__main__":
    raise SystemExit(main())
