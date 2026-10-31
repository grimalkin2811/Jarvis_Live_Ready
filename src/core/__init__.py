"""Interface-independent runtime for Jarvis.

Heavy audio, GUI and provider imports are deliberately deferred until startup.
"""

from .runtime import CoreRuntime, RuntimeHooks

__all__ = ["CoreRuntime", "RuntimeHooks"]
