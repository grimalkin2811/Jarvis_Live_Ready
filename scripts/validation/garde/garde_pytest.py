"""Plugin pytest du protocole de validation (``-p garde_pytest``).

* rattache chaque incident de la garde des données au test qui l'a provoqué ;
* consigne la progression test par test (``JARVIS_VALIDATION_PROGRESSION``,
  JSON par ligne, écrit au fil de l'eau) : si l'interpréteur s'arrête
  brutalement (plantage natif, le XML JUnit n'est alors jamais écrit), le
  protocole sait quels tests ont été exécutés et lequel était en cours.
"""

from __future__ import annotations

import json
import os

import pytest

try:
    import garde_donnees
except Exception:  # pragma: no cover - garde absente : plugin inerte
    garde_donnees = None

_PROGRESS = os.environ.get("JARVIS_VALIDATION_PROGRESSION", "")


def _write(entry: dict) -> None:
    if not _PROGRESS:
        return
    try:
        with open(_PROGRESS, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
            handle.flush()
    except Exception:
        pass


def pytest_collection_finish(session):
    _write({"evenement": "collecte", "nombre": len(session.items)})


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if garde_donnees is not None:
        garde_donnees.set_current_test(item.nodeid)
    _write({"evenement": "debut", "test": item.nodeid})
    yield
    _write({"evenement": "fin", "test": item.nodeid})
    if garde_donnees is not None:
        garde_donnees.set_current_test("")


def pytest_runtest_logreport(report):
    subtest = "subtest" in type(report).__name__.lower()
    if report.when != "call" and not (report.failed or report.skipped):
        return
    _write({
        "evenement": "resultat",
        "test": report.nodeid,
        "phase": report.when,
        "issue": report.outcome,
        "sous_test": subtest,
    })


def pytest_terminal_summary(terminalreporter):
    if garde_donnees is None:
        return
    info = garde_donnees.summary()
    terminalreporter.write_line(
        f"[garde de validation] active={info['active']} incidents={info['incidents']}"
    )
