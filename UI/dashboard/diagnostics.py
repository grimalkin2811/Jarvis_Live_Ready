"""Diagnostics du Dashboard — sans Qt.

Construit des **vérifications de composants** fondées sur des preuves
observables (tableau d'état runtime, base mémoire, Task Manager, fichiers de
journal, préférences) et un **rapport texte** copiable. Aucune valeur n'est
supposée : une information indisponible est affichée comme telle.

Confidentialité : le rapport ne contient jamais la clé API (seulement
« configurée : oui/non »), les chemins du profil sont abrégés en ``~`` et
tout texte d'erreur passe par ``redact_secrets``.
"""

from __future__ import annotations

import os
import platform
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from src.runtime_status import (
    ENGINE_LABELS,
    HEALTH_LABELS,
    EngineState,
    redact_secrets,
)

STATUS_OK = "ok"
STATUS_WARNING = "warning"
STATUS_ERROR = "error"
STATUS_UNKNOWN = "unknown"

STATUS_LABELS = {
    STATUS_OK: "OK",
    STATUS_WARNING: "Attention",
    STATUS_ERROR: "Erreur",
    STATUS_UNKNOWN: "Inconnu",
}


@dataclass
class ComponentCheck:
    id: str
    label: str
    status: str
    detail: str
    evidence: list[str] = field(default_factory=list)


def shorten_path(value: Any) -> str:
    text = str(value or "")
    home = os.path.expanduser("~")
    if home and home not in (os.sep, "~") and text.startswith(home):
        return "~" + text[len(home):]
    return text


def _fmt_time(value: Any) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(float(value)))
    except (TypeError, ValueError):
        return "—"


def engine_check(snapshot: dict[str, Any]) -> ComponentCheck:
    state = snapshot.get("engine_state", EngineState.NOT_STARTED)
    label = ENGINE_LABELS.get(state, state)
    evidence = [f"État publié par la boucle vocale : {label} (depuis {_fmt_time(snapshot.get('engine_since'))})"]
    if snapshot.get("model"):
        evidence.append(f"Modèle : {snapshot['model']}")
    if snapshot.get("session_generation"):
        evidence.append(f"Session n° {snapshot['session_generation']}")
    if snapshot.get("reconnect_attempts"):
        evidence.append(f"Tentatives de reconnexion : {snapshot['reconnect_attempts']}")
    if snapshot.get("last_error"):
        evidence.append(f"Dernière erreur ({_fmt_time(snapshot.get('last_error_at'))}) : "
                        f"{redact_secrets(snapshot['last_error'], limit=200)}")
    if state == EngineState.READY:
        status, detail = STATUS_OK, "Session Live ouverte et prête."
    elif state in (EngineState.STARTING, EngineState.CONNECTING):
        status, detail = STATUS_UNKNOWN, "Connexion en cours : pas encore de preuve de disponibilité."
    elif state == EngineState.NOT_STARTED:
        status, detail = STATUS_UNKNOWN, "Aucun état publié (backend vocal non démarré)."
    elif state == EngineState.RECONNECTING:
        status, detail = STATUS_WARNING, snapshot.get("engine_detail") or "Reconnexion en cours."
    else:
        status, detail = STATUS_ERROR, snapshot.get("engine_detail") or label
    return ComponentCheck("engine", "Moteur conversationnel (Gemini Live)", status, detail, evidence)


def audio_check(snapshot: dict[str, Any], mic_enabled: bool | None) -> ComponentCheck:
    audio = snapshot.get("audio_state", "unknown")
    evidence = []
    if snapshot.get("audio_detail"):
        evidence.append(snapshot["audio_detail"])
    if mic_enabled is not None:
        evidence.append("Micro : " + ("actif" if mic_enabled else "coupé par l'utilisateur"))
    if audio == "ok":
        return ComponentCheck("audio", "Pipeline audio", STATUS_OK, "Flux micro et haut-parleurs démarrés.", evidence)
    if audio == "failed":
        return ComponentCheck("audio", "Pipeline audio", STATUS_ERROR,
                              snapshot.get("audio_detail") or "Démarrage audio en échec.", evidence)
    return ComponentCheck("audio", "Pipeline audio", STATUS_UNKNOWN, "Aucune preuve de démarrage audio.", evidence)


def wake_word_check(snapshot: dict[str, Any]) -> ComponentCheck:
    state = snapshot.get("wake_word", "unknown")
    detail = snapshot.get("wake_word_detail") or ""
    if state == "loaded":
        return ComponentCheck("wake_word", "Mot d'éveil « Hey Jarvis »", STATUS_OK,
                              f"Modèle chargé{f' ({detail})' if detail else ''}.")
    if state == "unavailable":
        return ComponentCheck("wake_word", "Mot d'éveil « Hey Jarvis »", STATUS_WARNING,
                              "Modèle indisponible : réveil par écoute continue ou manuel uniquement.")
    return ComponentCheck("wake_word", "Mot d'éveil « Hey Jarvis »", STATUS_UNKNOWN, "État non publié.")


