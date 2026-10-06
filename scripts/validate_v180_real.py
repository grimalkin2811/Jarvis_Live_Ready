"""Validation RÉELLE de Jarvis v1.8 (API + préflight Windows).

Ce script n'utilise aucun faux serveur et aucun modèle simulé. Les tests API
font de vraies requêtes Gemini. Il ne prend jamais une clé en argument, ne
l'affiche pas et ne l'écrit pas dans le rapport.

Exemples Windows, depuis la racine du dépôt :

    py -3 scripts\\validate_v180_real.py --preflight
    py -3 scripts\\validate_v180_real.py --api
    py -3 scripts\\validate_v180_real.py --s1-s6
    py -3 scripts\\validate_v180_real.py --manual-protocol

``--api`` consomme normalement deux appels du quota 3.8 au maximum : une
exécution complexe, une deuxième pendant le test de concurrence, et aucun
appel volontaire destiné à épuiser une limite.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import paths, settings, wakeword  # noqa: E402
from src.background_tasks.config import BackgroundModelConfig  # noqa: E402
from src.background_tasks.gateway import BackgroundModelGateway  # noqa: E402
from src.background_tasks.manager import TaskManager  # noqa: E402
from src.background_tasks.models import TaskPriority, TaskStatus  # noqa: E402
from src.background_tasks.quota import ComplexModelQuota, QuotaExceededError  # noqa: E402
from src.background_tasks.router import TaskRouter  # noqa: E402

_KEY_RE = re.compile(r"AIza[A-Za-z0-9_-]{10,}|AQ\.[A-Za-z0-9_-]+")
TERMINAL = {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}


@dataclass
class Check:
    category: str
    name: str
    status: str  # PASS / FAIL / NON_TESTABLE / INFO
    detail: str = ""


class Report:
    def __init__(self) -> None:
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.checks: list[Check] = []

    def add(self, category: str, name: str, status: str, detail: str = "") -> None:
        clean = redact(detail).replace("\n", " ").strip()
        self.checks.append(Check(category, name, status, clean))
        safe_print(f"[{status}] {category} / {name}" + (f" — {clean}" if clean else ""))

    def write(self, destination: Path) -> None:
        payload = {
            "validation": "Jarvis v1.8 réelle",
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "platform": platform.platform(),
            "python": sys.version.split()[0],
            "checks": [asdict(item) for item in self.checks],
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        safe_print(f"Rapport sans secret : {destination}")

    def failed(self) -> bool:
        return any(item.status == "FAIL" for item in self.checks)


def redact(value) -> str:
    return _KEY_RE.sub("<clé masquée>", str(value or ""))


def safe_print(*values) -> None:
    print(redact(" ".join(str(value) for value in values)), flush=True)


def load_api_key() -> tuple[str, str]:
    """Retourne clé + origine. La clé ne doit jamais quitter la mémoire."""
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.getenv(name, "").strip()
        if value:
            return value, f"variable {name}"
    try:
        value = str(settings.load_file_config().get("api_key") or "").strip()
        if value:
            return value, "configuration Jarvis"
    except Exception:
        pass
    return "", "absente"


def configured_main_model() -> str:
    model = os.getenv("GEMINI_MODEL", "").strip()
    if model:
        return model
    try:
        model = str(settings.load_file_config().get("model") or "").strip()
    except Exception:
        model = ""
    return model or "gemini-2.5-flash-native-audio-preview-12-2025"


def preflight(report: Report) -> None:
    key, source = load_api_key()
    report.add("PRÉREQUIS", "configuration Gemini", "PASS" if key else "NON_TESTABLE", source)
    report.add("PRÉREQUIS", "système Windows", "PASS" if os.name == "nt" else "NON_TESTABLE", platform.platform())

    modules = ("google.genai", "numpy", "sounddevice", "PySide6", "onnxruntime", "openwakeword", "pyttsx3")
    for module in modules:
        available = importlib.util.find_spec(module) is not None
        report.add("DÉPENDANCES", module, "PASS" if available else "FAIL")

    try:
        import sounddevice as sd
        devices = sd.query_devices()
        defaults = sd.default.device
        input_id, output_id = int(defaults[0]), int(defaults[1])
        input_device = sd.query_devices(input_id, "input") if input_id >= 0 else None
        output_device = sd.query_devices(output_id, "output") if output_id >= 0 else None
        usable = bool(
            devices
            and input_device
            and output_device
            and input_device.get("max_input_channels", 0) > 0
            and output_device.get("max_output_channels", 0) > 0
        )
        detail = (
            f"{len(devices)} périphérique(s); entrée={input_device.get('name') if input_device else 'absente'}; "
            f"sortie={output_device.get('name') if output_device else 'absente'}"
        )
        report.add("AUDIO", "périphériques PortAudio", "PASS" if usable else "FAIL", detail)
    except Exception as exc:
        report.add("AUDIO", "périphériques PortAudio", "FAIL", f"{type(exc).__name__}: {exc}")

    try:
        ok, detail = wakeword.smoke_check(download=False, verbose=False)
        report.add("WAKE WORD", "chaîne ONNX", "PASS" if ok else "FAIL", detail)
    except Exception as exc:
        report.add("WAKE WORD", "chaîne ONNX", "FAIL", f"{type(exc).__name__}: {exc}")

    try:
        if os.name != "nt":
            os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication, QLabel
        app = QApplication.instance() or QApplication([])
        label = QLabel("Jarvis v1.8 validation")
        label.show()
        app.processEvents()
        label.close()
        report.add("QT", "création widget et event loop", "PASS")
    except Exception as exc:
        report.add("QT", "création widget et event loop", "FAIL", f"{type(exc).__name__}: {exc}")


def _task_detail(task) -> str:
    return f"id={task.id}; status={task.status.value}; model={task.model}; progression={task.progress}; erreur={task.error or 'aucune'}"


async def wait_terminal(manager: TaskManager, task_id: str, timeout: float = 300.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = manager.get_task(task_id)
        if task is not None and task.status in TERMINAL:
            return task
        await asyncio.sleep(0.2)
    raise TimeoutError(f"tâche {task_id} non terminée après {timeout:.0f}s")


async def check_live_model(client, model: str) -> None:
    """Ouvre réellement le WebSocket et attend setupComplete via __aenter__."""
    config = {"response_modalities": ["AUDIO"]}
    async with client.aio.live.connect(model=model, config=config):
        return


async def run_api_validation(report: Report) -> None:
    models = BackgroundModelConfig.from_env()
    active_models = (models.router_model, models.simple_model, models.medium_model)
    architecture_ok = all(model == "gemini-3-flash-preview" for model in active_models)
    report.add(
        "ARCHITECTURE",
        "background Flash Preview classique",
        "PASS" if architecture_ok and not hasattr(BackgroundModelGateway, "generate_live") else "FAIL",
        f"router={models.router_model}; simple={models.simple_model}; medium={models.medium_model}; endpoint=generateContent",
    )

    key, source = load_api_key()
    if not key:
        for name in ("Gemini 2.5 Native Audio", "Gemini 3 Flash Preview", "Gemini 3.8 Flash", "Google Search grounding"):
            report.add("API RÉELLE", name, "NON_TESTABLE", "aucune clé/configuration Gemini")
        return

    from google import genai
    client = genai.Client(api_key=key)
    main_model = configured_main_model()
    report.add("API RÉELLE", "clé chargée sans exposition", "PASS", source)

    try:
        await asyncio.wait_for(check_live_model(client, main_model), timeout=30)
        report.add("API RÉELLE", "Gemini 2.5 Native Audio disponible", "PASS", main_model)
    except Exception as exc:
        report.add("API RÉELLE", "Gemini 2.5 Native Audio disponible", "FAIL", f"{main_model}: {type(exc).__name__}: {exc}")

    router = TaskRouter(client, models)
    router_cases = [
        ("A météo", "Donne-moi la météo de demain.", "simple", models.simple_model),
        ("B processeurs", "Compare deux processeurs pour choisir lequel acheter.", "medium", models.medium_model),
        ("C moteurs ioniques", "Fais une recherche poussée et une analyse détaillée sur les moteurs ioniques.", "complex", models.complex_model),
    ]
    for title, prompt, expected_complexity, expected_model in router_cases:
        try:
            decision = await asyncio.wait_for(router.route(title, prompt, TaskPriority.NORMAL), timeout=60)
            valid = decision.complexity.value == expected_complexity and decision.model == expected_model
            detail = (
                f"complexity={decision.complexity.value}; model={decision.model}; priority={decision.priority.value}; "
                f"steps={decision.estimated_steps}; tools={decision.requires_tools}; reason={decision.reason}"
            )
            report.add("ROUTER RÉEL", title, "PASS" if valid else "FAIL", detail)
        except Exception as exc:
            report.add("ROUTER RÉEL", title, "FAIL", f"{type(exc).__name__}: {exc}")

    # L'outil Search est testé via Generate Content sur Flash Preview afin de
    # ne pas consommer un appel 3.8. Une réponse non vide valide l'outil serveur.
    try:
        grounded = await asyncio.wait_for(
            BackgroundModelGateway(client).generate(
                models.simple_model,
                "Quelle est la date d'aujourd'hui à Paris ? Donne une source web vérifiable.",
                system_instruction="Utilise Google Search et réponds brièvement en français avec la source consultée.",
                tools=[{"google_search": {}}],
            ),
            timeout=90,
        )
        report.add("API RÉELLE", "Google Search grounding", "PASS" if grounded.text else "FAIL", f"réponse={grounded.text[:240]}; tokens={grounded.total_tokens}")
    except Exception as exc:
        report.add("API RÉELLE", "Google Search grounding", "FAIL", f"{type(exc).__name__}: {exc}")

    await run_background_scenarios(report, key, models, client, main_model)


async def run_background_scenarios(report: Report, key: str, models: BackgroundModelConfig, client, main_model: str) -> None:
    with tempfile.TemporaryDirectory(prefix="jarvis-v180-real-") as folder:
        root = Path(folder)
        manager = TaskManager(key, config=models, storage_path=root / "tasks.json", output_dir=root / "results", client=client)
        events: dict[str, list[str]] = {}
        manager.add_hook(lambda task: events.setdefault(task.id, []).append(task.status.value))
        try:
            # Tâche simple réelle.
            before = time.monotonic()
            simple = manager.create_task("Résumé validation", "Résume en cinq points les bénéfices d'une sauvegarde régulière.")
            submission = time.monotonic() - before
            report.add("BACKGROUND", "soumission non bloquante", "PASS" if submission < 0.25 else "FAIL", f"{submission:.3f}s; statut initial={simple.status.value}")
            simple_done = await wait_terminal(manager, simple.id)
            simple_ok = simple_done.status is TaskStatus.COMPLETED and simple_done.model == models.simple_model and bool(simple_done.result)
            report.add("BACKGROUND", "tâche simple Gemini 3 Flash Preview classique", "PASS" if simple_ok else "FAIL", _task_detail(simple_done))
            unread = any(item.id == simple.id for item in manager.list_unread_completed_tasks())
            result = manager.get_task_result(simple.id, mark_seen=True)
            seen = manager.get_task(simple.id).seen
            state_path = events.get(simple.id, [])
            lifecycle = simple.status.value == "QUEUED" and "RUNNING" in state_path and "COMPLETED" in state_path
            report.add("BACKGROUND", "cycle QUEUED/RUNNING/COMPLETED", "PASS" if lifecycle else "FAIL", " -> ".join(state_path))
            report.add("BACKGROUND", "non vue puis vue", "PASS" if unread and seen and result and result.get("result") else "FAIL")

            # Tâche complexe réelle + quota.
            quota_before = manager.quota_status()
            complex_task = manager.create_task(
                "Analyse moteurs ioniques",
                "Fais une recherche poussée, multi-sources et une analyse détaillée sur les moteurs ioniques. Prépare un document Markdown avec les sources.",
                "high",
            )
            complex_done = await wait_terminal(manager, complex_task.id)
            quota_after = manager.quota_status()
            quota_delta = quota_after["calls_today"] - quota_before["calls_today"]
            complex_ok = complex_done.status is TaskStatus.COMPLETED and complex_done.model == models.complex_model and bool(complex_done.result)
            report.add("BACKGROUND", "tâche complexe Gemini 3.8 Flash", "PASS" if complex_ok else "FAIL", _task_detail(complex_done))
            report.add("QUOTA", "appel 3.8 comptabilisé", "PASS" if quota_delta == 1 else "FAIL", f"avant={quota_before}; après={quota_after}")
            report.add("BACKGROUND", "document référencé", "PASS" if complex_done.files and all(Path(item).exists() for item in complex_done.files) else "FAIL", repr(complex_done.files))

            # Deux tâches simultanées, dont une deuxième 3.8. On ne cherche pas
            # à atteindre les limites RPM/RPD.
            first = manager.create_task("Conseils courts", "Donne cinq conseils simples pour organiser un bureau.")
            second = manager.create_task("Comparatif stockage", "Fais une recherche poussée multi-sources comparant SSD NVMe et stockage cloud pour une PME, avec analyse détaillée.")
            different = first.id != second.id
            active_together = False
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                active = {item.id for item in manager.list_active_tasks()}
                if first.id in active and second.id in active:
                    active_together = True
                    break
                await asyncio.sleep(0.05)
            try:
                await asyncio.wait_for(check_live_model(client, main_model), timeout=30)
                still_active = bool(manager.list_active_tasks())
                report.add(
                    "NON-BLOCAGE",
                    "connexion Gemini 2.5 pendant deux tâches",
                    "PASS" if still_active else "FAIL",
                    "WebSocket principal ouvert pendant que le Task Manager restait actif; ce contrôle ne remplace pas le test audio manuel",
                )
            except Exception as exc:
                report.add("NON-BLOCAGE", "connexion Gemini 2.5 pendant deux tâches", "FAIL", f"{type(exc).__name__}: {exc}")
            first_done, second_done = await asyncio.gather(wait_terminal(manager, first.id), wait_terminal(manager, second.id))
            concurrent_ok = different and active_together and first_done.status is TaskStatus.COMPLETED and second_done.status is TaskStatus.COMPLETED
            model_pair = {first_done.model, second_done.model} == {models.simple_model, models.complex_model}
            report.add("BACKGROUND", "deux tâches simultanées", "PASS" if concurrent_ok and model_pair else "FAIL", f"{_task_detail(first_done)} | {_task_detail(second_done)}")

            # Persistance réelle du compteur (sans appel supplémentaire).
            restored = ComplexModelQuota(
                rpm=models.complex_rpm, rpd=models.complex_rpd, tpm=models.complex_tpm,
                concurrency=models.complex_concurrency, storage_path=root / "background_quota.json",
            ).snapshot()
            report.add("QUOTA", "compteur persistant", "PASS" if restored.calls_today == manager.quota_status()["calls_today"] else "FAIL", repr(asdict(restored)))

            # Vérifie le refus local sans effectuer ni gaspiller un appel API :
            # même état persistant, limite de validation fixée au compteur actuel.
            guard = ComplexModelQuota(
                rpm=models.complex_rpm,
                rpd=max(1, restored.calls_today),
                tpm=models.complex_tpm,
                concurrency=1,
                storage_path=root / "background_quota.json",
            )
            try:
                await guard.acquire()
                guard.release()
                report.add("QUOTA", "refus récupérable à la limite", "FAIL", "la garde locale a autorisé un appel au-delà de la limite de validation")
            except Exception as exc:
                report.add(
                    "QUOTA",
                    "refus récupérable à la limite",
                    "PASS" if isinstance(exc, QuotaExceededError) else "FAIL",
                    f"{type(exc).__name__}: {exc}",
                )
        finally:
            manager.close(wait=True)

        # Erreur API contrôlée et isolée : vrai appel vers un identifiant
        # volontairement inexistant, sans consommer Gemini 3.8.
        invalid = BackgroundModelConfig(
            router_model="jarvis-validation-model-intentionally-invalid",
            simple_model=models.simple_model,
            complex_model=models.complex_model,
            timeout_seconds=30,
        )
        failing = TaskManager(key, config=invalid, storage_path=root / "failed.json", output_dir=root / "failed-results", client=client)
        try:
            bad = failing.create_task("Erreur contrôlée", "Validation d'isolation d'une erreur API.")
            failed = await wait_terminal(failing, bad.id, timeout=90)
            report.add("BACKGROUND", "erreur contrôlée vers FAILED", "PASS" if failed.status is TaskStatus.FAILED and bool(failed.error) else "FAIL", _task_detail(failed))
            try:
                await asyncio.wait_for(check_live_model(client, main_model), timeout=30)
                report.add("NON-BLOCAGE", "Gemini principal après erreur background", "PASS")
            except Exception as exc:
                report.add("NON-BLOCAGE", "Gemini principal après erreur background", "FAIL", f"{type(exc).__name__}: {exc}")
        finally:
            failing.close(wait=True)


def run_s1_s6(report: Report) -> None:
    key, _source = load_api_key()
    if not key:
        report.add("S1→S6", "validate_reconnect_v174_real.py", "NON_TESTABLE", "aucune clé API disponible")
        return
    if os.name != "nt":
        report.add("S1→S6", "validate_reconnect_v174_real.py", "NON_TESTABLE", "ce harnais audio doit être exécuté sur Windows")
        return
    script = ROOT / "scripts" / "validate_reconnect_v174_real.py"
    safe_print("\nLancement du harnais réel S1→S6. Aucun résultat simulé.")
    result = subprocess.run([sys.executable, str(script)], cwd=ROOT, check=False)
    status = "PASS" if result.returncode == 0 else "FAIL"
    report.add("S1→S6", "validate_reconnect_v174_real.py", status, f"code retour={result.returncode}; voir les verdicts détaillés affichés ci-dessus")


MANUAL_PROTOCOL = """
VALIDATION MANUELLE CRITIQUE (Windows, micro et haut-parleurs réels)

