"""Profil de la PREMIÈRE ouverture du Dashboard, phase par phase (D01).

Validation Windows du 2026-10-16 (PR #50, Pentium Gold G5400) : fenêtre
affichée en 1 867 ms (seuil 1 500), contenu complet en 1 910 ms — alors que
la même ouverture prend ~50 ms hors écran sous Linux. Le contenu ne coûte que
~45 ms : tout le temps est AVANT la première peinture. Ce script découpe CE
MÊME évènement (de la demande d'ouverture à la fenêtre peinte) en phases,
dans des processus NEUFS (première ouverture réelle, rien de préchauffé), et
mesure séparément le coût à froid de la base de polices de Qt (premier texte
rendu dans un processus) :

* ``import_fenetre`` : import de ``UI.dashboard.window`` et de ses modules ;
* ``construction`` : ``DashboardWindow(...)`` (en-tête, navigation, style) ;
* ``affichage`` : ``show_restored()`` (fenêtre native, géométrie) ;
* ``premiere_peinture`` : jusqu'à la première peinture (polissage du style,
  polices, composition) — c'est l'évènement « fenêtre affichée » de D01 ;
* ``contenu`` : page et widgets construits après la peinture (indicatif).

Puis les ouvertures SUIVANTES dans le même processus (fenêtre masquée puis
rouverte). Aucune donnée réelle : dossier temporaire (``isolated_jarvis``),
moteur simulé.

    python scripts/dashboard_first_open_profile.py --natif --repetitions 5
    python scripts/dashboard_first_open_profile.py --json profil.json

Hors Windows, le rendu est hors écran : chiffres indicatifs seulement.
"""

from __future__ import annotations

import time

_T_PROCESS = time.perf_counter()  # au plus tôt : coût réel du démarrage du processus

import argparse  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import statistics  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SEUIL_MS = 1500.0


def _ms(a: float, b: float) -> float:
    return round((b - a) * 1000.0, 1)


def mesure_unique(natif: bool, reouvertures: int) -> dict:
    """Une première ouverture dans CE processus (neuf), puis des réouvertures."""
    if natif and sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ.setdefault("JARVIS_REDUCED_MOTION", "0")
    sys.path.insert(0, str(ROOT))
    phases: dict[str, float] = {}
    t = time.perf_counter()
    from PySide6 import QtCore, QtGui, QtWidgets  # noqa: F401

    phases["import_qt"] = _ms(t, time.perf_counter())
    t = time.perf_counter()
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    phases["qapplication"] = _ms(t, time.perf_counter())
    t = time.perf_counter()
    from src.runtime_status import RUNTIME, EngineState
    from tests.dashboard_support import isolated_jarvis

    phases["import_jarvis"] = _ms(t, time.perf_counter())
    resultat: dict = {"plateforme_qt": "", "phases_ms": phases}
    with isolated_jarvis("jarvis-profil-ouverture-") as env:
        RUNTIME.reset()
        RUNTIME.set_engine_state(EngineState.READY, model="modele-de-test")
        RUNTIME.update(audio_state="ok", wake_word="loaded", memory_state="ok")
        t = time.perf_counter()
        from UI.dashboard.controller import DashboardController

        controller = DashboardController(
            state_path=str(Path(env["root"]) / "s.json"), attach_bridge=False,
            dialog_executor=lambda d: True, open_path=lambda p: True,
            native_sender=lambda title, message: None, reduced_motion_probe=lambda: False,
        )
        phases["controleur"] = _ms(t, time.perf_counter())
        # ---- Évènement D01 : de la demande d'ouverture à la fenêtre peinte ----
        t0 = time.perf_counter()
        import UI.dashboard.window  # noqa: F401

        t_import = time.perf_counter()
        window = controller.ensure_window()
        t_build = time.perf_counter()
        stamps: dict[str, float] = {}
        original = window._complete_initial_build

        def premiere_peinture() -> None:
            stamps.setdefault("peinture", time.perf_counter())
            original()

        window._complete_initial_build = premiere_peinture
        controller.open()
        t_show = time.perf_counter()
        deadline = time.monotonic() + 15.0
        while "peinture" not in stamps and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.001)
        t_paint = stamps.get("peinture", time.perf_counter())
        while getattr(window, "_initial_build_pending", False) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.001)
        t_content = time.perf_counter()
        phases.update(
            import_fenetre=_ms(t0, t_import),
            construction=_ms(t_import, t_build),
            affichage=_ms(t_build, t_show),
            premiere_peinture=_ms(t_show, t_paint),
            contenu=_ms(t_paint, t_content),
        )
        resultat["premiere_ouverture_ms"] = _ms(t0, t_paint)
        resultat["contenu_complet_ms"] = _ms(t0, t_content)
        resultat["depuis_lancement_processus_ms"] = _ms(_T_PROCESS, t_paint)
        resultat["plateforme_qt"] = QtGui.QGuiApplication.platformName()
        durees = []
        for _ in range(max(0, reouvertures)):
            controller.hide_window()
            app.processEvents()
            time.sleep(0.05)
            t = time.perf_counter()
            controller.open()
            app.processEvents()
            durees.append(_ms(t, time.perf_counter()))
        resultat["reouvertures_ms"] = durees
        controller.shutdown()
        RUNTIME.reset()
    return resultat


