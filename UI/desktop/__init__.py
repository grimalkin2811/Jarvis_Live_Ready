"""Desktop Mode de Jarvis (v1.7.0) — présence d'écran non intrusive.

Le paquet est découpé en quatre couches strictement séparées :

``state``    machine d'états visuelle **sans Qt** : elle traduit les évènements
             réels du backend (hotword, transcription, outil, TTS…) en états
             visuels (``LISTENING``, ``THINKING``, ``TOOL_USE``…). Importable
             depuis n'importe quel thread.
``config``   ``DesktopAppearanceConfig`` : ce que l'utilisateur voit, où, et
             dans quels états. Pure donnée sérialisable, **sans Qt**.
``design``   jeton de design (durées, easing, opacités, épaisseurs, profils de
             halo). Une seule source pour tous les nombres de l'interface.
``halo`` / ``widgets`` / ``overlay``
             la présentation Qt : rendu du halo (pixmaps mis en cache),
             widgets du HUD, et la fenêtre overlay + son contrôleur.

Les imports Qt sont **paresseux** (PEP 562) : le backend vocal peut importer
``UI.desktop.state`` ou ``UI.desktop.config`` sans payer PySide6.
"""

from __future__ import annotations

__all__ = [
    "DesktopAppearanceConfig",
    "DesktopOverlay",
    "DesktopOverlayController",
    "DesktopState",
    "DesktopStateMachine",
    "DESKTOP_EVENTS",
]

_LAZY = {
    "DesktopOverlay": ("UI.desktop.overlay", "DesktopOverlay"),
    "DesktopOverlayController": ("UI.desktop.overlay", "DesktopOverlayController"),
    "DesktopAppearanceConfig": ("UI.desktop.config", "DesktopAppearanceConfig"),
    "DesktopState": ("UI.desktop.state", "DesktopState"),
    "DesktopStateMachine": ("UI.desktop.state", "DesktopStateMachine"),
    "DESKTOP_EVENTS": ("UI.desktop.events", "DESKTOP_EVENTS"),
}


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    value = getattr(importlib.import_module(target[0]), target[1])
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(globals().keys()) + __all__)
