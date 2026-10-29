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

# ---------------------------------------------------------------------------
# Données utilisateur RÉELLES : jamais touchées par la suite (audit PR #50)
# ---------------------------------------------------------------------------
# Constat (plugin d'audit, suite complète) : plusieurs tests créent l'orbe ou
# le menu, qui lisent/écrivent les chemins PAR DÉFAUT de Jarvis — dossier
# ``ui/`` (apparence, menu, système), ``memory.db``, ``schedule.db``,
# ``routines.json`` — soit ``%LOCALAPPDATA%\Jarvis`` sous Windows. Lancer
# ``pytest`` sur le PC de l'utilisateur écrasait ses réglages d'interface et,
# depuis le schéma mémoire v2, MIGRAIT sa vraie base mémoire. Toute la session
# pytest pointe donc vers un dossier temporaire, AVANT tout import de ``src``
# (les chemins par défaut de ``src.memory``/``src.scheduler`` sont figés à
# l'import). Un chemin réel fourni par l'environnement du développeur
# (``JARVIS_*_DATABASE_PATH``, ``JARVIS_USER_CONTENT_DIR``) est ignoré
# pendant les tests ; ceux qui ont besoin d'un dossier ``user_content`` le
# posent eux-mêmes (``isolated_jarvis``, ``patch.dict``).
import pytest  # noqa: E402

from tests._isolation import apply_test_isolation  # noqa: E402

# Même isolation que sous ``python -m unittest`` (tests/_isolation.py).
_ISOLATED_DATA_ENV = apply_test_isolation()


@pytest.fixture(autouse=True)
def _jarvis_data_stays_isolated():
    """Rétablit l'isolation si un test précédent a RETIRÉ la variable.

    Certains tests historiques font ``os.environ.pop("JARVIS_DATA_DIR")`` en
    nettoyage au lieu de restaurer la valeur précédente : sans ce filet, tous
    les tests suivants retomberaient sur le dossier réel. Une valeur posée par
    le test lui-même (son propre dossier temporaire) est respectée.
    """
    for key, value in _ISOLATED_DATA_ENV.items():
        if not os.environ.get(key, "").strip():
            os.environ[key] = value
    yield
