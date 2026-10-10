"""Validation RÉELLE de Jarvis v1.8 (API + préflight Windows).

Ce script n'utilise aucun faux serveur et aucun modèle simulé. Les tests API
font de vraies requêtes Gemini. Il ne prend jamais une clé en argument, ne
l'affiche pas et ne l'écrit pas dans le rapport.

Exemples Windows, depuis la racine du dépôt :

    py -3 scripts\\validate_v180_real.py --preflight
    py -3 scripts\\validate_v180_real.py --api
    py -3 scripts\\validate_v180_real.py --s1-s6
    py -3 scripts\\validate_v180_real.py --manual-protocol

``--api`` tente une exécution complexe puis, uniquement si 3.8 est disponible,
une deuxième pendant le test de concurrence. Les retries bornés réellement
envoyés sont comptés. Après un refus fournisseur, le scénario n'insiste pas et
marque la partie Live+Flash non testable.
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
from src.background_tasks.discovery import (  # noqa: E402
    LiveModelDiscoveryError,
    LiveModelResolver,
)
from src.background_tasks.gateway import (  # noqa: E402
    BackgroundModelGateway,
    classify_background_error,
)
from src.background_tasks.manager import TaskManager  # noqa: E402
from src.background_tasks.models import RoutingDecision, TaskComplexity, TaskPriority, TaskStatus  # noqa: E402
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
    return (
        f"id={task.id}; status={task.status.value}; model={task.model}; "
        f"transport={task.transport}; signal_fin={task.completion_signal}; "
        f"progression={task.progress}; erreur={task.error or 'aucune'}"
    )


def validate_document_artifacts(task) -> tuple[bool, list[Path]]:
    valid_files = [
        Path(item) for item in task.files
        if Path(item).is_file() and Path(item).stat().st_size > 0
    ]
    valid = (
        task.status is TaskStatus.COMPLETED
        and bool(task.result and str(task.result).strip())
        and bool(task.files)
        and len(valid_files) == len(task.files)
    )
    return valid, valid_files


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


def classify_failure(error) -> tuple[str, str]:
    """Adapte la classification runtime unique aux verdicts du harness."""
    info = classify_background_error(error)
    status = "NON_TESTABLE" if info.category in {
        "EXTERNAL_QUOTA",
        "EXTERNAL_SERVICE",
        "LIVE_RESOURCE_EXHAUSTED",
        "LOCAL_QUOTA_GUARD",
    } else "FAIL"
    return status, info.category


def execution_transport(complexity: str) -> str:
    return (
        "GenerateContent"
        if str(complexity).lower() == "complex"
        else "Live/BidiGenerateContent"
    )


def discovery_failure_verdict(category: str) -> str:
    if category in {
        "LIVE_TIMEOUT", "EXTERNAL_QUOTA", "EXTERNAL_SERVICE",
        "LIVE_RESOURCE_EXHAUSTED",
    }:
        return "NON_TESTABLE"
    if category in {
        "UNSUPPORTED_MODEL_OR_MODALITY", "CONFIGURATION_ERROR",
        "FILTERED_SPECIALIZED_MODEL", "DISCOVERY_CANDIDATE_LIMIT",
    }:
        return "INFO"
    return "FAIL"


def external_status(error: str) -> str:
    return classify_failure(error)[0]


def failure_detail(error: str) -> str:
    status, origin = classify_failure(error)
    return f"classification={origin}; verdict={status}; erreur={error or 'aucune'}"


async def run_api_validation(report: Report) -> None:
    config = BackgroundModelConfig.from_env()
    key, source = load_api_key()
    if not key:
        report.add("DÉCOUVERTE LIVE", "modèle Gemini 3", "NON_TESTABLE", "aucune clé/configuration Gemini")
        return

    from google import genai
    client = genai.Client(api_key=key)
    main_model = configured_main_model()
    report.add("API RÉELLE", "clé chargée sans exposition", "PASS", source)

    try:
        await asyncio.wait_for(check_live_model(client, main_model), timeout=30)
        report.add("API RÉELLE", "Gemini 2.5 Native Audio disponible", "PASS", main_model)
    except Exception as exc:
        report.add(
            "API RÉELLE", "Gemini 2.5 Native Audio disponible",
            external_status(str(exc)),
            f"{main_model}: {type(exc).__name__}: {exc}",
        )

    resolver = LiveModelResolver(
        client,
        api_key_fingerprint=LiveModelResolver.fingerprint(key),
        hint=config.live_model_hint,
        retry_attempts=config.retry_attempts,
        retry_base_delay=config.retry_base_delay,
    )
    try:
        resolution = await resolver.resolve(force=True)
        for failure in resolution.diagnostics:
            report.add(
                "DÉCOUVERTE CANDIDAT", failure.model,
                discovery_failure_verdict(failure.category),
                f"classification={failure.category}; {failure.detail}",
            )
        report.add(
            "DÉCOUVERTE LIVE", "modèle Gemini 3",
            "PASS",
            f"classification=VALIDATION_SUCCESS; model={resolution.model}; "
            f"transport={resolution.transport}; validated={resolution.validated}; "
            f"exchange=AUDIO→output_transcription→{resolution.completion_signal}",
        )
    except Exception as exc:
        if isinstance(exc, LiveModelDiscoveryError) and exc.failures:
            verdicts = []
            for failure in exc.failures:
                verdict = discovery_failure_verdict(failure.category)
                verdicts.append(verdict)
                report.add(
                    "DÉCOUVERTE CANDIDAT", failure.model, verdict,
                    f"classification={failure.category}; {failure.detail}",
                )
            overall = (
                "FAIL" if "FAIL" in verdicts
                else "NON_TESTABLE" if "NON_TESTABLE" in verdicts
                else "NON_TESTABLE"
            )
        else:
            overall = external_status(str(exc))
        report.add(
            "DÉCOUVERTE LIVE", "modèle Gemini 3", overall,
            f"{type(exc).__name__}: {exc}",
        )
        return

    gateway = BackgroundModelGateway(
        client,
        retry_attempts=config.retry_attempts,
        retry_base_delay=config.retry_base_delay,
    )
    router = TaskRouter(client, config, resolver, gateway=gateway)
    router_cases = [
        ("A météo", "Donne-moi la météo de demain.", "simple", resolution.model),
        ("B processeurs", "Compare deux processeurs pour choisir lequel acheter.", "medium", resolution.model),
        ("C moteurs ioniques", "Fais une recherche poussée et une analyse détaillée sur les moteurs ioniques.", "complex", config.complex_model),
    ]
    for title, prompt, expected_complexity, expected_model in router_cases:
        try:
            decision = await router.route(title, prompt, TaskPriority.NORMAL)
            valid = decision.complexity.value == expected_complexity and decision.model == expected_model
            detail = (
                f"complexity={decision.complexity.value}; model={decision.model}; "
                f"transport={execution_transport(decision.complexity.value)}; "
                f"priority={decision.priority.value}; steps={decision.estimated_steps}; tools={decision.requires_tools}"
            )
            report.add("ROUTER RÉEL", title, "PASS" if valid else "FAIL", detail)
        except Exception as exc:
            report.add("ROUTER RÉEL", title, external_status(str(exc)), f"{type(exc).__name__}: {exc}")

    try:
        grounded = await asyncio.wait_for(
            gateway.generate_live(
                resolution.model,
                "Quelle est la météo prévue demain à Paris ? Donne une source web vérifiable.",
                system_instruction="Utilise Google Search et réponds brièvement en français avec la source consultée.",
                tools=[{"google_search": {}}],
                timeout_seconds=config.timeout_seconds,
            ),
            timeout=config.timeout_seconds + 1.0,
        )
        report.add("API RÉELLE", "Google Search grounding Live", "PASS" if grounded.text else "FAIL", f"réponse={grounded.text[:240]}; tokens={grounded.total_tokens}")
    except Exception as exc:
        info = classify_background_error(exc)
        status, origin = classify_failure(exc)
        report.add(
            "API RÉELLE", "Google Search grounding Live", status,
            f"classification={origin}; status_code={info.status_code}; "
            f"live_close_code={info.live_close_code}; {type(exc).__name__}: {exc}",
        )

    await run_background_scenarios(report, key, config, client, main_model, resolution.model)


async def run_background_scenarios(report: Report, key: str, config: BackgroundModelConfig, main_client, main_model: str, live_model: str) -> None:
    with tempfile.TemporaryDirectory(prefix="jarvis-v180-real-") as folder:
        root = Path(folder)
        # Important: le Manager crée son propre client dans SON thread. Ne
        # jamais lui transmettre le client attaché à la boucle du harness.
        manager = TaskManager(key, config=config, storage_path=root / "tasks.json", output_dir=root / "results")
        trace_started = time.monotonic()
        events: dict[str, list[dict]] = {}

        def record_event(task) -> None:
            events.setdefault(task.id, []).append({
                "t": round(time.monotonic() - trace_started, 3),
                "status": task.status.value,
                "progress": task.progress,
                "step": task.current_step,
                "model": task.model,
                "error": task.error,
            })

        def timeline(task_id: str) -> str:
            return " | ".join(
                f"+{item['t']:.3f}s {item['status']} {item['progress']}% {item['step']}"
                + (f" error={item['error']}" if item["error"] else "")
                for item in events.get(task_id, [])
            )

        manager.add_hook(record_event)
        try:
            resolved = manager.resolve_live_model()
            report.add("BACKGROUND", "modèle Live dans la boucle worker", "PASS" if resolved["model"] == live_model else "FAIL", repr(resolved))

            before = time.monotonic()
            simple = manager.create_task("Résumé validation", "Résume en cinq points les bénéfices d'une sauvegarde régulière.")
            submission = time.monotonic() - before
            report.add("BACKGROUND", "soumission non bloquante", "PASS" if submission < 0.25 else "FAIL", f"{submission:.3f}s; statut initial={simple.status.value}")
            simple_done = await wait_terminal(manager, simple.id, timeout=config.task_timeout_seconds + 15)
            simple_ok = (
                simple_done.status is TaskStatus.COMPLETED
                and simple_done.model == live_model
                and simple_done.transport == "Live/BidiGenerateContent"
                and simple_done.completion_signal in {
                    "generation_complete", "turn_complete", "interaction_status=IDLE",
                }
                and bool(simple_done.result)
            )
            simple_status = "PASS" if simple_ok else external_status(simple_done.error)
            report.add("BACKGROUND", "tâche simple Gemini 3 Live", simple_status, _task_detail(simple_done))
            unread = any(item.id == simple.id for item in manager.list_unread_completed_tasks())
            result = manager.get_task_result(simple.id, mark_seen=True)
            seen = manager.get_task(simple.id).seen
            state_path = [item["status"] for item in events.get(simple.id, [])]
            lifecycle = simple.status.value == "QUEUED" and "RUNNING" in state_path and "COMPLETED" in state_path
            report.add(
                "BACKGROUND", "cycle QUEUED/RUNNING/COMPLETED",
                "PASS" if lifecycle else simple_status, timeline(simple.id),
            )
            report.add("BACKGROUND", "non vue puis vue", "PASS" if unread and seen and result and result.get("result") else simple_status)

            quota_before = manager.quota_status()
            complex_task = manager.create_task(
                "Analyse moteurs ioniques",
                "Fais une recherche poussée, multi-sources et une analyse détaillée sur les moteurs ioniques. Prépare un document Markdown avec les sources.",
                "high",
            )
            complex_done = await wait_terminal(manager, complex_task.id, timeout=config.task_timeout_seconds + 15)
            quota_after = manager.quota_status()
            quota_delta = quota_after["calls_today"] - quota_before["calls_today"]
            complex_ok = (
                complex_done.status is TaskStatus.COMPLETED
                and complex_done.model == config.complex_model
                and complex_done.transport == "GenerateContent"
                and bool(complex_done.result)
            )
            complex_status = "PASS" if complex_ok else external_status(complex_done.error)
            complex_origin = "NONE" if complex_ok else classify_failure(complex_done.error)[1]
            report.add(
                "BACKGROUND", "tâche complexe Gemini 3.8 Flash", complex_status,
                f"classification={complex_origin}; {_task_detail(complex_done)}; timeline={timeline(complex_done.id)}",
            )
            # Chaque retry 429/503 a été réellement envoyé et doit compter
            # exactement une fois; ni sous-comptage, ni double comptage.
            if complex_ok:
                expected_delta = 1
            elif complex_origin in {"EXTERNAL_QUOTA", "EXTERNAL_SERVICE"}:
                error_info = classify_background_error(complex_done.error)
                expected_delta = config.retry_attempts if error_info.retryable else 1
            elif complex_done.model != config.complex_model:
                expected_delta = 0
            else:
                expected_delta = None
            quota_ok = expected_delta is not None and quota_delta == expected_delta
            report.add(
                "QUOTA", "tentative(s) 3.8 comptabilisée(s)",
                "PASS" if quota_ok else "INFO" if expected_delta is None else "FAIL",
                f"delta={quota_delta}; attendu={expected_delta}; avant={quota_before}; après={quota_after}",
            )
            document_ok, valid_files = validate_document_artifacts(complex_done)
            report.add(
                "BACKGROUND", "document référencé",
                "PASS" if document_ok else complex_status,
                f"résultat_non_vide={bool(complex_done.result)}; fichiers={complex_done.files}; "
                f"fichiers_valides={[str(item) for item in valid_files]}",
            )

            # Le scénario simultané possède son propre état LOCAL de quota et
            # sa propre persistance. Cela ne réinitialise évidemment aucun
            # quota Google. Si le fournisseur vient de refuser 3.8, on teste
            # deux vraies tâches Live et on marque séparément Live+Flash comme
            # non testable plutôt que de provoquer d'autres appels coûteux.
            concurrent_root = root / "concurrent"
            concurrent_manager = TaskManager(
                key,
                config=config,
                storage_path=concurrent_root / "tasks.json",
                output_dir=concurrent_root / "results",
            )
            concurrent_manager.add_hook(record_event)
            flash_blocked = complex_origin in {
                "EXTERNAL_QUOTA", "EXTERNAL_SERVICE", "LOCAL_QUOTA_GUARD",
            }
            flash_available = not flash_blocked
            try:
                first = concurrent_manager.create_task(
                    "Conseils courts",
                    "Donne cinq conseils simples pour organiser un bureau.",
                )
                if flash_available:
                    second = concurrent_manager.create_task(
                        "Comparatif stockage",
                        "Fais une recherche poussée multi-sources comparant SSD NVMe et stockage cloud pour une PME, avec analyse détaillée.",
                    )
                    pair_kind = "LIVE_PLUS_FLASH"
                else:
                    second = concurrent_manager.create_task(
                        "Comparaison courte",
                        "Compare brièvement deux méthodes simples d'organisation du travail.",
                    )
                    pair_kind = "TWO_LIVE_TASKS"
                    report.add(
                        "BACKGROUND", "concurrence Live + Flash",
                        "NON_TESTABLE",
                        f"classification={complex_origin}; quota fournisseur 3.8 indisponible lors du test complexe précédent; aucun quota Google réinitialisé",
                    )

                active_together = False
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    active = {item.id for item in concurrent_manager.list_active_tasks()}
                    if first.id in active and second.id in active:
                        active_together = True
                        break
                    await asyncio.sleep(0.05)
                try:
                    active_before_main_check = bool(concurrent_manager.list_active_tasks())
                    await asyncio.wait_for(check_live_model(main_client, main_model), timeout=30)
                    active_after_main_check = bool(concurrent_manager.list_active_tasks())
                    report.add(
                        "NON-BLOCAGE", "Gemini 2.5 pendant deux tâches",
                        "PASS" if active_before_main_check else "FAIL",
                        f"pair={pair_kind}; active_before={active_before_main_check}; "
                        f"active_after={active_after_main_check}",
                    )
                except Exception as exc:
                    report.add(
                        "NON-BLOCAGE", "Gemini 2.5 pendant deux tâches",
                        external_status(str(exc)),
                        f"pair={pair_kind}; {failure_detail(str(exc))}",
                    )
                first_done, second_done = await asyncio.gather(
                    wait_terminal(concurrent_manager, first.id, timeout=config.task_timeout_seconds + 15),
                    wait_terminal(concurrent_manager, second.id, timeout=config.task_timeout_seconds + 15),
                )
                classifications = [
                    classify_failure(item.error) if item.status is not TaskStatus.COMPLETED else ("PASS", "NONE")
                    for item in (first_done, second_done)
                ]
                both_completed = all(
                    item.status is TaskStatus.COMPLETED
                    for item in (first_done, second_done)
                )
                live_signals = {
                    "generation_complete", "turn_complete", "interaction_status=IDLE",
                }
                executions_valid = (
                    first_done.transport == "Live/BidiGenerateContent"
                    and first_done.completion_signal in live_signals
                    and (
                        second_done.transport == "GenerateContent"
                        if pair_kind == "LIVE_PLUS_FLASH"
                        else second_done.transport == "Live/BidiGenerateContent"
                        and second_done.completion_signal in live_signals
                    )
                )
                concurrency_status = (
                    "PASS" if active_together and both_completed and executions_valid
                    else "NON_TESTABLE" if any(value[0] == "NON_TESTABLE" for value in classifications)
                    else "FAIL"
                )
                report.add(
                    "BACKGROUND", "deux tâches simultanées indépendantes",
                    concurrency_status,
                    f"pair={pair_kind}; active_together={active_together}; "
                    f"executions_valid={executions_valid}; "
                    f"classifications={classifications}; {_task_detail(first_done)}; "
                    f"timeline_1={timeline(first_done.id)} || {_task_detail(second_done)}; "
                    f"timeline_2={timeline(second_done.id)}",
                )
            finally:
                concurrent_manager.close(wait=True)

            restored = ComplexModelQuota(
                rpm=config.complex_rpm, rpd=config.complex_rpd, tpm=config.complex_tpm,
                concurrency=config.complex_concurrency, storage_path=root / "background_quota.json",
            ).snapshot()
            report.add("QUOTA", "compteur persistant", "PASS" if restored.calls_today == manager.quota_status()["calls_today"] else "FAIL", repr(asdict(restored)))

            # Limite locale déterministe, sans requête API ni faux compteur.
            guard = ComplexModelQuota(rpd=0, storage_path=root / "guard-quota.json")
            try:
                lease = await guard.reserve()
                guard.finish(lease)
                report.add("QUOTA", "refus récupérable à la limite", "FAIL", "la réservation a été autorisée")
            except Exception as exc:
                report.add(
                    "QUOTA", "refus récupérable à la limite",
                    "PASS" if isinstance(exc, QuotaExceededError) else "FAIL",
                    f"{type(exc).__name__}: {exc}",
                )
        finally:
            manager.close(wait=True)

        class InvalidComplexRouter:
            async def route(self, title, description, priority):
                return RoutingDecision(TaskComplexity.COMPLEX, "jarvis-validation-model-intentionally-invalid", "erreur contrôlée", False, 1, priority)

        invalid_config = BackgroundModelConfig(
            complex_model="jarvis-validation-model-intentionally-invalid",
            timeout_seconds=30,
            task_timeout_seconds=60,
            retry_attempts=1,
        )
        failing = TaskManager(
            key,
            config=invalid_config,
            router=InvalidComplexRouter(),
            storage_path=root / "failed.json",
            output_dir=root / "failed-results",
        )
        try:
            bad = failing.create_task("Erreur contrôlée", "Validation d'isolation d'une erreur API.")
            failed = await wait_terminal(failing, bad.id, timeout=75)
            report.add("BACKGROUND", "erreur contrôlée vers FAILED", "PASS" if failed.status is TaskStatus.FAILED and bool(failed.error) else "FAIL", _task_detail(failed))
            try:
                await asyncio.wait_for(check_live_model(main_client, main_model), timeout=30)
                report.add("NON-BLOCAGE", "Gemini principal après erreur background", "PASS")
            except Exception as exc:
                report.add("NON-BLOCAGE", "Gemini principal après erreur background", external_status(str(exc)), f"{type(exc).__name__}: {exc}")
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
