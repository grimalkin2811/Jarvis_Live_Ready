"""Préparation du texte final à écrire ou à enregistrer.

Gemini Live produit le contenu (argument ``text`` de l'outil). Cette étape
retire seulement les emballages que le modèle ajoute parfois (préambule,
balises de code) sans réécrire le fond. Elle ne génère rien.
"""

from __future__ import annotations

import re

#: Au-delà, on refuse plutôt que de bloquer la saisie pendant très longtemps.
MAX_TEXT_CHARS = 100_000

_FENCE = re.compile(r"^```[a-zA-Z0-9_-]*\n([\s\S]*?)\n?```$")
_PREAMBLE = re.compile(
    r"^(voici|voila|tiens)\b[^.\n]{0,48}[:.]?\s*$",
    re.IGNORECASE,
)


def coerce_text(value) -> str:
    """Ramène l'argument modèle à une chaîne (listes et enveloppes simples)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(coerce_text(item) for item in value)
    if isinstance(value, dict):
        for key in ("text", "texte", "content", "contenu", "body"):
            if key in value and value[key] is not None:
                return coerce_text(value[key])
        return ""
    return str(value)


def finalize_text(value) -> str:
    """Retourne le texte prêt à insérer, ou une chaîne vide s'il n'y a rien.

    Les retours à la ligne internes sont conservés. Les caractères de contrôle
    autres que tabulation et saut de ligne sont retirés : ils ne doivent jamais
    devenir une touche destructive.
    """
    text = coerce_text(value).replace("\r\n", "\n").replace("\r", "\n")
    stripped = text.strip()
    fenced = _FENCE.match(stripped)
    if fenced:
        stripped = fenced.group(1).strip("\n")
    lines = stripped.split("\n")
    if len(lines) > 1 and _is_preamble(lines[0]):
        lines = lines[1:]
        if lines and not lines[0].strip():
            lines = lines[1:]
        stripped = "\n".join(lines).strip()
    cleaned = []
    for char in stripped:
        code = ord(char)
        if char in "\n\t" or (code >= 32 and code != 0x7F):
            cleaned.append(char)
    return "".join(cleaned)


def _is_preamble(line: str) -> bool:
    candidate = line.strip()
    if not candidate or len(candidate) > 80:
        return False
    return _PREAMBLE.match(candidate) is not None
