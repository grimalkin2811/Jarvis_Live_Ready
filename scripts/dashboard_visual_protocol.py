"""Protocole visuel du Dashboard : captures d'écran reproductibles.

Usage :
    python scripts/dashboard_visual_protocol.py --out build/dashboard_screens
    python scripts/dashboard_visual_protocol.py --out DIR --themes jarvis --quick

IMPORTANT (honnêteté des captures) : les captures sont produites hors écran
(``QT_QPA_PLATFORM=offscreen``) avec des données de DÉMONSTRATION isolées
(dossier temporaire) et un état moteur SIMULÉ (aucune session Gemini réelle,
aucun micro). Elles valident le rendu, la mise en page et les états ; elles
ne prouvent pas un fonctionnement de bout en bout sur Windows.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("JARVIS_REDUCED_MOTION", "0")


def _seed(env, block: threading.Event):
    from src.background_tasks import manager as manager_module
    from src.conversation import get_default_conversation_context
    from src.runtime_status import RUNTIME, EngineState
    from src.settings import DEFAULT_MODEL
    from tests.dashboard_support import FakeTextEngine, make_task_manager, wait_task

    memory = env["memory"]
    samples = [
        ("Je préfère des réponses courtes le matin.", "preference", 4),
        ("Mon projet actuel s'appelle Atlas (application de cartographie).", "project", 5),
        ("Ma sœur Claire habite à Lyon.", "person", 3),
        ("J'utilise Python 3.11 sous Windows 11.", "configuration", 3),
        ("Décision : les sauvegardes se font le dimanche soir.", "decision", 4),
        ("Je fais du vélo tous les samedis matin.", "habit", 2),
    ]
    for content, category, importance in samples:
        memory.create_memory_entry(content, category, importance, source="démo", distinct=True)
    first = memory.browse_memories(limit=50)["memories"][-1]
    from UI.dashboard.memory_service import version_of

    memory.update_memory_checked(first["id"], version_of(first), content="Je préfère des réponses très courtes le matin.")
    env["todo"].add_task("Relire le compte rendu Atlas", due="demain 9h", priority="haute")
    env["todo"].add_task("Appeler le garage")
    from src import tools

    tools.take_note("Idée : un widget météo pour l'Accueil.")
    tools.take_note("Penser à exporter les notes vendredi.")
    docs = Path(os.environ["JARVIS_USER_CONTENT_DIR"])
    (docs / "tasks").mkdir(parents=True, exist_ok=True)
    (docs / "Lettre au syndic.txt").write_text("Madame, Monsieur…", encoding="utf-8")
    (docs / "tasks" / "Comparatif ordinateurs.md").write_text("# Comparatif", encoding="utf-8")

    task_manager = make_task_manager(Path(env["root"]) / "tasks", fail_titles=("Analyse des factures",), block=None)
    manager_module._DEFAULT = task_manager
    done = task_manager.create_task("Comparatif ordinateurs portables", "Comparer 3 modèles")
    failed = task_manager.create_task("Analyse des factures", "Analyser les factures 2026")
    wait_task(task_manager, done.id, ["COMPLETED"])
    wait_task(task_manager, failed.id, ["FAILED"])
    running_manager_executor = task_manager._provided_executor
    running_manager_executor.block = block
    task_manager.create_task("Recherche approfondie : énergie solaire", "Synthèse détaillée")

    RUNTIME.reset()
    RUNTIME.set_engine_state(EngineState.STARTING, "chargement")
    RUNTIME.update(memory_state="ok", tasks_state="ok", wake_word="loaded", wake_word_detail="onnx",
                   audio_state="ok", audio_detail="flux démarrés (simulation)", model=DEFAULT_MODEL)
    RUNTIME.set_engine_state(EngineState.READY, "", model=DEFAULT_MODEL, session_generation=2)
    context = get_default_conversation_context()
    context.start_new_conversation(reason="démo")
    context.add_user_message("Quelle est la météo prévue demain à Tours ?")
    context.add_tool_interaction("get_forecast", {"city": "Tours", "days": 1}, {"success": True, "summary": "12 °C, averses"})
    context.add_assistant_message("Demain à Tours : environ 12 °C avec des averses l'après-midi. Pense au parapluie.")
    context.add_user_message("Ajoute « acheter un parapluie » à ma liste.")
    context.add_assistant_message("C'est noté dans tes tâches personnelles.")
    engine = FakeTextEngine(context, reply="Bien reçu (réponse de démonstration).")
    RUNTIME.register_text_handler(engine)
    return task_manager


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=str(ROOT / "build" / "dashboard_screens"))
    parser.add_argument("--themes", default="jarvis,edith,friday")
    parser.add_argument("--quick", action="store_true", help="Accueil seulement pour chaque thème.")
    args = parser.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    from tests.dashboard_support import isolated_jarvis, pump

    shots: list[dict] = []
    block = threading.Event()
    with isolated_jarvis("jarvis-dashboard-visual-") as env:
        task_manager = _seed(env, block)
        from UI.dashboard.controller import DashboardController

        controller = DashboardController(
            state_path=str(Path(env["root"]) / "dashboard_state.json"), attach_bridge=False,
            dialog_executor=lambda dialog: True, open_path=lambda path: True,
            native_sender=lambda title, message: None, reduced_motion_probe=lambda: False,
        )
        hub = controller.hub
        hub.notifications.notify("network", "Connexion au modèle perdue", "Reconnexion automatique (démo).",
                                 level="warning", key="demo-net")
        controller.open()
        window = controller.window

        def settle(seconds: float = 0.5) -> None:
            pump(app, seconds)
            hub.jobs.wait_idle(5)
            pump(app, 0.15)

        def capture(name: str, description: str, widget=None) -> None:
            settle(0.35)
            target = widget or window
            path = out / f"{name}.png"
            target.grab().save(str(path))
            shots.append({"file": path.name, "description": description})

        window.resize(1600, 980)
        settle(0.8)
        pages = [p.id for p in controller.store.config.layout.pages]
        for theme in [t.strip() for t in args.themes.split(",") if t.strip()]:
            controller.theme_manager.update(theme=theme)
            settle(0.4)
            for page in (pages if not args.quick else ["accueil"]):
                window.open_page(page, animate=False)
                if page == "memoire":
                    settle(0.4)
                    canvas = window._current_canvas()
                    frame = next(iter(canvas.frames.values()))
                    memory_widget = frame.widget
                    if memory_widget.list.count():
                        rows = [memory_widget.list.item(i).text() for i in range(memory_widget.list.count())]
                        target = next((i for i, text in enumerate(rows) if "très courtes" in text), 0)
                        memory_widget.list.setCurrentRow(target)
                        settle(0.4)
                        memory_widget.history_button.setChecked(True)
                        settle(0.4)
                        if memory_widget.history_list.count():
                            memory_widget.history_list.setCurrentRow(0)
                capture(f"{theme}_{page}", f"Thème {theme.upper()} — page {page}")
        controller.theme_manager.update(theme="jarvis")
        if not args.quick:
            controller.set_view_mode("onepage")
            if window._onepage is not None:
                # Construction progressive (une section par tour de boucle) :
                # on la termine pour des captures déterministes.
                window._onepage.build_all()
            settle(0.6)
            capture("jarvis_onepage_haut", "OnePage — haut de page")
            bar = window._onepage.verticalScrollBar()
            bar.setValue(bar.maximum())
            settle(0.5)
            capture("jarvis_onepage_bas_diagnostics", "OnePage — bas de page (diagnostics en dernier)")
            controller.set_view_mode("multipage")
            settle(0.4)
            window.open_page("accueil", animate=False)
            window.resize(820, 900)
            settle(0.6)
            capture("jarvis_etroit", "État étroit (820 px) : navigation par icônes, grille 4 colonnes")
            window.resize(1200, 900)
            settle(0.5)
            capture("jarvis_normal", "État normal (1200 px) : grille 8 colonnes")
            window.resize(1600, 980)
            window.set_edit_mode(True)
            settle(0.4)
            capture("jarvis_edition", "Mode édition : poignées, réglages, masquer/retirer, bibliothèque")
            window.set_edit_mode(False, save=False)
            from UI.dashboard.dialogs import ConfirmDialog

            dialog = ConfirmDialog(3, "Supprimer un souvenir", "Supprimer ce souvenir ?",
                                   "« Ma sœur Claire habite à Lyon. »\nCatégorie : Personne", window)
            dialog.resize(460, 220)
            dialog.show()
            capture("dialogue_niveau3", "Confirmation renforcée (niveau 3)", dialog)
            dialog.close()
        block.set()
        controller.shutdown()
        task_manager.close(wait=True)
    index = out / "index.json"
    index.write_text(json.dumps({"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                                 "platform": os.environ.get("QT_QPA_PLATFORM"), "shots": shots},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(shots)} capture(s) écrite(s) dans {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