1. Lance Jarvis normalement : py -3 run_jarvis.py --ui
2. Ouvre « Tâches d'arrière-plan… » depuis le tray.
3. Demande vocalement une recherche poussée sur les moteurs ioniques.
4. Chronomètre la confirmation : elle doit être immédiate et la tâche QUEUED/RUNNING.
5. Pendant RUNNING, pose au moins trois questions normales à Gemini 2.5 Native Audio.
6. Note séparément : micro, transcription, réponse audio, continuité du contexte.
7. Lance en parallèle une tâche simple; vérifie deux IDs et deux progressions.
8. Vérifie compteurs actif/non-lu, statut, modèle, étape et temps écoulé.
9. Laisse finir une tâche pendant une réponse vocale : notification visible, aucun bip,
   aucune coupure audio, aucune prise du micro.
10. Ouvre le résultat : détail/fichiers visibles, NEW disparaît, historique conservé.
11. Lance puis annule une tâche active : statut CANCELLED sans exception UI.
12. Ferme/réouvre Blob/Desktop : les tâches doivent rester disponibles.

Ne marque PASS que sur observation humaine réelle. Conserve le JSON API et les
sorties S1→S6 avec le compte rendu manuel; ils ne contiennent aucune clé.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--preflight", action="store_true", help="dépendances/matériel/config, sans appel API")
    group.add_argument("--api", action="store_true", help="router, tâches, grounding, concurrence et erreur sur API réelle")
    group.add_argument("--s1-s6", action="store_true", help="lance le harnais audio/reconnexion réel existant")
    group.add_argument("--manual-protocol", action="store_true", help="affiche le protocole UI/audio critique")
    parser.add_argument("--report", type=Path, help="rapport JSON; aucun secret n'y est écrit")
    args = parser.parse_args()

    if args.manual_protocol:
        safe_print(MANUAL_PROTOCOL)
        return 0

    report = Report()
    if args.preflight:
        preflight(report)
    elif args.api:
        preflight(report)
        asyncio.run(run_api_validation(report))
    elif args.s1_s6:
        run_s1_s6(report)

    destination = args.report or paths.logs_dir() / f"validation_v180_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report.write(destination)
    if any(item.status == "NON_TESTABLE" for item in report.checks):
        return 2
    return 1 if report.failed() else 0


if __name__ == "__main__":
    raise SystemExit(main())
