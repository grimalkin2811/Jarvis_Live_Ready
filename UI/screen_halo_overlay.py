"""Overlay Desktop de Jarvis — façade de compatibilité (1.5.3 → 1.7.0).

Depuis la 1.7.0, l'implémentation vit dans le paquet ``UI.desktop`` :

* ``UI.desktop.state``   — machine d'états visuelle pilotée par les évènements
  réels du backend ;
* ``UI.desktop.halo``    — rendu du halo périphérique (pixmaps mis en cache,
  repaint limité aux bandes) ;
* ``UI.desktop.widgets`` — widgets optionnels du HUD ;
* ``UI.desktop.overlay`` — la fenêtre traversante et son contrôleur.

Ce module reste le point d'entrée historique : ``ScreenHaloOverlay`` et
``build_presence_hook`` gardent exactement le même contrat qu'en 1.6.0
(``show_listening`` / ``show_thinking`` / ``show_speaking`` / ``show_idle`` /
``hide_overlay`` / ``current_presence``), ce qui garantit qu'aucun appelant —
ni aucun test de non-régression — n'a besoin d'être réécrit.
"""

from __future__ import annotations

from .desktop.overlay import (  # noqa: F401
    DesktopControlsWindow,
    DesktopOverlay,
    DesktopOverlayController,
    PresenceRouter,
)
from .desktop.state import DesktopEvent, DesktopState  # noqa: F401

#: Nom historique de la fenêtre d'overlay.
ScreenHaloOverlay = DesktopOverlay

__all__ = [
    "DesktopControlsWindow",
    "DesktopEvent",
    "DesktopOverlay",
    "DesktopOverlayController",
    "DesktopState",
    "PresenceRouter",
    "ScreenHaloOverlay",
    "build_presence_hook",
]


def build_presence_hook(overlay: DesktopOverlay):
    """Retourne un callable ``presence_hook`` compatible avec ``AudioIO``.

    Conservé tel quel depuis la 1.5.3 : le backend continue d'émettre des
    chaînes (« listening », « thinking », « speaking », « hidden ») et l'overlay
    les traduit lui-même en états visuels.

    ⚠️ Comme en 1.6.0, le hook doit être appelé depuis le thread Qt (ou
    réacheminé par un signal) : ``InterfaceModeController`` s'en charge.
    """

    mapping = {
        "listening": overlay.show_listening,
        "thinking": overlay.show_thinking,
        "speaking": overlay.show_speaking,
        "idle": overlay.show_idle,
        "hidden": overlay.hide_overlay,
        "hide_overlay": overlay.hide_overlay,
        "error": overlay.show_error,
    }

    def _hook(state: str) -> None:
        action = mapping.get(str(state or "").strip().lower())
        if action is not None:
            action()

    return _hook
