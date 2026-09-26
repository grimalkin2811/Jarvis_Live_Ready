"""Noms de fichiers ``.txt`` sûrs pour Windows.

Le nom est court, lisible, sans caractères interdits, et n'écrase jamais un
fichier existant (``document.txt``, ``document_1.txt``, ``document_2.txt``).
Aucun chemin absolu n'est accepté : seul le nom de base est conservé.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

_FORBIDDEN = set('\\/:*?"<>|')
_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
}

#: Verbes de commande et mots vides retirés pour déduire un nom parlant.
_COMMAND_WORDS = {
    "ecris",
    "ecrit",
    "ecrire",
    "ecrivez",
    "ecrives",
    "redige",
    "rediger",
    "redigez",
    "compose",
    "composer",
    "composez",
    "cree",
    "creer",
    "creez",
    "crees",
    "fais",
    "fait",
    "faire",
    "faites",
    "genere",
    "generer",
    "generez",
    "tape",
    "taper",
    "insere",
    "inserer",
    "dicte",
    "dicter",
    "sauvegarde",
    "sauvegarder",
    "enregistre",
    "enregistrer",
    "prepare",
    "preparer",
}
_STOPWORDS = {
    "moi",
    "me",
    "un",
    "une",
    "le",
    "la",
    "les",
    "de",
    "du",
    "des",
    "d",
    "l",
    "mon",
    "ma",
    "mes",
    "ton",
    "ta",
    "tes",
    "son",
    "sa",
    "ses",
    "pour",
    "sur",
    "avec",
    "et",
    "a",
    "au",
    "aux",
    "en",
    "dans",
    "ce",
    "cet",
    "cette",
    "ces",
    "qui",
    "que",
    "quoi",
    "dont",
    "ou",
    "par",
    "ne",
    "pas",
    "plus",
    "stp",
    "sil",
    "te",
    "plait",
    "svp",
    "texte",
    "fichier",
    "document",
    "txt",
    "contenu",
    "suivant",
    "demande",
    "lui",
    "leur",
    "leurs",
    "nous",
    "vous",
    "toi",
    "ici",
    "y",
    "ca",
    "cela",
    "dun",
    "dune",
    "petit",
    "petite",
    "nouveau",
    "nouvelle",
    "simple",
    "the",
    "of",
}

_EXPLICIT_NAME = re.compile(
    r"(?:nomme le|appelle le|sous le nom(?: de)?|fichier nomme|nom du fichier)"
    r"\s+([a-z0-9][a-z0-9 _-]{0,48})"
)

MAX_STEM = 40
MAX_TOKENS = 5
MAX_COLLISIONS = 10_000


def fold(value: str) -> str:
    """Minuscules ASCII, apostrophes devenues des espaces."""
    text = str(value or "").lower().strip()
    text = text.replace("’", "'").replace("`", "'")
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.replace("'", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def explicit_filename_is_invalid(filename: str) -> bool:
    """Vrai si un nom fourni explicitement ne contient aucune lettre ni chiffre."""
    raw = str(filename or "").strip()
    if not raw:
        return False
    base = raw.replace("\\", "/").split("/")[-1]
    if base.lower().endswith(".txt"):
        base = base[:-4]
    elif "." in base:
        base = base.rsplit(".", 1)[0]
    kept = "".join(char for char in base if char.isalnum())
    return not kept


def sanitize_filename(filename: str) -> str:
    """Retourne un nom ``.txt`` compatible Windows, ou ``document.txt``."""
    raw = str(filename or "").strip()
    raw = raw.replace("\\", "/").split("/")[-1]
    raw = unicodedata.normalize("NFKD", raw)
    raw = "".join(char for char in raw if not unicodedata.combining(char))
    raw = raw.lower().strip()
    if raw.endswith(".txt"):
        raw = raw[:-4]
    elif "." in raw:
        raw = raw.rsplit(".", 1)[0]
    cleaned = []
    for char in raw:
        if char in _FORBIDDEN or ord(char) < 32:
            cleaned.append("_")
        elif char.isalnum() or char in (" ", "-", "_"):
            cleaned.append(char)
        else:
            cleaned.append("_")
    stem = "".join(cleaned).replace(" ", "_").replace("-", "_")
    stem = re.sub(r"_+", "_", stem).strip("._ ")
    stem = stem[:MAX_STEM].strip("_")
    if not stem or stem.upper() in _RESERVED:
        stem = "document" if not stem else f"fichier_{stem}"
    if stem.upper() in _RESERVED:
        stem = f"fichier_{stem}"
    return f"{stem}.txt"


def extract_explicit_name(request: str) -> str | None:
    """Nom demandé explicitement (« appelle-le brouillon », « nomme-le … »)."""
    folded = fold(request)
    # Le wake word en tête ne fait pas partie du nom.
    folded = re.sub(r"^(hey |ok )?jarvis ", "", folded)
    match = _EXPLICIT_NAME.search(folded)
    if not match:
        return None
    tokens = [token for token in re.split(r"[\s_-]+", match.group(1).strip()) if token]
    kept: list[str] = []
    for token in tokens:
        if token in _STOPWORDS or token in _COMMAND_WORDS:
            if kept:
                break
            continue
        kept.append(token)
        if len(kept) >= 4:
            break
    if not kept:
        return None
    return "_".join(kept)


def _tokens_from_phrase(phrase: str) -> list[str]:
    folded = fold(phrase)
    folded = re.sub(r"^(hey |ok )?jarvis ", "", folded)
    tokens: list[str] = []
    for token in folded.split():
        if token in _COMMAND_WORDS or token in _STOPWORDS:
            continue
        if len(token) < 2 and not token.isdigit():
            continue
        tokens.append(token)
        if len(tokens) >= MAX_TOKENS:
            break
    return tokens


def derive_filename(request: str = "", fallback_text: str = "") -> str:
    """Déduit un nom court à partir de la demande, sinon du texte, sinon ``document.txt``."""
    explicit = extract_explicit_name(request)
    if explicit:
        return sanitize_filename(explicit)
    tokens = _tokens_from_phrase(request)
    if not tokens:
        tokens = _tokens_from_phrase(fallback_text)
    if not tokens:
        return "document.txt"
    return sanitize_filename("_".join(tokens))


def generate_unique_filename(directory: Path | str, filename: str) -> str:
    """Nom libre dans ``directory`` : ``nom.txt``, puis ``nom_1.txt``, ``nom_2.txt``…"""
    directory = Path(directory)
    safe = sanitize_filename(filename)
    stem = safe[:-4]
    candidate = f"{stem}.txt"
    if not (directory / candidate).exists():
        return candidate
    for index in range(1, MAX_COLLISIONS + 1):
        candidate = f"{stem}_{index}.txt"
        if not (directory / candidate).exists():
            return candidate
    raise OSError("Impossible de trouver un nom de fichier libre.")
