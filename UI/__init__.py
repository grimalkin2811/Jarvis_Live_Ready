"""Package UI de Jarvis (orbe morphing, overlays, ponts)."""

from __future__ import annotations

__all__ = ["ScreenHaloOverlay", "build_presence_hook"]


def __getattr__(name: str):
    # Lazy (PEP 562) : screen_halo_overlay coûte ~20-25 ms à l'import et
    # n'est nécessaire qu'en mode desktop. Les sous-modules (appearance_actions,
    # menu_state, jarvis_menu…) restent importables normalement via
    # « from UI import … » — on lève AttributeError pour eux.
    if name in __all__:
        from .screen_halo_overlay import ScreenHaloOverlay, build_presence_hook

        mapping = {
            "ScreenHaloOverlay": ScreenHaloOverlay,
            "build_presence_hook": build_presence_hook,
        }
        value = mapping[name]
        globals()[name] = value  # cache pour les accès suivants
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(list(globals().keys()) + __all__)
