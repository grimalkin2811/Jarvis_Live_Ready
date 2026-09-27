"""Package UI de Jarvis (orbe morphing, overlays, ponts)."""

from __future__ import annotations

__all__ = [
    "DesktopOverlay",
    "DesktopOverlayController",
    "ScreenHaloOverlay",
    "build_presence_hook",
]

#: Nom exporté -> (module, attribut). Tous ces symboles tirent PySide6 ; ils
#: restent donc paresseux (PEP 562).
_LAZY = {
    "ScreenHaloOverlay": (".screen_halo_overlay", "ScreenHaloOverlay"),
    "build_presence_hook": (".screen_halo_overlay", "build_presence_hook"),
    "DesktopOverlay": (".desktop.overlay", "DesktopOverlay"),
    "DesktopOverlayController": (".desktop.overlay", "DesktopOverlayController"),
}


def __getattr__(name: str):
    # Lazy (PEP 562) : l'overlay Desktop coûte ~20-25 ms à l'import et n'est
    # nécessaire qu'en mode desktop. Les sous-modules (appearance_actions,
    # menu_state, jarvis_menu…) restent importables normalement via
    # « from UI import … » — on lève AttributeError pour eux.
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module(target[0], __name__)
    value = getattr(module, target[1])
    globals()[name] = value  # cache pour les accès suivants
    return value


def __dir__():
    return sorted(list(globals().keys()) + __all__)
