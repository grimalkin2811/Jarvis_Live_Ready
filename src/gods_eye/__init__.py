"""God's Eye View — globe OSINT Jarvis-natif (module optionnel).

Pipeline : PySide6 (``widget.py``) → ``controller.py`` → JSON (``protocol.py``)
→ module web Cesium (``web/gods_eye_view``). Les outils Gemini Live sont dans
``tools.py`` (branchement additif dans ``src/tools.py``).

Rien ici n'est importé tant que le module n'est pas utilisé — et Qt n'est
chargé que via ``widget.py`` (jamais depuis ``protocol`` / ``controller``).
"""

from . import protocol  # noqa: F401
from .controller import (
    GodsEyeController,
    get_default_controller,
    reset_default_controller,
)

__all__ = [
    "GodsEyeController",
    "get_default_controller",
    "protocol",
    "reset_default_controller",
]
__version__ = "0.1.0"
