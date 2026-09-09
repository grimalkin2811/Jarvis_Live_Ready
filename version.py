"""Version de Jarvis (commodité pour scripts de développement et CI).

La source unique de vérité est ``src/version.py``. Ce module ne fait que la
re-porter : il ne contient aucune valeur dupliquée.
"""

from src.version import (  # noqa: F401
    APP_NAME,
    CHANNEL,
    REPO_SLUG,
    __version__,
    compare_versions,
    get_version,
    is_newer,
    version_tuple,
)
