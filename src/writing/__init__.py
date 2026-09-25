"""Système d'écriture de Jarvis (champ actif et fichiers ``.txt``).

Deux modes indépendants, déclenchés seulement par une demande explicite :

* insertion au curseur dans le champ actif (simulation de saisie) ;
* création d'un fichier ``.txt`` dans ``user_content/``.

Le routage vocal reste celui de Gemini Live (outils). Ce paquet fournit la
détection d'intention, la préparation du texte, l'insertion, les fichiers et
les interrupteurs. Il ne vocalise jamais le texte produit.
"""

from .filenames import derive_filename, generate_unique_filename, sanitize_filename
from .intent import (
    BOTH,
    CONVERSATION,
    CREATE_TEXT_FILE,
    WRITE_ACTIVE_FIELD,
    WritingIntent,
    classify_writing_intent,
)
from .service import (
    SPOKEN_FILE_CREATED,
    SPOKEN_FILE_FAILED,
    SPOKEN_WRITE_FAILED,
    SPOKEN_WRITTEN,
    create_text_file,
    handle_command,
    system_instruction,
    write_to_active_field,
)

__all__ = [
    "BOTH",
    "CONVERSATION",
    "CREATE_TEXT_FILE",
    "SPOKEN_FILE_CREATED",
    "SPOKEN_FILE_FAILED",
    "SPOKEN_WRITE_FAILED",
    "SPOKEN_WRITTEN",
    "WRITE_ACTIVE_FIELD",
    "WritingIntent",
    "classify_writing_intent",
    "create_text_file",
    "derive_filename",
    "generate_unique_filename",
    "handle_command",
    "sanitize_filename",
    "system_instruction",
    "write_to_active_field",
]
