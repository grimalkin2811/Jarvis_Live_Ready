"""Les Protocoles de Jarvis : des séquences cinématiques scriptées.

Un *protocole* est une suite de **beats** (temps forts) joués dans l'ordre :
chaque beat affiche une ligne de journal, peut interroger une sonde
télémétrique réelle (CPU, RAM, disque, batterie, réseau, mémoire, routines)
et peut déclencher un outil Jarvis déjà déclaré dans ``src.tools``.

Philosophie, identique au reste du projet :

* **Liste blanche** — un protocole ne peut appeler que des outils existants,
  et jamais un outil destructeur (``FORBIDDEN_TOOLS`` de ``src.routines``).
* **Aucun crash** — une sonde ou un outil qui échoue dégrade la ligne
  affichée, jamais la séquence.
* **Zéro dépendance UI** — ce module n'importe ni Qt ni sounddevice. Il
  émet des évènements ; l'interface (``UI.boot_sequence``) ou la console
  (``render_console``) les affichent. Il est donc testable tel quel.

Le protocole vedette est ``wake_up`` (« Jarvis, wake up ») : allumage complet
avec diagnostic réel de la machine, façon salle de contrôle.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import threading
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Callable

# ---------------------------------------------------------------------------
# Évènements émis pendant la lecture d'un protocole
# ---------------------------------------------------------------------------

#: Types d'évènements transmis au ``emit`` fourni par l'appelant.
EVENT_START = "start"        # {protocol, title, subtitle, accent, beats}
EVENT_BEAT = "beat"          # {index, label, detail, status, progress}
EVENT_LINE = "line"          # {text} — ligne de journal supplémentaire
EVENT_PROGRESS = "progress"  # {progress} 0.0 .. 1.0
EVENT_DONE = "done"          # {protocol, cancelled, closing, duration}


@dataclass(frozen=True)
class Beat:
    """Un temps fort du protocole.

    ``duration`` est le temps d'affichage minimal du beat : c'est lui qui
    donne le rythme « cinématique ». Il est ignoré en mode ``fast`` (tests).
    """

    label: str
    duration: float = 0.45
    probe: str = ""
    tool: str = ""
    args: dict = field(default_factory=dict)
    detail: str = ""


@dataclass(frozen=True)
class Protocol:
    protocol_id: str
    name: str
    title: str
    subtitle: str
    closing: str
    beats: tuple[Beat, ...]
    accent: tuple[int, int, int] = (80, 200, 255)
    aliases: tuple[str, ...] = ()
    cinematic: bool = True


# ---------------------------------------------------------------------------
# Sondes télémétriques — toujours sûres, toujours une chaîne courte
# ---------------------------------------------------------------------------

try:  # psutil est déjà une dépendance du projet, mais reste optionnel ici
    import psutil
except Exception:  # pragma: no cover - dépend de l'installation
    psutil = None


def _probe_core() -> str:
    system = f"{platform.system()} {platform.release()}".strip()
    host = socket.gethostname()
    return f"noyau {system or 'inconnu'} · hôte {host}"


def _probe_cpu() -> str:
    if psutil is None:
        return f"{os.cpu_count() or '?'} cœurs logiques"
    try:
        load = psutil.cpu_percent(interval=0.15)
        return f"{psutil.cpu_count(logical=True)} cœurs · charge {load:.0f} %"
    except Exception:
        return f"{os.cpu_count() or '?'} cœurs logiques"


def _probe_memory_ram() -> str:
    if psutil is None:
        return "mémoire vive non instrumentée"
    try:
        vm = psutil.virtual_memory()
        return (
            f"{vm.total / (1024 ** 3):.1f} Go · "
            f"{vm.percent:.0f} % occupés"
        )
    except Exception:
        return "mémoire vive non instrumentée"


def _probe_disk() -> str:
    try:
        usage = shutil.disk_usage(os.path.expanduser("~"))
        free = usage.free / (1024 ** 3)
        pct = (usage.free / usage.total * 100) if usage.total else 0
        return f"{free:.0f} Go libres · {pct:.0f} % disponibles"
    except Exception:
        return "volume principal illisible"


def _probe_power() -> str:
    if psutil is None:
        return "alimentation secteur présumée"
    try:
        battery = psutil.sensors_battery()
    except Exception:
        battery = None
    if battery is None:
        return "alimentation secteur"
    state = "sur secteur" if battery.power_plugged else "sur batterie"
    return f"{int(battery.percent)} % · {state}"


def _probe_network() -> str:
    start = time.time()
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2.5):
            pass
    except Exception:
        return "liaison distante indisponible · mode local"
    return f"liaison établie · {int((time.time() - start) * 1000)} ms"


def _probe_memory_bank() -> str:
    try:
        from .memory import get_default_memory_manager

        manager = get_default_memory_manager()
        if not getattr(manager, "enabled", False):
            return "banque mémoire désactivée"
        result = manager.list_memories(limit=500)
        count = len(result.get("souvenirs", result.get("memories", [])) or [])
        return f"{count} souvenir(s) indexé(s)"
    except Exception:
        return "banque mémoire hors ligne"


def _probe_routines() -> str:
    try:
        from .routines import get_default_routine_manager

        result = get_default_routine_manager().list_routines()
        routines = result.get("routines", []) or []
        active = sum(1 for item in routines if item.get("enabled"))
        return f"{active} routine(s) armée(s) sur {len(routines)}"
    except Exception:
        return "moteur de routines indisponible"


def _probe_reminders() -> str:
    try:
        from .scheduler import get_default_scheduler

        result = get_default_scheduler().list_reminders(limit=50)
        count = len(result.get("rappels", result.get("reminders", [])) or [])
        return f"{count} rappel(s) en attente"
    except Exception:
        return "planificateur au repos"


def _probe_audio() -> str:
    try:
        from . import tools as _tools

        result = _tools.get_volume()
        if result.get("success"):
            muet = result.get("muet")
            volume = result.get("volume")
            if volume is not None:
                return f"sortie à {volume} %" + (" · coupée" if muet else "")
    except Exception:
        pass
    return "chaîne audio nominale"


def _probe_operator() -> str:
    user = (
        os.environ.get("JARVIS_USER")
        or os.environ.get("USERNAME")
        or os.environ.get("USER")
        or ""
    ).strip()
    return f"opérateur reconnu : {user}" if user else "opérateur non identifié"


#: Sondes disponibles dans les beats (``probe=...``).
PROBES: dict[str, Callable[[], str]] = {
    "core": _probe_core,
    "cpu": _probe_cpu,
    "ram": _probe_memory_ram,
    "disk": _probe_disk,
    "power": _probe_power,
    "network": _probe_network,
    "memory_bank": _probe_memory_bank,
    "routines": _probe_routines,
    "reminders": _probe_reminders,
    "audio": _probe_audio,
    "operator": _probe_operator,
}


# ---------------------------------------------------------------------------
# Catalogue des protocoles
# ---------------------------------------------------------------------------

WAKE_UP = Protocol(
    protocol_id="wake_up",
    name="Réveil",
    title="JARVIS",
    subtitle="SÉQUENCE D'ALLUMAGE",
    closing="Tous les systèmes sont opérationnels.",
    accent=(90, 205, 255),
    aliases=("wake up", "wake-up", "reveil", "réveil", "demarrage", "démarrage",
             "boot", "allumage", "jarvis wake up"),
    beats=(
        Beat("Amorçage du noyau", 0.55, probe="core"),
        Beat("Identification de l'opérateur", 0.45, probe="operator"),
        Beat("Diagnostic processeur", 0.60, probe="cpu"),
        Beat("Cartographie de la mémoire vive", 0.45, probe="ram"),
        Beat("Analyse des volumes de stockage", 0.45, probe="disk"),
        Beat("Contrôle de l'alimentation", 0.40, probe="power"),
        Beat("Ouverture de la liaison distante", 0.70, probe="network"),
        Beat("Montage de la banque mémoire", 0.50, probe="memory_bank"),
        Beat("Armement des routines", 0.45, probe="routines"),
        Beat("Synchronisation du planificateur", 0.40, probe="reminders"),
        Beat("Calibration de la chaîne audio", 0.45, probe="audio"),
        Beat("Mise en ligne des systèmes", 0.80, detail="tous les modules répondent"),
    ),
)

STAND_DOWN = Protocol(
    protocol_id="stand_down",
    name="Repos",
    title="JARVIS",
    subtitle="MISE EN VEILLE",
    closing="Systèmes en veille. Je reste à l'écoute.",
    accent=(120, 140, 255),
    aliases=("stand down", "veille", "repos", "bonne nuit", "good night",
             "dors", "sleep"),
    beats=(
        Beat("Clôture des diagnostics", 0.45, probe="cpu"),
        Beat("Sauvegarde de la banque mémoire", 0.50, probe="memory_bank"),
        Beat("Routines maintenues en arrière-plan", 0.45, probe="routines"),
        Beat("Rappels conservés", 0.40, probe="reminders"),
        Beat("Atténuation de la chaîne audio", 0.55,
             tool="set_volume", args={"volume": 20}),
        Beat("Passage en veille attentive", 0.70, detail="micro toujours armé"),
    ),
)

DIAGNOSTIC = Protocol(
    protocol_id="diagnostic",
    name="Diagnostic",
    title="JARVIS",
    subtitle="ANALYSE COMPLÈTE",
    closing="Diagnostic terminé, aucune anomalie bloquante.",
    accent=(120, 255, 210),
    aliases=("diagnostic", "check", "bilan", "status", "etat systeme",
             "état système", "scan"),
    beats=(
        Beat("Relevé processeur", 0.45, probe="cpu"),
        Beat("Relevé mémoire vive", 0.40, probe="ram"),
        Beat("Relevé stockage", 0.40, probe="disk"),
        Beat("Relevé alimentation", 0.40, probe="power"),
        Beat("Test de liaison distante", 0.65, probe="network"),
        Beat("Intégrité de la banque mémoire", 0.45, probe="memory_bank"),
        Beat("Rapport consolidé", 0.55, detail="synthèse prête"),
    ),
)

FOCUS = Protocol(
    protocol_id="focus",
    name="Concentration",
    title="JARVIS",
    subtitle="PROTOCOLE CONCENTRATION",
    closing="Environnement de travail sécurisé. À vous de jouer.",
    accent=(255, 190, 110),
    aliases=("focus", "concentration", "travail", "deep work", "work mode"),
    beats=(
        Beat("Verrouillage de l'attention", 0.45, probe="operator"),
        Beat("Réduction du volume ambiant", 0.55,
             tool="set_volume", args={"volume": 25}),
        Beat("Contrôle des ressources", 0.45, probe="cpu"),
        Beat("Armement du minuteur de session", 0.55,
             tool="set_timer", args={"minutes": 25, "label": "session de concentration"}),
        Beat("Protocole actif", 0.70, detail="25 minutes de travail protégé"),
    ),
)

PROTOCOLS: tuple[Protocol, ...] = (WAKE_UP, DIAGNOSTIC, FOCUS, STAND_DOWN)


def _normalize(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or "").strip().lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.replace("_", " ").replace("-", " ").split())


def list_protocols() -> list[dict]:
    """Catalogue lisible (utilisé par l'outil vocal et l'UI)."""
    return [
        {
            "id": p.protocol_id,
            "nom": p.name,
            "sous_titre": p.subtitle,
            "etapes": len(p.beats),
            "duree_estimee_s": round(sum(b.duration for b in p.beats), 1),
            "alias": list(p.aliases),
        }
        for p in PROTOCOLS
    ]


def find_protocol(name) -> Protocol | None:
    """Retrouve un protocole par identifiant, nom ou alias (tolérant)."""
    needle = _normalize(name)
    if not needle:
        return None
    for protocol in PROTOCOLS:
        candidates = {_normalize(protocol.protocol_id), _normalize(protocol.name)}
        candidates.update(_normalize(alias) for alias in protocol.aliases)
        if needle in candidates:
            return protocol
    # Tolérance : « lance le protocole réveil maintenant »
    for protocol in PROTOCOLS:
        for alias in (protocol.protocol_id, protocol.name, *protocol.aliases):
            token = _normalize(alias)
            if token and token in needle:
                return protocol
    return None


# ---------------------------------------------------------------------------
# Exécution
# ---------------------------------------------------------------------------

#: Outils qu'un protocole ne peut jamais appeler (repris des routines).
def _forbidden_tools() -> set[str]:
    try:
        from .routines import FORBIDDEN_TOOLS

        return set(FORBIDDEN_TOOLS)
    except Exception:  # pragma: no cover - défensif
        return {"shutdown_pc", "restart_pc", "clear_memory", "delete_notes"}


def _tool_functions() -> dict:
    try:
        from .tools import TOOL_FUNCTIONS

        return TOOL_FUNCTIONS
    except Exception:  # pragma: no cover - défensif
        return {}


def _run_probe(name: str) -> str:
    probe = PROBES.get(name)
    if probe is None:
        return ""
    try:
        return str(probe() or "")
    except Exception as exc:  # pragma: no cover - défensif
        return f"sonde indisponible ({exc})"


def _run_tool(beat: Beat) -> tuple[str, bool]:
    name = beat.tool
    if name in _forbidden_tools():
        return "action refusée (outil protégé)", False
    fn = _tool_functions().get(name)
    if fn is None:
        return f"outil « {name} » indisponible", False
    try:
        result = fn(**dict(beat.args or {}))
    except Exception as exc:
        return f"échec : {exc}", False
    if isinstance(result, dict) and not result.get("success", True):
        return f"échec : {result.get('error', 'inconnu')}", False
    return "exécuté", True


class ProtocolRun:
    """Poignée d'un protocole en cours (annulable, joignable)."""

    def __init__(self, protocol: Protocol) -> None:
        self.protocol = protocol
        self.cancel_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.result: dict | None = None

    def cancel(self) -> None:
        self.cancel_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self.thread is not None:
            self.thread.join(timeout=timeout)

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()


