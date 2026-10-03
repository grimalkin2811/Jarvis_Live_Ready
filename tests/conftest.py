"""Pytest-only setup for the fake-server test suite.

These environment overrides are intentionally kept OUT of
``tests/__init__.py``: that module runs on every plain ``import tests.xxx``
(including from standalone scripts such as
``scripts/validate_reconnect_v174_real.py``, which runs against the REAL
Gemini Live API). ``conftest.py`` is different — it is only ever loaded by
pytest's collection machinery, never by a normal Python import of the
``tests`` package. Putting test-only timeouts/flags here guarantees they can
never silently leak into a real validation run again.

(Regression found & fixed during the v1.7.5 S6 real-Gemini investigation:
``JARVIS_LIVE_SEED_COMMIT_TIMEOUT=1.5`` used to live in ``tests/__init__.py``
and was overriding the production default of 5s in
``scripts/validate_reconnect_v174_real.py`` purely because that script does
``from tests.real_gemini_harness import ...``, which executes
``tests/__init__.py`` as a side effect of the package import.)
"""

import os

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
# seule fois à l'import du module) : pytest charge ce ``conftest.py`` avant
# de collecter/importer le moindre module de test.
os.environ.setdefault("JARVIS_LIVE_SEED_COMMIT_TIMEOUT", "1.5")