def cout_polices_a_froid(natif: bool) -> dict:
    """Coût du PREMIER texte rendu dans un processus neuf (base de polices Qt)."""
    if natif and sys.platform == "win32":
        os.environ["QT_QPA_PLATFORM"] = "windows"
    else:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication, QLabel

    app = QApplication.instance() or QApplication([])
    t = time.perf_counter()
    familles = len(QFontDatabase.families())
    t_db = time.perf_counter()
    label = QLabel("Jarvis — Tableau de bord")
    label.show()
    app.processEvents()
    t_label = time.perf_counter()
    return {"familles": familles, "base_polices_ms": _ms(t, t_db), "premier_libelle_ms": _ms(t_db, t_label)}


def _enfant(args: list[str]) -> dict:
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=300, cwd=ROOT)
    for line in reversed(proc.stdout.splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    return {"erreur": (proc.stderr or proc.stdout)[-600:], "code": proc.returncode}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--natif", action="store_true", help="plateforme Qt native (Windows)")
    parser.add_argument("--repetitions", type=int, default=3, help="processus neufs (premières ouvertures)")
    parser.add_argument("--reouvertures", type=int, default=3)
    parser.add_argument("--json", default="", help="fichier de sortie")
    parser.add_argument("--enfant", choices=("ouverture", "polices"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.enfant == "ouverture":
        print(json.dumps(mesure_unique(args.natif, args.reouvertures), ensure_ascii=False))
        return 0
    if args.enfant == "polices":
        print(json.dumps(cout_polices_a_froid(args.natif), ensure_ascii=False))
        return 0
    natif = ["--natif"] if args.natif else []
    essais = [_enfant(["--enfant", "ouverture", "--reouvertures", str(args.reouvertures), *natif])
              for _ in range(max(1, args.repetitions))]
    polices = [_enfant(["--enfant", "polices", *natif]) for _ in range(max(1, min(3, args.repetitions)))]
    valides = [e for e in essais if "premiere_ouverture_ms" in e]
    rapport = {
        "plateforme": f"{sys.platform} · Python {sys.version.split()[0]}",
        "cpu": os.cpu_count(),
        "seuil_ms": SEUIL_MS,
        "essais": essais,
        "polices_a_froid": polices,
    }
    if valides:
        premieres = [e["premiere_ouverture_ms"] for e in valides]
        reouv = [d for e in valides for d in e.get("reouvertures_ms", [])]
        rapport["synthese"] = {
            "premiere_ouverture_mediane_ms": round(statistics.median(premieres), 1),
            "premiere_ouverture_max_ms": max(premieres),
            "reouverture_mediane_ms": round(statistics.median(reouv), 1) if reouv else None,
            "phases_medianes_ms": {
                cle: round(statistics.median(e["phases_ms"][cle] for e in valides), 1)
                for cle in valides[0]["phases_ms"]
            },
        }
    if args.json:
        Path(args.json).write_text(json.dumps(rapport, ensure_ascii=False, indent=2), encoding="utf-8")
    synthese = rapport.get("synthese", {})
    print(f"Plateforme : {rapport['plateforme']} ({rapport['cpu']} CPU), Qt "
          f"{valides[0]['plateforme_qt'] if valides else '?'}")
    for index, essai in enumerate(essais, 1):
        if "premiere_ouverture_ms" in essai:
            print(f"  processus {index} : première ouverture {essai['premiere_ouverture_ms']} ms "
                  f"(contenu {essai['contenu_complet_ms']} ms) — phases {essai['phases_ms']} — "
                  f"réouvertures {essai['reouvertures_ms']}")
        else:
            print(f"  processus {index} : ERREUR {essai.get('erreur', '')[:300]}")
    for index, police in enumerate(polices, 1):
        print(f"  polices à froid {index} : {police}")
    if synthese:
        print(f"Synthèse : première ouverture médiane {synthese['premiere_ouverture_mediane_ms']} ms "
              f"(max {synthese['premiere_ouverture_max_ms']}, seuil {SEUIL_MS:.0f}), réouverture médiane "
              f"{synthese['reouverture_mediane_ms']} ms ; phases médianes {synthese['phases_medianes_ms']}")
    return 0 if valides else 1


if __name__ == "__main__":
    raise SystemExit(main())