_ACTIVE_LOCK = threading.RLock()
_ACTIVE_RUN: ProtocolRun | None = None
_LISTENERS: list[Callable[[dict], None]] = []


def add_listener(listener: Callable[[dict], None]) -> None:
    """Abonne un observateur global (l'UI) aux évènements de protocole."""
    with _ACTIVE_LOCK:
        if listener not in _LISTENERS:
            _LISTENERS.append(listener)


def remove_listener(listener: Callable[[dict], None]) -> None:
    with _ACTIVE_LOCK:
        if listener in _LISTENERS:
            _LISTENERS.remove(listener)


def has_listener() -> bool:
    with _ACTIVE_LOCK:
        return bool(_LISTENERS)


def _broadcast(event: dict) -> None:
    with _ACTIVE_LOCK:
        listeners = list(_LISTENERS)
    for listener in listeners:
        try:
            listener(dict(event))
        except Exception:
            pass


def play_protocol(
    protocol: Protocol,
    emit: Callable[[dict], None] | None = None,
    *,
    cancel_event: threading.Event | None = None,
    fast: bool = False,
    sleeper: Callable[[float], None] | None = None,
) -> dict:
    """Joue un protocole **de façon synchrone** et renvoie son résumé.

    ``emit`` reçoit chaque évènement en plus des observateurs globaux.
    ``fast=True`` supprime les temporisations (tests, exécution silencieuse).
    """

    cancel_event = cancel_event or threading.Event()
    started = time.monotonic()
    lines: list[str] = []

    def _emit(event: dict) -> None:
        if emit is not None:
            try:
                emit(dict(event))
            except Exception:
                pass
        _broadcast(event)

    def _pause(seconds: float) -> None:
        if fast or seconds <= 0:
            return
        if sleeper is not None:
            sleeper(seconds)
        else:
            cancel_event.wait(seconds)

    total = len(protocol.beats)
    _emit({
        "type": EVENT_START,
        "protocol": protocol.protocol_id,
        "name": protocol.name,
        "title": protocol.title,
        "subtitle": protocol.subtitle,
        "accent": protocol.accent,
        "beats": [beat.label for beat in protocol.beats],
    })

    cancelled = False
    for index, beat in enumerate(protocol.beats):
        if cancel_event.is_set():
            cancelled = True
            break

        detail = beat.detail
        status = "ok"
        if beat.probe:
            probed = _run_probe(beat.probe)
            detail = f"{detail} · {probed}" if detail and probed else (probed or detail)
        if beat.tool:
            message, ok = _run_tool(beat)
            detail = f"{detail} · {message}" if detail else message
            status = "ok" if ok else "warn"

        line = f"{beat.label} — {detail}" if detail else beat.label
        lines.append(line)
        _emit({
            "type": EVENT_BEAT,
            "index": index,
            "total": total,
            "label": beat.label,
            "detail": detail,
            "status": status,
            "progress": (index + 1) / total if total else 1.0,
        })
        _pause(beat.duration)

    if cancel_event.is_set():
        cancelled = True

    duration = round(time.monotonic() - started, 2)
    closing = "Protocole interrompu." if cancelled else protocol.closing
    summary = {
        "success": not cancelled,
        "protocole": protocol.protocol_id,
        "nom": protocol.name,
        "etapes": lines,
        "conclusion": closing,
        "duree_s": duration,
        "interrompu": cancelled,
    }
    _emit({
        "type": EVENT_DONE,
        "protocol": protocol.protocol_id,
        "cancelled": cancelled,
        "closing": closing,
        "duration": duration,
    })
    return summary


