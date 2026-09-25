"""Pont thread-safe des commandes explicites d'affichage (Blob / menu).

C'est le mécanisme central des commandes « affiche le blob », « masque le
blob », « affiche le menu », « masque le menu » (voir ``show_blob`` /
``hide_blob`` / ``show_menu`` / ``hide_menu`` dans ``src/tools.py``).

Principes :

* Le backend vocal (thread asyncio) **écrit** des intentions dans ce pont,
  sans jamais toucher à Qt.
* L'interface (thread Qt, orbe ``UI/jarvis_menu.py``) **consomme** les
  intentions une par frame et **rapporte** l'état réel (visible / menu
  ouvert) pour ``get_ui_state``.
* Une commande explicite est une **action**, pas un filtre : elle doit
  produire l'affichage/masquage quel que soit le mode actif (jeu, focus…)
  ou l'état courant — c'est le widget qui arbitre avec sa politique de
  mode, jamais le backend.

Le module reste sans dépendance Qt : il est importable depuis les threads
du backend, même quand l'interface n'est pas lancée (mode console).
"""

from __future__ import annotations

import re
import threading
import unicodedata
from typing import Any

#: Noms des 5 menus du Blob (identiques à ``UI/jarvis_menu.MENU_SPECS``).
MENU_NAMES = ("Voice", "System", "Memory", "Appearance", "Routines")

#: Alias acceptés (voix française / anglaise) → nom du menu.
MENU_ALIASES: dict[str, str] = {
    "voice": "Voice",
    "voix": "Voice",
    "system": "System",
    "systeme": "System",
    "syteme": "System",
    "systeme jarvis": "System",
    "memory": "Memory",
    "memoire": "Memory",
    "appearance": "Appearance",
    "apparence": "Appearance",
    "look": "Appearance",
    "routines": "Routines",
    "routine": "Routines",
    "macros": "Routines",
}


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or ""))
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    text = text.lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def resolve_menu_name(name: Any) -> str | None:
    """Résout « system », « système », « memory »… vers l'un des 5 menus.

    ``None`` (pas de précision) est retournée telle quelle : le widget
    choisit alors le menu par défaut. Retourne ``None`` aussi pour un nom
    inconnu — le caller distingue « non précisé » et « inconnu » en
    comparant l'entrée brute.
    """
    if name is None:
        return None
    key = _normalize(name)
    if not key:
        return None
    if key in MENU_ALIASES:
        return MENU_ALIASES[key]
    for menu in MENU_NAMES:
        if key == _normalize(menu):
            return menu
    return None


def valid_menu_names() -> list[str]:
    return list(MENU_NAMES)


class VisibilityBridge:
    """Intentions d'affichage explicitement demandées par l'utilisateur.

    Un seul « slot » par action : relancer « affiche le blob » remplace la
    demande précédente (pas d'accumulation, pas de liste de tâches qui
    pourrait s'emballer). Le widget consomme les intentions en une passe.
    """

    ACTIONS = ("show_blob", "hide_blob", "show_menu", "hide_menu")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pending: dict[str, dict] = {}
        #: Numéro de version incrémenté à chaque nouvelle intention : le
        #: widget peut détecter les changements sans garder d'état.
        self._version = 0
        #: État réel rapporté par l'interface (None tant qu'aucune UI).
        self._ui_state: dict = {
            "ui_attached": False,
            "blob_visible": False,
            "menu_open": False,
            "menu": None,
        }

    # ------------------------------------------------------------------
    # Écriture (backend / outils vocaux — thread-safe)
    # ------------------------------------------------------------------
    def _request(self, action: str, **extra) -> None:
        if action not in self.ACTIONS:
            raise ValueError(f"Action inconnue : {action}")
        with self._lock:
            self._pending[action] = {"action": action, **extra}
            self._version += 1

    def show_blob(self) -> None:
        """Afficher (à nouveau) le Blob, quel que soit l'état courant."""
        self._request("show_blob")

    def hide_blob(self) -> None:
        """Masquer le Blob (ferme aussi le menu ouvert : c'est son contenu)."""
        self._request("hide_blob")

    def show_menu(self, menu: str | None = None) -> None:
        """Afficher le menu radial demandé (ou le menu par défaut)."""
        self._request("show_menu", menu=menu)

    def hide_menu(self) -> None:
        """Fermer le menu radial ouvert."""
        self._request("hide_menu")

    # ------------------------------------------------------------------
    # Lecture (thread Qt — widget)
    # ------------------------------------------------------------------
    def consume_requests(self) -> list[dict]:
        """Récupère et efface les intentions en attente (ordre stable)."""
        with self._lock:
            pending = [self._pending[name] for name in self.ACTIONS if name in self._pending]
            self._pending.clear()
            return pending

    def report_state(
        self,
        *,
        ui_attached: bool,
        blob_visible: bool,
        menu_open: bool,
        menu: str | None,
    ) -> None:
        """Le widget publie l'état réel (pour ``get_ui_state``)."""
        with self._lock:
            self._ui_state = {
                "ui_attached": bool(ui_attached),
                "blob_visible": bool(blob_visible),
                "menu_open": bool(menu_open),
                "menu": menu,
            }

    def ui_state(self) -> dict:
        with self._lock:
            return dict(self._ui_state)

    def version(self) -> int:
        with self._lock:
            return self._version

    def reset(self) -> None:
        """Purge (tests / redémarrage) : aucune intention, aucune UI."""
        with self._lock:
            self._pending.clear()
            self._version = 0
            self._ui_state = {
                "ui_attached": False,
                "blob_visible": False,
                "menu_open": False,
                "menu": None,
            }


#: Instance unique partagée entre le backend vocal et l'interface.
VISIBILITY = VisibilityBridge()
