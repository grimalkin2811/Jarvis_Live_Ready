"""Shared text matching helpers for music features.

The Deezer provider and the local playlist store deliberately use the same
normalization and similarity rules.  Keeping them here avoids subtly different
matching behaviour between the API and the local fallback.
"""

from __future__ import annotations

import re
import unicodedata


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def normalize_text(value: str) -> str:
    """Normalize a label for case/accent/punctuation-insensitive comparisons."""
    text = _strip_accents(str(value or "")).lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> float:
    """Return a small, deterministic similarity score in the ``[0, 1]`` range."""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    # Compact forms make "cyberpunk" and "Cyber Punk" close matches.
    ca, cb = na.replace(" ", ""), nb.replace(" ", "")
    if ca == cb:
        return 0.96
    if na in nb or nb in na or ca in cb or cb in ca:
        shorter, longer = (ca, cb) if len(ca) <= len(cb) else (cb, ca)
        return 0.72 + 0.25 * (len(shorter) / max(len(longer), 1))

    ta, tb = set(na.split()), set(nb.split())
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    union = len(ta | tb)
    jaccard = inter / union if union else 0.0
    coverage = inter / len(ta)
    score = max(jaccard, coverage * 0.85)
    if score < 0.5:
        token_hits = sum(1 for token in ta if token in cb or any(token in item for item in tb))
        if token_hits:
            score = max(score, 0.55 * token_hits / max(len(ta), 1))
        if ca and cb and (ca in cb or cb in ca):
            score = max(score, 0.75)
    return score