def start_protocol(
    name,
    emit: Callable[[dict], None] | None = None,
    *,
    fast: bool = False,
) -> ProtocolRun | None:
    """Lance un protocole dans un thread de fond.

    Un seul protocole peut tourner à la fois : relancer annule le précédent
    (c'est ce qui permet de « réveiller » Jarvis deux fois de suite sans
    empiler deux séquences).
    """

    protocol = name if isinstance(name, Protocol) else find_protocol(name)
    if protocol is None:
        return None

    global _ACTIVE_RUN
    with _ACTIVE_LOCK:
        previous = _ACTIVE_RUN
    if previous is not None and previous.running:
        previous.cancel()
        previous.join(timeout=1.5)

    run = ProtocolRun(protocol)

    def _target() -> None:
        run.result = play_protocol(
            protocol,
            emit,
            cancel_event=run.cancel_event,
            fast=fast,
        )

    run.thread = threading.Thread(
        target=_target,
        name=f"jarvis-protocol-{protocol.protocol_id}",
        daemon=True,
    )
    with _ACTIVE_LOCK:
        _ACTIVE_RUN = run
    run.thread.start()
    return run


def active_run() -> ProtocolRun | None:
    with _ACTIVE_LOCK:
        return _ACTIVE_RUN


def cancel_active() -> bool:
    run = active_run()
    if run is not None and run.running:
        run.cancel()
        return True
    return False


