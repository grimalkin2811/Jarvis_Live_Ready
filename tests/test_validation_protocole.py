"""Tests du protocole de validation automatisée (``scripts/validation``).

Ces tests ne lancent jamais le protocole complet : ils vérifient ses garanties
de sécurité (garde des données, masquage des secrets, isolation des processus
enfants, liste blanche d'outils), son honnêteté (statuts, verdict, comptage
des tests sans double comptage) et la conservation des rapports existants.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

VALIDATION = Path(__file__).resolve().parents[1] / "scripts" / "validation"
GARDE = VALIDATION / "garde"
sys.path.insert(0, str(VALIDATION))

import protocole as P  # noqa: E402


def _valider_pr():
    previous = sys.dont_write_bytecode
    try:
        import valider_pr
    finally:
        sys.dont_write_bytecode = previous
    return valider_pr


# ---------------------------------------------------------------------------
# Garde des données réelles
# ---------------------------------------------------------------------------

SONDE_GARDE = textwrap.dedent("""
    import json, os, shutil, sqlite3, sys
    from pathlib import Path
    reel, checkout, libre = sys.argv[1], sys.argv[2], sys.argv[3]
    out = {}
    def essai(nom, fn):
        try:
            fn()
            out[nom] = "autorisé"
        except PermissionError:
            out[nom] = "refusé"
    essai("lecture_reelle", lambda: open(os.path.join(reel, "memory.db"), "rb").read())
    essai("sqlite_reel", lambda: sqlite3.connect(os.path.join(reel, "memory.db")))
    essai("ecriture_reelle", lambda: Path(reel, "nouveau.txt").write_text("x"))
    essai("suppression_reelle", lambda: shutil.rmtree(reel))
    essai("lecture_checkout", lambda: open(os.path.join(checkout, "a.py")).read())
    essai("ecriture_checkout", lambda: open(os.path.join(checkout, "a.py"), "a").write("x"))
    essai("renommage_checkout", lambda: os.replace(os.path.join(checkout, "a.py"), os.path.join(checkout, "b.py")))
    essai("ecriture_libre", lambda: Path(libre, "ok.txt").write_text("ok"))
    essai("sqlite_libre", lambda: sqlite3.connect(os.path.join(libre, "ok.db")).execute("create table t(x)"))
    print(json.dumps(out))
