"""Test helpers shared by the unittest suite."""

import gc
import os
import tempfile

# Les tests ne doivent JAMAIS télécharger de modèles depuis le réseau :
# toute résolution/téléchargement via src.wakeword est désactivée par défaut
# (les tests concernés forcent explicitement `download=True` avec des mocks).
os.environ.setdefault("JARVIS_NO_MODEL_DOWNLOAD", "1")

# v1.7.5 : GeminiLive attend désormais la confirmation RÉELLE du serveur
# (son propre turn_complete) avant d'ouvrir le micro après un rejeu de
# contexte clôturé (cf. GeminiLive._await_seed_commit, SEED_COMMIT_TIMEOUT_
# SECONDS). Sur un modèle sans recap (historyConfig/3.x, commit silencieux),
# ce délai est intégralement consommé avant l'ouverture — en production
# c'est volontairement généreux (réseau/inférence réels), mais les tests au
# faux serveur n'ont besoin que d'une marge de sécurité courte (le faux
# serveur répond, quand il répond, de façon quasi instantanée). Doit être
# défini AVANT le premier import de ``src.gemini_live`` (constante lue une
# seule fois à l'import du module) : cf. ``tests/__init__.py`` chargé avant
# tout module de test individuel.
os.environ.setdefault("JARVIS_LIVE_SEED_COMMIT_TIMEOUT", "1.5")


_original_cleanup = tempfile.TemporaryDirectory.cleanup


def _cleanup_with_gc(self):
    """Release short-lived SQLite/file objects before Windows removes a temp dir."""
    gc.collect()
    return _original_cleanup(self)


tempfile.TemporaryDirectory.cleanup = _cleanup_with_gc
