"""Persistent local aliases for Deezer playlists.

This store only contains a user-facing name, a Deezer playlist id and a safe
Deezer URL.  It intentionally has no knowledge of OAuth or the Deezer HTTP
client.  That separation lets a saved playlist keep working when OAuth is not
available anymore.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import tempfile
import threading
import urllib.parse
from pathlib import Path
from typing import Any

from .. import paths
from .text import normalize_text, similarity

log = logging.getLogger("jarvis.music.local_playlists")

_STORE_VERSION = 1
_DEEZER_HOSTS = {"deezer.com", "www.deezer.com"}
_PLAYLIST_ID_RE = re.compile(r"^[0-9]+$")
_LOCALE_RE = re.compile(r"^[a-z]{2}(?:-[a-z]{2})?$", re.IGNORECASE)


class LocalPlaylistStoreError(ValueError):
    """Raised when a local playlist mapping cannot be safely written."""


def validate_playlist_id(value: str | int) -> str:
    """Validate and return a positive numeric Deezer playlist id."""
    text = str(value or "").strip()
    if not _PLAYLIST_ID_RE.fullmatch(text) or int(text) <= 0:
        raise LocalPlaylistStoreError("L'identifiant de playlist Deezer doit être numérique et positif.")
    return text


def extract_playlist_id(value: str) -> str | None:
    """Extract a playlist id from a Deezer playlist URL.

    Only the official Deezer hosts and the playlist path are accepted.  An
    arbitrary URL that merely contains ``playlist/`` is never trusted.
    Query strings and fragments are ignored, which also supports Deezer share
    links with tracking parameters.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urllib.parse.urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https", "deezer"}:
        return None
    try:
        hostname = (parsed.hostname or "").lower()
        port = parsed.port
    except ValueError:
        return None
    if hostname not in _DEEZER_HOSTS:
        return None
    if parsed.username or parsed.password or port:
        return None

    segments = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    # Accept /playlist/id and the usual /fr/playlist/id style locale prefix.
    if len(segments) == 2:
        playlist_index = 0
    elif len(segments) == 3 and _LOCALE_RE.fullmatch(segments[0]):
        playlist_index = 1
    else:
        return None
    if segments[playlist_index].lower() != "playlist":
        return None
    try:
        return validate_playlist_id(segments[playlist_index + 1])
    except LocalPlaylistStoreError:
        return None


def extract_deezer_playlist_id(value: str) -> str | None:
    """Descriptive alias for :func:`extract_playlist_id`."""
    return extract_playlist_id(value)


def playlist_id_from_url(value: str) -> str | None:
    """Compatibility-friendly alias used by callers that prefer this wording."""
    return extract_playlist_id(value)


def extract_playlist_id_from_url(value: str) -> str | None:
    """Explicit URL-named alias for :func:`extract_playlist_id`."""
    return extract_playlist_id(value)


def _storage_key(name: str) -> str:
    normalized = normalize_text(name)
    # Spaces and punctuation are not meaningful for a voice alias.  This also
    # makes adding "Cyber Punk" update the existing "Cyberpunk" entry.
    return normalized.replace(" ", "")


def _canonical_url(playlist_id: str) -> str:
    return f"https://www.deezer.com/playlist/{playlist_id}"