""")


def _env_garde(tmp_path: Path, actif: bool = True) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(GARDE)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if actif:
        env["JARVIS_VALIDATION_GARDE"] = "1"
    else:
        env.pop("JARVIS_VALIDATION_GARDE", None)
    env["JARVIS_VALIDATION_INTERDIT"] = str(tmp_path / "reel" / "Jarvis")
    env["JARVIS_VALIDATION_LECTURE_SEULE"] = str(tmp_path / "checkout")
    env["JARVIS_VALIDATION_INCIDENTS"] = str(tmp_path / "incidents.jsonl")
    env["JARVIS_VALIDATION_ACTIVATIONS"] = str(tmp_path / "activations.jsonl")
    return env


def _preparer(tmp_path: Path) -> tuple[Path, Path, Path]:
    reel = tmp_path / "reel" / "Jarvis"
    checkout = tmp_path / "checkout"
    libre = tmp_path / "libre"
    for folder in (reel, checkout, libre):
        folder.mkdir(parents=True)
    (reel / "memory.db").write_bytes(b"donnees personnelles")
    (checkout / "a.py").write_text("code", encoding="utf-8")
    return reel, checkout, libre


def test_guard_blocks_real_data_and_read_only_checkout_and_logs_incidents(tmp_path):
    reel, checkout, libre = _preparer(tmp_path)
    proc = subprocess.run([sys.executable, "-c", SONDE_GARDE, str(reel), str(checkout), str(libre)],
                          env=_env_garde(tmp_path), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])
    assert result == {
        "lecture_reelle": "refusé", "sqlite_reel": "refusé", "ecriture_reelle": "refusé",
        "suppression_reelle": "refusé", "lecture_checkout": "autorisé", "ecriture_checkout": "refusé",
        "renommage_checkout": "refusé", "ecriture_libre": "autorisé", "sqlite_libre": "autorisé",
    }
    # Les données sont intactes : l'opération est bloquée AVANT le système de fichiers.
    assert (reel / "memory.db").read_bytes() == b"donnees personnelles"
    assert sorted(p.name for p in reel.iterdir()) == ["memory.db"]
    assert (checkout / "a.py").read_text(encoding="utf-8") == "code"
    incidents = [json.loads(line) for line in (tmp_path / "incidents.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(incidents) == 6
    assert {i["operation"] for i in incidents} >= {"sqlite3.connect", "shutil.rmtree"}
    assert all(i["pile"] for i in incidents), "chaque incident indique le code appelant"
    assert len((tmp_path / "activations.jsonl").read_text(encoding="utf-8").splitlines()) == 1


def test_guard_is_inert_without_its_variable(tmp_path):
    reel, checkout, libre = _preparer(tmp_path)
    code = "import sys; print(open(sys.argv[1], 'rb').read().decode())"
    proc = subprocess.run([sys.executable, "-c", code, str(reel / "memory.db")],
                          env=_env_garde(tmp_path, actif=False), capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and "donnees personnelles" in proc.stdout
    assert not (tmp_path / "incidents.jsonl").exists()


# ---------------------------------------------------------------------------
# Masquage, statuts et verdict
# ---------------------------------------------------------------------------


def test_masker_hides_secrets_paths_and_user_names():
    masque = P.Masqueur()
    cle = "AIzaSyD" + "x" * 32
    masque.secret(cle)
    masque.chemin("/srv/depot", "<dépôt>")
    texte = (f"clé {cle} ; GEMINI_API_KEY={cle[:-1]}Z ; jeton ghp_{'a' * 36} ; Authorization: Bearer {'b' * 30} ; "
             f'{{"api_key": "valeur-secrete-123"}} ; /srv/depot/src/x.py ; C:\\Users\\Ada\\AppData ; /home/ada/x')
    result = masque(texte)
    for fuite in (cle, "ghp_aaaa", "b" * 30, "valeur-secrete-123", "Ada", "/home/ada"):
        assert fuite not in result
    assert "<dépôt>/src/x.py" in result and "C:\\Users\\<utilisateur>" in result
    assert masque(result) == result, "le masquage est idempotent"
    assert masque.contient_secret(texte) and not masque.contient_secret(result)


def test_unknown_status_is_refused():
    with pytest.raises(ValueError):
        P.Controle("X01", "x").regler("PASS")


def _rapport(*statuts, requis=True, termine=True) -> P.Rapport:
    rapport = P.Rapport(termine=termine)
    for index, statut in enumerate(statuts):
        rapport.ajouter(f"A{index:02d}", f"contrôle {index}", requis=requis).regler(statut, "r")
    return rapport


def test_verdict_is_ready_only_when_every_required_check_passed():
    assert P.calculer_verdict(_rapport(P.REUSSI, P.REUSSI, P.NON_APPLICABLE))[:2] == (P.VERDICT_PRETE, 0)
    assert P.calculer_verdict(_rapport(P.REUSSI, P.ECHEC))[:2] == (P.VERDICT_BLOQUEE, 1)
    assert P.calculer_verdict(_rapport(P.REUSSI, P.NON_TESTE))[:2] == (P.VERDICT_INSUFFISANTE, 2)
    assert P.calculer_verdict(_rapport(P.REUSSI, P.BLOQUE))[:2] == (P.VERDICT_INSUFFISANTE, 2)
    # Un protocole interrompu n'est jamais « prêt », même si tout ce qui a tourné est vert.
    assert P.calculer_verdict(_rapport(P.REUSSI, termine=False))[0] == P.VERDICT_INSUFFISANTE
    erreur = _rapport(P.REUSSI)
    erreur.erreur_protocole = "boom"
    assert P.calculer_verdict(erreur)[:2] == (P.VERDICT_INSUFFISANTE, 3)
    # Un contrôle informatif en échec ne bloque pas ; un problème bloquant, si.
    info = _rapport(P.REUSSI)
    info.ajouter("A99", "info", requis=False).regler(P.ECHEC, "x")
    assert P.calculer_verdict(info)[0] == P.VERDICT_PRETE
    info.problemes.append(P.Probleme("bloquant", "secret exposé"))
    assert P.calculer_verdict(info)[0] == P.VERDICT_BLOQUEE


def test_report_has_the_fifteen_sections_and_never_leaks_a_secret():
    masque = P.Masqueur()
    cle = "AIza" + "k" * 35
    masque.secret(cle)
    rapport = _rapport(P.REUSSI, P.NON_TESTE)
    rapport.controles[0].resultat = f"sortie contenant {cle}"
    rapport.problemes.append(P.Probleme("important", "écart", f"clé {cle}", "src/x.py:12"))
    texte = P.rendre_markdown(rapport, masque)
    for numero in range(1, 16):
        assert f"## {numero}. " in texte
    assert cle not in texte and P.MASQUE in texte
    assert "**NON TESTÉ**" in texte and "VALIDATION INSUFFISANTE" in texte


def test_report_file_never_replaces_an_existing_report(tmp_path):
    first = P.creer_fichier_unique(tmp_path, "validation_pr50_20261015-120000")
    first.write_text("rapport précédent", encoding="utf-8")
    second = P.creer_fichier_unique(tmp_path, "validation_pr50_20261015-120000")
    assert second != first and second.name == "validation_pr50_20261015-120000_2.md"
    assert first.read_text(encoding="utf-8") == "rapport précédent"


# ---------------------------------------------------------------------------
# Comptage des tests
# ---------------------------------------------------------------------------


def test_junit_counts_tests_not_subtests(tmp_path):
    (tmp_path / "test_echantillon.py").write_text(textwrap.dedent("""
        import unittest, pytest
        class T(unittest.TestCase):
            def test_sous_tests(self):
                for i in range(3):
                    with self.subTest(i=i):
                        self.assertTrue(True)
        def test_ok():
            pass
        @pytest.mark.skip(reason="x")
        def test_ignore():
            pass
        def test_echec():
            assert False
    """), encoding="utf-8")
    junit = tmp_path / "j.xml"
    subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}",
                    str(tmp_path / "test_echantillon.py")], cwd=tmp_path, capture_output=True, timeout=120)
    data = P.lire_junit(junit)
    assert data["totaux"]["executes"] == 3 and data["totaux"]["reussis"] == 2
    assert data["totaux"]["echoues"] == 1 and data["totaux"]["ignores"] == 1
    assert {c["nom"] for c in data["cas"]} == {"test_sous_tests", "test_ok", "test_ignore", "test_echec"}
    compte = P.compter_tests(4, data, None)
    assert compte["bloques"] == 0 and compte["executes"] + compte["ignores"] == 4


def test_progress_log_reveals_a_crash_and_blocked_tests(tmp_path):
    log = tmp_path / "progression.jsonl"
    lines = [{"evenement": "collecte", "nombre": 5}]
    for name, issue in (("t1", "passed"), ("t2", "failed"), ("t3", "skipped")):
        lines += [{"evenement": "debut", "test": name},
                  {"evenement": "resultat", "test": name, "phase": "call", "issue": issue, "sous_test": False},
                  {"evenement": "fin", "test": name}]
    lines += [{"evenement": "resultat", "test": "t1", "phase": "call", "issue": "passed", "sous_test": True},
              {"evenement": "debut", "test": "t4"}]  # plantage natif pendant t4
    log.write_text("\n".join(json.dumps(item) for item in lines), encoding="utf-8")
    progression = P.lire_progression(log)
    assert progression["en_cours_au_plantage"] == "t4"
    compte = P.compter_tests(None, None, progression)
    assert (compte["executes"], compte["reussis"], compte["echoues"], compte["ignores"], compte["bloques"]) == (2, 1, 1, 1, 2)
    assert compte["sous_tests"]["reussis"] == 1


# ---------------------------------------------------------------------------
# Orchestrateur : isolation, outils, en-tête SQLite, verdicts des scripts réels
# ---------------------------------------------------------------------------


def _execution(tmp_path, valider_pr):
    args = argparse.Namespace(depot=str(tmp_path / "depot"), dossier_validation=str(tmp_path / "validation"),
                              non_interactif=True, depot_github="exemple/depot", pr=50)
    (tmp_path / "depot").mkdir(exist_ok=True)
    return valider_pr.Execution(args)


def test_child_environment_strips_secrets_and_redirects_data(tmp_path, monkeypatch):
    valider_pr = _valider_pr()
    monkeypatch.setenv("GEMINI_API_KEY", "AIza" + "s" * 35)
    monkeypatch.setenv("GH_TOKEN", "ghp_" + "t" * 36)
    x = _execution(tmp_path, valider_pr)
    x.racines_reelles = [tmp_path / "reel" / "Jarvis"]
    env = x.env_enfant("essai")
    assert "GEMINI_API_KEY" not in env and "GH_TOKEN" not in env
    assert Path(env["JARVIS_DATA_DIR"]).is_relative_to(x.run)
    assert env["JARVIS_VALIDATION_GARDE"] == "1" and env["PYTHONPATH"] == str(GARDE)
    assert env["JARVIS_VALIDATION_INTERDIT"] == str(tmp_path / "reel" / "Jarvis")
    assert env["JARVIS_VALIDATION_LECTURE_SEULE"] == str(x.depot)
    x.cle = "AIza" + "c" * 35
    assert x.env_enfant("reel", cle=True)["GEMINI_API_KEY"] == x.cle


def test_safe_tools_neutralise_side_effect_tools(monkeypatch):
    from src import tools

    import outils_surs

    monkeypatch.setattr(tools, "TOOL_FUNCTIONS", dict(tools.TOOL_FUNCTIONS))
    neutralises = outils_surs.installer()
    assert {"shutdown_pc", "open_application", "empty_recycle_bin", "write_to_active_field"} <= set(neutralises)
    assert not set(neutralises) & outils_surs.OUTILS_AUTORISES
    result = tools.TOOL_FUNCTIONS["shutdown_pc"]()
    assert result["success"] is False and "aucune action" in result["error"]
    assert "shutdown_pc" in outils_surs.APPELS_NEUTRALISES
    assert tools.TOOL_FUNCTIONS["open_dashboard"] is not None


def test_sqlite_user_version_is_read_without_opening_the_database(tmp_path):
    valider_pr = _valider_pr()
    db = tmp_path / "memory.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA user_version = 2")
    con.execute("create table t(x)")
    con.commit()
    con.close()
    assert valider_pr.user_version_sqlite(db) == 2
    (tmp_path / "autre.db").write_bytes(b"pas une base")
    assert valider_pr.user_version_sqlite(tmp_path / "autre.db") is None


def test_official_real_script_verdict_lines_are_parsed():
    import re

    valider_pr = _valider_pr()
    sortie = "\n".join([
        "GEMINI LIVE RÉEL : PASS", "AUDIO RÉEL : FAIL", "RECONNEXION RÉELLE : PASS",
        "SESSION RESUMPTION RÉELLE : NON TESTABLE", "OUTILS RÉELS : PASS",
        "S1+S4 SILENCE PUIS EXPIRATION : PASS", "S6 CONTEXTE REEL APRES RECONNEXION : FAIL",
        "note : S2 : le rappel a échoué : PASS ? non",
    ])
    verdicts = dict(re.findall(valider_pr.VERDICT_RE, sortie, re.MULTILINE))
    assert verdicts["OUTILS RÉELS"] == "PASS" and verdicts["AUDIO RÉEL"] == "FAIL"
    assert verdicts["S1+S4 SILENCE PUIS EXPIRATION"] == "PASS"
    assert verdicts["S6 CONTEXTE REEL APRES RECONNEXION"] == "FAIL"
    assert len(verdicts) == 7


def test_snapshot_detects_any_change_under_the_protected_roots(tmp_path):
    valider_pr = _valider_pr()
    racine = tmp_path / "Jarvis"
    (racine / "logs").mkdir(parents=True)
    (racine / "memory.db").write_bytes(b"a" * 10)
    x = argparse.Namespace(racines_reelles=[racine])
    avant = valider_pr.instantane(x)
    assert valider_pr.instantane(x) == avant
    (racine / "memory.db").write_bytes(b"b" * 10)  # même taille : seule l'empreinte change
    os.utime(racine / "memory.db", ns=(1, avant[str(racine / "memory.db")][1]))
    assert valider_pr.instantane(x) != avant


def test_final_window_without_interaction_answers_nothing(tmp_path):
    entree, sortie = tmp_path / "e.json", tmp_path / "s.json"
    entree.write_text(json.dumps({"pr": 50, "notification_emise": True}), encoding="utf-8")
    subprocess.run([sys.executable, str(VALIDATION / "fenetre_finale.py"), "--entree", str(entree), "--sortie",
                    str(sortie), "--auto"], check=True, timeout=60)
    reponses = json.loads(sortie.read_text(encoding="utf-8"))
    assert reponses["captures"] == "non répondu" and reponses["notification_vue"] == "non répondu"
    assert reponses["micro"] == {"teste": False} and reponses["haut_parleur"]["teste"] is False


def test_report_groups_series_of_checks_with_the_same_cause():
    items = [P.Controle(f"D{i:02d}", f"d{i}", statut=P.NON_TESTE,
                        raison=f"pas de Windows natif (offscreen) : répétition = RÉUSSI — détail {i}") for i in range(1, 5)]
    items.append(P.Controle("E01", "e1", statut=P.NON_TESTE, raison="clé indisponible"))
    lignes = P.regrouper(items)
    assert lignes[0].startswith("D01–D04 (4 contrôles) : NON TESTÉ — pas de Windows natif (offscreen)")
    assert lignes[1].startswith("E01 « e1 » : NON TESTÉ — clé indisponible") and len(lignes) == 2


# ---------------------------------------------------------------------------
# C10 : protocole officiel du Dashboard (validation Windows PR #50 : « ÉCHEC »
# global alors que les sous-étapes étaient réussies)
# ---------------------------------------------------------------------------


def _protocole_dashboard_module():
    import importlib.util

    chemin = Path(__file__).resolve().parents[1] / "scripts" / "run_dashboard_protocol.py"
    spec = importlib.util.spec_from_file_location("run_dashboard_protocol_essai", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_protocol_step_survives_output_in_the_windows_ansi_code_page(tmp_path):
    """Un message natif en cp1252 ne doit pas faire planter le protocole (UTF-8 strict avant)."""
    module = _protocole_dashboard_module()
    commande = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xc9tape r\\xe9ussie\\n')"]
    resultat = module._run("essai", commande, tmp_path)
    assert resultat["returncode"] == 0
    assert resultat["last_line"].endswith("tape r\ufffdussie")


def test_c10_mismatch_between_exit_code_and_steps_stays_failed_but_explained(tmp_path):
    valider_pr = _valider_pr()
    x = _execution(tmp_path, valider_pr)
    x.args.sauter = []
    x.worktree = tmp_path
    x.python_tests = sys.executable
    out_dir = x.run / "protocole_dashboard"
    out_dir.mkdir(parents=True)
    etapes = [{"step": nom, "returncode": 0, "last_line": "ok"} for nom in ("unit", "integration", "perf_tests")]
    (out_dir / "summary.json").write_text(json.dumps({"steps": etapes, "success": True, "exit_code": 0}),
                                          encoding="utf-8")
    x.executer = lambda *a, **k: (1, "…\nTraceback (most recent call last):\nOSError: [Errno 22] Invalid argument\n")
    valider_pr.protocole_dashboard(x)
    controle = x.ctl("C10", "")
    assert controle.statut == P.ECHEC, "un code de sortie non nul n'est jamais transformé en réussite"
    assert "code de sortie 1 alors que toutes les étapes du résumé ont réussi" in controle.resultat
    assert "OSError: [Errno 22] Invalid argument" in controle.resultat
    # Cas nominal : code 0 et étapes réussies -> RÉUSSI.
    (tmp_path / "b").mkdir()
    x2 = _execution(tmp_path / "b", valider_pr)
    x2.args.sauter, x2.worktree, x2.python_tests = [], tmp_path, sys.executable
    (x2.run / "protocole_dashboard").mkdir(parents=True)
    (x2.run / "protocole_dashboard" / "summary.json").write_text(json.dumps({"steps": etapes}), encoding="utf-8")
    x2.executer = lambda *a, **k: (0, "Résumé : SUCCÈS")
    valider_pr.protocole_dashboard(x2)
    assert x2.ctl("C10", "").statut == P.REUSSI


def test_c10_names_the_exceeded_criterion_instead_of_the_last_line(tmp_path):
    """Validation Windows PR #50 : C10 affichait « perf_measures=ÉCHEC (… premier affichage 179) »,
    la dernière ligne (OnePage, indicative) au lieu du critère réellement dépassé."""
    valider_pr = _valider_pr()
    x = _execution(tmp_path, valider_pr)
    x.args.sauter, x.worktree, x.python_tests = [], tmp_path, sys.executable
    out_dir = x.run / "protocole_dashboard"
    out_dir.mkdir(parents=True)
    etapes = [{"step": "perf_tests", "returncode": 0, "last_line": "4 passed"},
              {"step": "perf_measures", "returncode": 1,
               "last_line": "initialisation contrôleur : 4 ms ; OnePage : premier affichage 179 ms",
               "failed_checks": {"event_loop_max_lag_ms": {"value": 182.4, "limit": 150.0}}}]
    (out_dir / "summary.json").write_text(json.dumps({"steps": etapes, "success": False, "exit_code": 1}),
                                          encoding="utf-8")
    x.executer = lambda *a, **k: (1, "Résumé : ÉCHEC")
    valider_pr.protocole_dashboard(x)
    controle = x.ctl("C10", "")
    assert controle.statut == P.ECHEC
    assert "critères en échec : event_loop_max_lag_ms=182.4 (seuil 150.0)" in controle.resultat
    assert "premier affichage 179" not in controle.resultat


def test_c10_names_the_phase_that_froze_the_event_loop(tmp_path):
    """Diagnostic du critère global event_loop_max_lag_ms : la phase du scénario où le pic est
    observé (ouverture, contenu, pages, onepage, retour) est citée ; le seuil ne change pas."""
    valider_pr = _valider_pr()
    x = _execution(tmp_path, valider_pr)
    x.args.sauter, x.worktree, x.python_tests = [], tmp_path, sys.executable
    out_dir = x.run / "protocole_dashboard"
    out_dir.mkdir(parents=True)
    etapes = [{"step": "perf_measures", "returncode": 1, "last_line": "OnePage : premier affichage 179 ms",
               "failed_checks": {"event_loop_max_lag_ms": {"value": 182.4, "limit": 150.0}},
               "measures": {"event_loop_max_lag_ms": 182.4,
                            "event_loop_max_lag_by_phase_ms": {"ouverture": 120.0, "contenu": 182.4, "onepage": 90.0}}}]
    (out_dir / "summary.json").write_text(json.dumps({"steps": etapes, "success": False, "exit_code": 1}),
                                          encoding="utf-8")
    x.executer = lambda *a, **k: (1, "Résumé : ÉCHEC")
    valider_pr.protocole_dashboard(x)
    controle = x.ctl("C10", "")
    assert controle.statut == P.ECHEC
    assert "event_loop_max_lag_ms=182.4 (seuil 150.0)" in controle.resultat
    assert "latence maximale pendant « contenu » (182.4 ms)" in controle.resultat


def test_lag_probe_charges_each_gap_to_the_phase_where_it_is_observed():
    import importlib.util
    import time

    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    chemin = Path(__file__).resolve().parents[1] / "scripts" / "dashboard_perf.py"
    spec = importlib.util.spec_from_file_location("dashboard_perf_essai", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    probe = module.LagProbe()
    probe.set_phase("contenu")
    probe.last = time.perf_counter() - 0.200  # battement suivant observé 200 ms plus tard
    probe._tick()
    probe.set_phase("pages")
    probe.last = time.perf_counter() - 0.050
    probe._tick()
    phases = probe.phases()
    assert 185.0 <= phases["contenu"] < 260.0 and 35.0 <= phases["pages"] < 110.0
    assert probe.max_gap == max(probe.by_phase.values())
    probe.start()
    assert probe.phases() == {} and probe.max_gap == 0.0
    probe.stop()


def test_protocol_summary_carries_the_failed_perf_criteria(tmp_path):
    module = _protocole_dashboard_module()
    perf = tmp_path / "perf.json"
    perf.write_text(json.dumps({"checks": {"event_loop_max_lag_ms": {"value": 182.4, "limit": 150.0, "ok": False},
                                           "page_switch_max_ms": {"value": 90.0, "limit": 400.0, "ok": True}},
                                "event_loop_max_lag_ms": 182.4, "onepage_build_ms": 179.0}), encoding="utf-8")
    details = module._perf_details(perf)
    assert details["failed_checks"] == {"event_loop_max_lag_ms": {"value": 182.4, "limit": 150.0}}
    assert details["measures"]["onepage_build_ms"] == 179.0
    perf.write_text(json.dumps({"checks": {}, "event_loop_max_lag_by_phase_ms": {"contenu": 182.4}}), encoding="utf-8")
    assert module._perf_details(perf)["measures"]["event_loop_max_lag_by_phase_ms"] == {"contenu": 182.4}
    assert module._perf_details(tmp_path / "absent.json")["failed_checks"] is None


def test_jarvis_harnesses_come_from_the_tested_commit(tmp_path):
    """La copie locale peut être en retard sur la PR : son harnais ne doit pas tester le nouveau code."""
    valider_pr = _valider_pr()
    x = _execution(tmp_path, valider_pr)
    x.worktree = None
    assert valider_pr.harnais(x, "gemini_reel_dashboard.py") == str(VALIDATION / "gemini_reel_dashboard.py")
    copie = tmp_path / "copie_pr"
    (copie / "scripts" / "validation").mkdir(parents=True)
    (copie / "scripts" / "validation" / "gemini_reel_dashboard.py").write_text("# harnais du commit testé\n")
    x.worktree = copie
    assert valider_pr.harnais(x, "gemini_reel_dashboard.py") == str(copie / "scripts" / "validation" /
                                                                   "gemini_reel_dashboard.py")
    # Commit testé plus ancien, sans ce harnais : repli explicite sur la copie locale.
    assert valider_pr.harnais(x, "controle_dashboard_reel.py") == str(VALIDATION / "controle_dashboard_reel.py")


# ---------------------------------------------------------------------------
# B05 / B09 (validation Windows PR #50 : B05 « ÉCHEC » car l'installation
# quotidienne embarque Qt 6.12.0 ; B09 « NON TESTÉ » par simple dépendance à B05)
# ---------------------------------------------------------------------------


def _qt_versions_module():
    import importlib.util

    chemin = Path(__file__).resolve().parents[1] / "src" / "qt_versions.py"
    spec = importlib.util.spec_from_file_location("qt_versions_essai", chemin)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_b05_states_what_the_installation_proves():
    valider_pr = _valider_pr()
    qv = _qt_versions_module()
    meme = {"pyside6.abi3.dll": "6.12.0", "shiboken6.abi3.dll": "6.12.0"}
    # Installation antérieure à la PR, cohérente : pas d'incompatibilité démontrée.
    statut, detail, point = valider_pr.evaluer_installation("6.12.0.0", meme, False, {}, qv)
    assert statut == P.REUSSI and "construite AVANT la borne" in detail and point is not None
    # Composants Qt divergents : incohérence réelle.
    statut, detail, _ = valider_pr.evaluer_installation("6.12.0.0", {"pyside6.abi3.dll": "6.11.2"}, False, {}, qv)
    assert statut == P.ECHEC and "incohérente" in detail
    # Build contrôlé dont la DLL contredit le contrôle.
    statut, detail, _ = valider_pr.evaluer_installation("6.12.0.0", meme, True, {"qt_runtime": "6.11.2"}, qv)
    assert statut == P.ECHEC
    # Build contrôlé conforme.
    meme_611 = {"pyside6.abi3.dll": "6.11.2", "shiboken6.abi3.dll": "6.11.2"}
    statut, detail, point = valider_pr.evaluer_installation("6.11.2.0", meme_611, True,
                                                            {"qt_runtime": "6.11.2"}, qv)
    assert statut == P.REUSSI and point is None
    # Build contrôlé mais hors plage : jamais accepté.
    statut, _detail, _ = valider_pr.evaluer_installation("6.12.0.0", meme, True, {"qt_runtime": "6.12.0"}, qv)
    assert statut == P.ECHEC
    assert valider_pr.evaluer_installation("absent", {}, False, {}, qv)[0] == P.ECHEC


def _execution_b09(tmp_path, valider_pr, cas):
    x = _execution(tmp_path, valider_pr)
    x.installation = tmp_path / "installation"
    x.racines_reelles = []
    x.junit = {"cas": cas}
    x.b09_migration = (True, "base réelle migrable par la PR : intégrité ok → ok")
    return x


def test_b09_rests_on_data_config_and_reversible_update_not_on_qt(tmp_path):
    valider_pr = _valider_pr()
    tous = [{"nom": nom, "issue": "passed"} for nom in valider_pr.TESTS_MISE_A_JOUR]
    x = _execution_b09(tmp_path, valider_pr, tous)
    x.ctl("B05", "").regler(P.ECHEC, "Qt6Core 6.12.0")  # sans effet sur B09
    valider_pr.compatibilite_installation(x)
    controle = x.ctl("B09", "")
    assert controle.statut == P.REUSSI, controle.resultat
    assert "mise à jour" in controle.resultat and "tous réussis" in controle.resultat

    (tmp_path / "b").mkdir()
    un_echec = [dict(c, issue="failure") if c["nom"] == "test_rollback_restores_previous" else c for c in tous]
    x2 = _execution_b09(tmp_path / "b", valider_pr, un_echec)
    valider_pr.compatibilite_installation(x2)
    assert x2.ctl("B09", "").statut == P.ECHEC

    (tmp_path / "c").mkdir()
    x3 = _execution_b09(tmp_path / "c", valider_pr, [])
    valider_pr.compatibilite_installation(x3)
    assert x3.ctl("B09", "").statut == P.NON_TESTE
    assert "mise à jour" in x3.ctl("B09", "").raison
