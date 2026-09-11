"""Test helpers shared by the unittest suite."""

import gc
import os
import tempfile

# Les tests ne doivent JAMAIS télécharger de modèles depuis le réseau :
# toute résolution/téléchargement via src.wakeword est désactivée par défaut
# (les tests concernés forcent explicitement `download=True` avec des mocks).
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")


_original_cleanup = tempfile.TemporaryDirectory.cleanup


def _cleanup_with_gc(self):
    """Release short-lived SQLite/file objects before Windows removes a temp dir."""
    gc.collect()
    return _original_cleanup(self)


tempfile.TemporaryDirectory.cleanup = _cleanup_with_gc
