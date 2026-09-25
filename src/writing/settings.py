"""Interrupteurs du système d'écriture.

Les deux réglages sont indépendants et persistés avec le menu radial
(``menu_state.json``), via le pont thread-safe ``UI.menu_state.LIVE``.
Ce module n'importe pas Qt : le menu est lu à la demande pour éviter tout
cycle d'import au démarrage.
"""

from __future__ import annotations

#: Valeurs utilisées si le pont UI n'est pas encore chargé.
DEFAULT_ACTIVE_FIELD = True
DEFAULT_TEXT_FILES = True

ACTIVE_FIELD_LABEL = "Active Field"
TEXT_FILES_LABEL = "Text Files"

#: Libellés demandés pour les réglages (flash / documentation).
ACTIVE_FIELD_TITLE = "Writing → Active field"
TEXT_FILES_TITLE = "Writing → Create text files"

ACTIVE_FIELD_DESCRIPTION = (
    "Allows Jarvis to insert generated text into the currently active text field."
)
TEXT_FILES_DESCRIPTION = (
    "Allows Jarvis to create generated .txt files in the user_content folder."
)


def _live():
    try:
        from UI.menu_state import LIVE
    except Exception:
        return None
    return LIVE


def active_field_enabled() -> bool:
    """Vrai si Jarvis a le droit d'insérer du texte dans le champ actif."""
    live = _live()
    if live is None:
        return DEFAULT_ACTIVE_FIELD
    try:
        return bool(live.get_writing_active_field())
    except Exception:
        return DEFAULT_ACTIVE_FIELD


def text_files_enabled() -> bool:
    """Vrai si Jarvis a le droit de créer des fichiers ``.txt``."""
    live = _live()
    if live is None:
        return DEFAULT_TEXT_FILES
    try:
        return bool(live.get_writing_text_files())
    except Exception:
        return DEFAULT_TEXT_FILES
