"""Actions rapides du Dashboard — sans Qt.

Chaque action appelle un mécanisme EXISTANT de Jarvis (pont ``LIVE`` du
micro, contexte conversationnel, contrôleur d'interface, gestionnaire de
routines, protocoles, dossiers réels). Aucune commande système arbitraire.

* Une action indisponible le dit (``availability`` → raison affichée).
* Le résultat (``ActionResult``) est construit à partir de la réponse du
  service : jamais de succès annoncé avant confirmation.
* Le niveau de sécurité (confirmation) est décidé par ``security`` ; ce
  module ne fait qu'exécuter une action déjà confirmée.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActionResult:
    success: bool
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class QuickAction:
    action_id: str  # identifiant de politique de sécurité (ex. "mic.toggle")
    key: str  # identifiant unique d'instance (ex. "routine.run:Mode travail")
    label: str
    description: str
    icon: str
    run: Callable[[], ActionResult]
    availability: Callable[[], tuple[bool, str]] = lambda: (True, "")
    #: Exécuter hors du thread graphique (accès disque, routines…).
    blocking: bool = False
    group: str = "Jarvis"


def _ok(message: str, **data: Any) -> ActionResult:
    return ActionResult(True, message, data)


def _fail(message: str, **data: Any) -> ActionResult:
    return ActionResult(False, message, data)


class ActionHost:
    """Points d'accroche fournis par l'interface (injectables en test)."""

    def __init__(
        self,
        *,
        set_interface_mode: Callable[[str], str] | None = None,
        current_interface_mode: Callable[[], str] | None = None,
        open_path: Callable[[str], bool] | None = None,
    ) -> None:
        self.set_interface_mode = set_interface_mode
        self.current_interface_mode = current_interface_mode
        self.open_path = open_path


def _live():
    from UI import menu_state

    return menu_state.LIVE


def mic_toggle() -> ActionResult:
    live = _live()
    target = not live.get_mic_enabled()
    live.set_mic_enabled(target)
    actual = live.get_mic_enabled()
    if actual != target:
        return _fail("Le micro n'a pas changé d'état.")
    return _ok("Micro rétabli." if actual else "Micro coupé.", mic_enabled=actual)


def voice_stop() -> ActionResult:
    if _live().request_stop_speaking():
        return _ok("Réponse interrompue.")
    return _fail("Aucun moteur vocal branché : rien à interrompre.")


def conversation_new() -> ActionResult:
    from src.conversation import get_default_conversation_context

    info = get_default_conversation_context().start_new_conversation(reason="Dashboard")
    return _ok(
        "Nouvelle conversation démarrée (mémoire persistante conservée).",
        conversation_id=info.get("conversation_id"),
    )


def _folder_availability(getter: Callable[[], str]) -> Callable[[], tuple[bool, str]]:
    def _check() -> tuple[bool, str]:
        try:
            path = getter()
        except Exception as exc:
            return False, str(exc)
        if not path or not os.path.isdir(path):
            return False, "Dossier pas encore créé (aucun fichier produit)."
        return True, ""
    return _check


def _results_dir() -> str:
    from src import paths

    return str(paths.background_task_results_dir())


def _documents_dir() -> str:
    from src import paths

    return str(paths.user_content_dir())


def _logs_dir() -> str:
    from src import paths

    return str(paths.logs_dir())


def routine_names() -> list[str]:
    try:
        from src.routines import get_default_routine_manager

        result = get_default_routine_manager().list_routine_names()
    except Exception:
        return []
    if not result.get("success"):
        return []
    return [str(item["name"]) for item in result.get("routines", []) if item.get("enabled")][:8]


def run_routine(name: str) -> ActionResult:
    from src.routines import get_default_routine_manager

    result = get_default_routine_manager().run_routine(name)
    if result.get("success"):
        return _ok(f"Routine « {name} » exécutée.", result=result)
    return _fail(f"Routine « {name} » : {result.get('error') or 'échec partiel'}", result=result)


def build_actions(host: ActionHost) -> list[QuickAction]:
    """Catalogue des actions réellement disponibles dans ce Jarvis."""
    actions: list[QuickAction] = [
        QuickAction("mic.toggle", "mic.toggle", "Micro", "Couper ou rétablir le micro (effet immédiat).", "◉",
                    mic_toggle),
        QuickAction("voice.stop", "voice.stop", "Interrompre", "Couper la réponse vocale en cours.", "■",
                    voice_stop),
        QuickAction("conversation.new", "conversation.new", "Nouvelle conversation",
                    "Efface le contexte conversationnel (pas la mémoire).", "↺", conversation_new),
    ]

    def _mode_action(mode: str, label: str, icon: str) -> QuickAction:
        def _run() -> ActionResult:
            if host.set_interface_mode is None:
                return _fail("Contrôleur d'interface indisponible.")
            selected = host.set_interface_mode(mode)
            if selected == mode:
                return _ok(f"{label} activé (la voix et les tâches continuent).", mode=selected)
            return _fail(f"Le mode demandé n'a pas été appliqué ({selected}).")

        def _available() -> tuple[bool, str]:
            if host.set_interface_mode is None:
                return False, "Contrôleur d'interface indisponible."
            current = host.current_interface_mode() if host.current_interface_mode else None
            if current == mode:
                return False, "Déjà actif."
            return True, ""

        return QuickAction(f"interface.{mode}", f"interface.{mode}", label,
                           f"Bascule à chaud vers {label} (sans redémarrer le moteur).", icon, _run, _available,
                           group="Interfaces")

    actions.append(_mode_action("blob", "Mode orbe", "●"))
    actions.append(_mode_action("desktop", "Mode Desktop", "◫"))

    def _folder_action(action_id: str, label: str, getter: Callable[[], str]) -> QuickAction:
        def _run() -> ActionResult:
            path = getter()
            if host.open_path is None:
                return _fail("Ouverture de dossier indisponible.")
            if not os.path.isdir(path):
                return _fail("Dossier introuvable.")
            if host.open_path(path):
                return _ok(f"Dossier ouvert : {os.path.basename(path) or path}")
            return _fail("Le système n'a pas pu ouvrir le dossier.")

        return QuickAction(action_id, action_id, label, f"Ouvre le dossier réel ({label.lower()}).", "▣", _run,
                           _folder_availability(getter), group="Dossiers")

    actions.append(_folder_action("folder.results", "Résultats des tâches", _results_dir))
    actions.append(_folder_action("folder.documents", "Documents", _documents_dir))
    actions.append(_folder_action("folder.logs", "Journaux", _logs_dir))

    for name in routine_names():
        actions.append(
            QuickAction("routine.run", f"routine.run:{name}", name[:24], f"Lance la routine « {name} ».", "▷",
                        (lambda n=name: run_routine(n)), blocking=True, group="Routines")
        )
    try:
        from src import protocols

        for protocol in protocols.PROTOCOLS:
            def _run(pid=protocol.protocol_id, pname=protocol.name) -> ActionResult:
                from src import protocols as _p

                try:
                    from src.modes import get_default_mode_manager

                    if get_default_mode_manager().should_suppress_visuals():
                        return _fail("Mode jeu actif : affichage plein écran bloqué.")
                except Exception:
                    pass
                run = _p.start_protocol(pid)
                if run is None:
                    return _fail(f"Protocole « {pname} » introuvable.")
                return _ok(f"Protocole « {pname} » lancé.")

            actions.append(QuickAction("protocol.run", f"protocol.run:{protocol.protocol_id}", protocol.name[:24],
                                       "Séquence visuelle plein écran avec diagnostic réel.", "✦", _run,
                                       blocking=True, group="Protocoles"))
    except Exception:
        pass
    return actions