class LocalPlaylistStore:
    """Atomic JSON store for user-created Deezer playlist aliases."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path is not None else paths.deezer_playlists_file()
        self._lock = threading.RLock()
        self._issues: list[str] = []

    @property
    def issues(self) -> list[str]:
        """Non-sensitive diagnostics from the last load (safe to expose)."""
        with self._lock:
            return list(self._issues)

    def add(self, name: str, playlist_id: str | int, url: str | None = None) -> dict[str, str]:
        """Add or replace an alias, validating both id and optional URL."""
        display_name = str(name or "").strip()
        key = _storage_key(display_name)
        if not display_name or not key:
            raise LocalPlaylistStoreError("Le nom de playlist ne peut pas être vide.")

        id_text = extract_playlist_id(str(playlist_id)) if isinstance(playlist_id, str) else None
        if id_text is None and str(playlist_id or "").strip():
            id_text = validate_playlist_id(playlist_id)
        if url:
            url_id = extract_playlist_id(url)
            if url_id is None:
                raise LocalPlaylistStoreError("Le lien fourni n'est pas une URL de playlist Deezer valide.")
            if id_text is not None and url_id != id_text:
                raise LocalPlaylistStoreError("L'URL et l'identifiant de playlist Deezer ne correspondent pas.")
            id_text = url_id
        if id_text is None:
            raise LocalPlaylistStoreError("Précise un identifiant ou une URL de playlist Deezer valide.")

        entry = {"name": display_name, "id": id_text, "url": _canonical_url(id_text)}
        with self._lock:
            data = self._read()
            data[key] = entry
            self._write(data)
        return dict(entry)

    def get(self, name: str) -> dict[str, str] | None:
        """Return an exact normalized alias, if present."""
        key = _storage_key(str(name or ""))
        if not key:
            return None
        with self._lock:
            data = self._read()
            entry = data.get(key)
            return dict(entry) if entry else None

    def resolve(
        self,
        name: str,
        *,
        min_score: float = 0.55,
        ambiguity_delta: float = 0.10,
    ) -> tuple[dict[str, str] | None, list[dict[str, str]], str | None]:
        """Resolve an alias with exact, compact and fuzzy matching.

        The return shape mirrors the provider's existing ranking helpers:
        ``(match, candidates, reason)``.  ``reason == 'ambiguous'`` must be
        surfaced to the user instead of selecting a random playlist.
        """
        query = str(name or "").strip()
        if not query:
            return None, [], "empty"
        with self._lock:
            data = self._read()
            exact = data.get(_storage_key(query))
            if exact:
                return dict(exact), [], None
            entries = list(data.values())

        ranked = sorted(
            ((similarity(query, entry["name"]), entry) for entry in entries),
            key=lambda pair: pair[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] < min_score:
            return None, [], "not_found"
        best_score, best = ranked[0]
        close = [entry for score, entry in ranked if score >= best_score - ambiguity_delta]
        if len(close) > 1:
            return None, [dict(entry) for entry in close[:5]], "ambiguous"
        return dict(best), [], None

    def remove(self, name: str) -> bool:
        key = _storage_key(str(name or ""))
        if not key:
            return False
        with self._lock:
            data = self._read()
            if key not in data:
                return False
            del data[key]
            self._write(data)
        return True

    def list(self) -> list[dict[str, str]]:
        """Return sanitized entries in stable display-name order."""
        with self._lock:
            # Listing is the first normal use on a fresh installation; create
            # the empty store then so its location and permissions are stable.
            if not self.path.exists():
                self._write({})
            data = self._read()
            return [dict(data[key]) for key in sorted(data, key=lambda item: data[item]["name"].casefold())]

    def clear(self) -> int:
        """Remove all mappings and return the number removed."""
        with self._lock:
            data = self._read()
            count = len(data)
            if count or self.path.exists():
                self._write({})
            return count

    # ------------------------------------------------------------------
    # JSON safety
    # ------------------------------------------------------------------

    def _read(self) -> dict[str, dict[str, str]]:
        self._issues = []
        if not self.path.is_file():
            return {}
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, ValueError, TypeError) as exc:
            issue = "Le fichier des playlists Deezer est illisible ou corrompu."
            self._issues.append(issue)
            log.warning("%s (%s)", issue, exc.__class__.__name__)
            return {}

        if raw is None or raw == {}:
            return {}
        if not isinstance(raw, dict):
            self._issues.append("La structure des playlists Deezer est invalide.")
            return {}
        raw_playlists = raw.get("playlists", raw)
        if not isinstance(raw_playlists, dict):
            self._issues.append("La section playlists Deezer est invalide.")
            return {}

        clean: dict[str, dict[str, str]] = {}
        for raw_key, raw_entry in raw_playlists.items():
            if not isinstance(raw_entry, dict):
                self._issues.append("Une entrée de playlist Deezer invalide a été ignorée.")
                continue
            name = str(raw_entry.get("name") or raw_key or "").strip()
            key = _storage_key(name)
            raw_id = str(raw_entry.get("id") or "").strip()
            try:
                playlist_id = validate_playlist_id(raw_id)
            except LocalPlaylistStoreError:
                self._issues.append(f"L'entrée Deezer « {name or raw_key} » a un identifiant invalide.")
                continue
            raw_url = str(raw_entry.get("url") or "").strip()
            if raw_url:
                url_id = extract_playlist_id(raw_url)
                if url_id != playlist_id:
                    self._issues.append(f"L'URL de « {name} » a été remplacée par une URL Deezer sûre.")
            clean[key] = {"name": name, "id": playlist_id, "url": _canonical_url(playlist_id)}
        return clean

    def _write(self, data: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": _STORE_VERSION, "playlists": data}

        # Keep a backup before replacing an existing file.  It is best-effort:
        # the atomic replacement below is still the source of truth.
        if self.path.is_file():
            backup = self.path.with_suffix(self.path.suffix + ".bak")
            try:
                shutil.copy2(self.path, backup)
            except OSError as exc:
                log.warning("Backup des playlists Deezer impossible: %s", exc)

        fd, temporary_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        except Exception:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
            raise
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass


__all__ = [
    "LocalPlaylistStore",
    "LocalPlaylistStoreError",
    "extract_playlist_id",
    "extract_deezer_playlist_id",
    "playlist_id_from_url",
    "extract_playlist_id_from_url",
    "validate_playlist_id",
]