def memory_check(stats: dict[str, Any] | None, status: dict[str, Any] | None) -> ComponentCheck:
    status = status or {}
    if not status.get("enabled", True):
        return ComponentCheck("memory", "Mémoire persistante", STATUS_WARNING, "Désactivée par l'utilisateur.")
    if not stats or not stats.get("success"):
        error = (stats or {}).get("error") or status.get("error") or "indisponible"
        return ComponentCheck("memory", "Mémoire persistante", STATUS_ERROR, redact_secrets(error, limit=200))
    evidence = [
        f"Souvenirs actifs : {stats.get('total')} (archivés : {stats.get('archived')})",
        f"Schéma : v{stats.get('schema_version')}",
        f"Versions historisées : {stats.get('revisions') if stats.get('revisions') is not None else 'n/d'}",
        f"Fichier : {shorten_path(stats.get('path'))}",
    ]
    return ComponentCheck("memory", "Mémoire persistante", STATUS_OK, "Base SQLite accessible.", evidence)


def tasks_check(available: bool, reason: str, counts: dict[str, int] | None) -> ComponentCheck:
    if not available:
        return ComponentCheck("tasks", "Tâches d'arrière-plan", STATUS_WARNING if reason else STATUS_UNKNOWN,
                              reason or "Gestionnaire non initialisé.")
    counts = counts or {}
    evidence = [f"Actives : {counts.get('active', 0)} — terminées non vues : {counts.get('unread', 0)}",
                f"Échouées : {counts.get('FAILED', 0)} — annulées : {counts.get('CANCELLED', 0)}"]
    return ComponentCheck("tasks", "Tâches d'arrière-plan", STATUS_OK, "Task Manager actif.", evidence)


def config_check(load_status: str, notes: list[str], path: str, last_save_ok: bool | None) -> ComponentCheck:
    evidence = [f"Fichier : {shorten_path(path)}"] + [str(n) for n in notes[:6]]
    if last_save_ok is False:
        evidence.append("Dernière sauvegarde : ÉCHEC (préférences gardées en mémoire)")
    if load_status in ("defaults", "loaded"):
        status, detail = STATUS_OK, "Préférences chargées." if load_status == "loaded" else "Valeurs par défaut (premier lancement)."
    elif load_status in ("repaired", "newer"):
        status, detail = STATUS_WARNING, "Préférences chargées avec corrections." if load_status == "repaired" else "Préférences d'une version plus récente."
    else:
        status, detail = STATUS_ERROR, "Fichier illisible : valeurs par défaut (copie de sauvegarde conservée)."
    if last_save_ok is False and status == STATUS_OK:
        status = STATUS_WARNING
    return ComponentCheck("config", "Préférences du Dashboard", status, detail, evidence)


def versions() -> dict[str, str]:
    info = {"python": platform.python_version(), "platform": f"{platform.system()} {platform.release()}"}
    try:
        from src.version import __version__

        info["jarvis"] = __version__
    except Exception:
        info["jarvis"] = "inconnue"
    try:
        import PySide6
        from PySide6 import QtCore

        info["pyside6"] = PySide6.__version__
        info["qt"] = QtCore.qVersion()
    except Exception:
        info["pyside6"] = "indisponible"
    try:
        # Binaire construit avant la borne PySide6<6.12 (ex. Qt 6.12.0 embarqué) :
        # signalé, pas bloquant (PR #50, src/qt_versions.py).
        from src import qt_versions

        info["pyside6_validation"] = qt_versions.describe()
    except Exception:
        info["pyside6_validation"] = "inconnue"
    try:
        from importlib.metadata import version as _v

        info["google-genai"] = _v("google-genai")
    except Exception:
        info["google-genai"] = "inconnue"
    try:
        import psutil

        info["psutil"] = psutil.__version__
    except Exception:
        info["psutil"] = "indisponible"
    return info


def build_report(checks: list[ComponentCheck], snapshot: dict[str, Any], extra: dict[str, Any] | None = None) -> str:
    """Rapport texte, sans secret, pour copie/sauvegarde par l'utilisateur."""
    lines = ["Rapport de diagnostic Jarvis — Dashboard", f"Généré le {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for key, value in versions().items():
        lines.append(f"{key}: {value}")
    lines.append(f"Santé globale : {HEALTH_LABELS.get(snapshot.get('health', 'unknown'), 'Inconnu')}")
    lines.append("")
    for check in checks:
        lines.append(f"[{STATUS_LABELS.get(check.status, check.status)}] {check.label} — {check.detail}")
        for item in check.evidence:
            lines.append(f"    · {item}")
    for key, value in (extra or {}).items():
        lines.append(f"{key}: {value}")
    try:
        key_configured = bool(os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"))
    except Exception:
        key_configured = False
    lines.append(f"Clé API dans l'environnement : {'oui' if key_configured else 'non (ou fichier de configuration)'}")
    lines.append(f"Exécutable : {shorten_path(sys.executable)}")
    return redact_secrets("\n".join(lines), limit=20000)


def collect_checks(
    snapshot: dict[str, Any],
    *,
    memory_stats: Callable[[], dict[str, Any] | None] | None = None,
    memory_status: Callable[[], dict[str, Any] | None] | None = None,
    tasks: tuple[bool, str, dict[str, int] | None] | None = None,
    config: tuple[str, list[str], str, bool | None] | None = None,
    mic_enabled: bool | None = None,
) -> list[ComponentCheck]:
    checks = [engine_check(snapshot), audio_check(snapshot, mic_enabled), wake_word_check(snapshot)]
    try:
        stats = memory_stats() if memory_stats else None
    except Exception as exc:
        stats = {"success": False, "error": str(exc)}
    try:
        status = memory_status() if memory_status else None
    except Exception as exc:
        status = {"error": str(exc)}
    checks.append(memory_check(stats, status))
    if tasks is not None:
        checks.append(tasks_check(*tasks))
    if config is not None:
        checks.append(config_check(*config))
    return checks