# ---------------------------------------------------------------------------
# Rendu console (mode headless) — le même spectacle, en ASCII
# ---------------------------------------------------------------------------

def render_console(width: int = 62) -> Callable[[dict], None]:
    """Fabrique un ``emit`` qui dessine le protocole dans le terminal."""

    state = {"total": 1}

    def _emit(event: dict) -> None:
        kind = event.get("type")
        if kind == EVENT_START:
            state["total"] = max(1, len(event.get("beats") or []))
            print()
            print("╔" + "═" * (width - 2) + "╗")
            title = f"{event.get('title', 'JARVIS')} · {event.get('subtitle', '')}".strip()
            print("║" + title.center(width - 2) + "║")
            print("╚" + "═" * (width - 2) + "╝")
        elif kind == EVENT_BEAT:
            progress = float(event.get("progress") or 0.0)
            filled = int(progress * 24)
            bar = "█" * filled + "░" * (24 - filled)
            mark = "✔" if event.get("status") == "ok" else "!"
            label = str(event.get("label", ""))
            detail = str(event.get("detail", ""))
            print(f" [{bar}] {mark} {label}")
            if detail:
                print(f"          ↳ {detail}")
        elif kind == EVENT_LINE:
            print(f"          · {event.get('text', '')}")
        elif kind == EVENT_DONE:
            print()
            print(" " + str(event.get("closing", "")).center(width - 2))
            print()

    return _emit
