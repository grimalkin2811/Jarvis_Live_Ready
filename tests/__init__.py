"""Test helpers shared by the unittest suite.

IMPORTANT (régression corrigée) : ce module est le ``__init__.py`` du
paquet ``tests``. Il s'exécute dès qu'ON IMPORTE QUOI QUE CE SOIT depuis
``tests.*`` — y compris depuis un script de validation RÉELLE qui n'est pas
lancé par pytest (ex. ``scripts/validate_reconnect_v174_real.py`` fait
``from tests.real_gemini_harness import ...``). Les overrides
d'environnement *réservés aux tests au faux serveur* ne doivent donc JAMAIS
vivre ici : ils fuiteraient silencieusement dans les scripts de validation
réelle. Ils vivent dans ``tests/conftest.py``, qui n'est chargé que par
pytest (jamais par un `import` Python ordinaire du paquet ``tests``).
"""

import gc
import tempfile

_original_cleanup = tempfile.TemporaryDirectory.cleanup


def _cleanup_with_gc(self):
    """Release short-lived SQLite/file objects before Windows removes a temp dir."""
    gc.collect()
    return _original_cleanup(self)


tempfile.TemporaryDirectory.cleanup = _cleanup_with_gc
