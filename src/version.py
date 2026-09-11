"""Version de Jarvis — source unique de vérité.

Toutes les parties de l'application (Jarvis lui-même, le launcher, l'updater et
le build GitHub Actions) lisent cette valeur. Ne la dupliquez pas par ailleurs :
le fichier ``version.py`` à la racine du dépôt ne fait que re-porter cette valeur
pour les scripts de développement.
"""

from __future__ import annotations

__version__ = "1.1.1"

#: Canal de distribution. ``stable`` est utilisé par le launcher pour filtrer
#: les mises à jour ; ``beta``/``dev`` servent aux canaux de prépublication.
CHANNEL = "stable"

#: Nom de l'application, utilisé pour les chemins de données et l'affichage.
APP_NAME = "Jarvis"

#: Identifiant du dépôt GitHub utilisé pour les mises à jour (owner/repo).
#: Surchargeable par ``JARVIS_REPO`` (utile pour les forks / tests).
REPO_SLUG = "grimalkin2811/Jarvis_Live_Ready"


def get_version() -> str:
    """Retourne la version courante (chaîne ``MAJOR.MINOR.PATCH``)."""
    return __version__


def version_tuple(version: str | None = None) -> tuple[int, int, int]:
    """Convertit une version ``1.2.3`` en tuple ``(1, 2, 3)``.

    Les éléments non numériques sont ignorés (``1.2.3-beta`` -> ``(1, 2, 3)``).
    """
    text = (version or __version__).split("+")[0].split("-")[0]
    parts: list[int] = []
    for token in text.split("."):
        digits = "".join(ch for ch in token if ch.isdigit())
        if digits:
            parts.append(int(digits))
    while len(parts) < 3:
        parts.append(0)
    return (parts[0], parts[1], parts[2])


def compare_versions(a: str, b: str) -> int:
    """Compare deux versions sémantiques.

    Retourne -1 si ``a < b``, 0 si égales, 1 si ``a > b``.
    """
    ta = version_tuple(a)
    tb = version_tuple(b)
    if ta < tb:
        return -1
    if ta > tb:
        return 1
    return 0


def is_newer(candidate: str, current: str) -> bool:
    """Vrai si ``candidate`` est une version strictement plus récente."""
    return compare_versions(candidate, current) > 0
