"""Persistance JSON locale des fichiers d'état (menu, apparence, système, modes).

Quatre modules réimplémentaient la même mécanique : lire un petit fichier JSON
en tolérant son absence ou sa corruption (valeur par défaut silencieuse),
l'écrire en créant le dossier au besoin. Ce module centralise UNIQUEMENT cette
mécanique d'entrées/sorties ; chaque appelant conserve son schéma, sa
validation champ par champ et ses valeurs par défaut.

Écriture atomique : le contenu est écrit dans ``<chemin>.tmp`` puis renommé
(``os.replace``, atomique sur Windows comme sur POSIX). Une interruption en
cours d'écriture ne peut donc plus laisser un fichier d'état tronqué, ce que
les écritures directes précédentes permettaient.

Aucune exception n'est propagée : un état local illisible ou non inscriptible
ne doit jamais empêcher Jarvis de fonctionner (l'appelant garde ses valeurs en
mémoire). Les appelants qui DOIVENT échouer bruyamment (``src.settings`` pour
la configuration utilisateur) gardent volontairement leur propre code.
"""

from __future__ import annotations

import json
import os
from typing import Any


def load_dict(path: str | os.PathLike | None) -> dict[str, Any]:
    """Charge un objet JSON, ou ``{}`` si rien d'exploitable n'est lisible.

    Couvre : chemin vide, fichier absent, fichier illisible (``OSError``),
    contenu invalide ou encodage invalide (``ValueError``, dont
    ``json.JSONDecodeError`` et ``UnicodeDecodeError``), et racine JSON qui
    n'est pas un objet (liste, chaîne, nombre).
    """
    if not path:
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def save_dict(path: str | os.PathLike | None, payload: dict[str, Any]) -> bool:
    """Écrit ``payload`` en JSON (dossier créé au besoin), en remplacement atomique.

    Retourne ``True`` si le fichier a bien été écrit, ``False`` sinon (le
    détail est laissé au système : l'état reste valable en mémoire).
    """
    if not path:
        return False
    try:
        directory = os.path.dirname(str(path))
        if directory:
            os.makedirs(directory, exist_ok=True)
        temp = f"{path}.tmp"
        with open(temp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp, path)
    except (OSError, TypeError, ValueError):
        return False
    return True
