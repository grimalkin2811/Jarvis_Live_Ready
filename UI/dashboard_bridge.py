"""Pont thread-safe des commandes Dashboard (voix, raccourcis) — sans Qt.

Même contrat que ``UI.interface_mode_bridge`` et ``UI.visibility_bridge`` :

* importable depuis n'importe quel thread (outils Gemini Live, routines) ;
* au plus un consommateur attaché (« le dernier attaché gagne ») : le
  ``DashboardController`` Qt, qui convertit la demande en signal Qt et
  l'exécute dans le thread graphique ;
* aucune exception ne remonte vers l'appelant.

Le pont ne garde aucune copie d'état métier : il transmet des intentions et
expose le dernier état d'affichage RAPPORTÉ par l'interface (pour que l'outil
``get_ui_state``/``open_dashboard`` reste honnête).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

#: Pages connues du Dashboard (identifiants stables). Les pages
#: personnalisées sont résolues par l'interface elle-même.
BUILTIN_PAGES = ("accueil", "travail", "taches", "memoire", "systeme", "parametres")

#: Alias vocaux → identifiant de page.
PAGE_ALIASES: dict[str, str] = {
    "accueil": "accueil",
    "home": "accueil",
    "travail": "travail",
    "work": "travail",
    "taches": "taches",
    "tâches": "taches",
    "tasks": "taches",
    "memoire": "memoire",
    "mémoire": "memoire",
    "memory": "memoire",
    "souvenirs": "memoire",
    "systeme": "systeme",
    "système": "systeme",
    "system": "systeme",
    "diagnostic": "systeme",
    "diagnostics": "systeme",
    "parametres": "parametres",
    "paramètres": "parametres",
    "reglages": "parametres",
    "réglages": "parametres",
    "settings": "parametres",
}


def resolve_page(name: Any) -> str | None:
    """Résout un nom de page (alias FR/EN) ; ``None`` si vide ou inconnu."""
    key = str(name or "").strip().lower()
    if not key:
        return None
    return PAGE_ALIASES.get(key)


Handler = Callable[[str, dict[str, Any]], None]


class DashboardBridge:
    """Point d'entrée unique des commandes Dashboard hors thread Qt."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handler: Handler | None = None
        self._state: dict[str, Any] = {"attached": False, "visible": False, "page": None}

    def set_handler(self, handler: Handler | None) -> None:
        with self._lock:
            self._handler = handler
            self._state["attached"] = handler is not None
            if handler is None:
                self._state["visible"] = False

    def attached(self) -> bool:
        with self._lock:
            return self._handler is not None

    def _send(self, command: str, payload: dict[str, Any]) -> bool:
        with self._lock:
            handler = self._handler
        if handler is None:
            return False
        try:
            handler(command, dict(payload))
        except Exception:
            return False
        return True

    def request_open(self, page: str | None = None) -> bool:
        """Demande l'ouverture (et éventuellement une page). False si non attaché."""
        return self._send("open", {"page": page})

    def request_close(self) -> bool:
        return self._send("close", {})

    def request_toggle(self) -> bool:
        return self._send("toggle", {})

    # -- état rapporté par l'interface ----------------------------------------

    def report_state(self, **state: Any) -> None:
        with self._lock:
            self._state.update(state)

    def state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)


#: Singleton partagé par le backend et l'interface.
DASHBOARD = DashboardBridge()
