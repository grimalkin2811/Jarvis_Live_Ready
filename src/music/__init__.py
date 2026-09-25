"""Intégration musicale de Jarvis (v1.5.0).

Architecture :

    MusicManager  →  providers (DeezerProvider, …)

Le gestionnaire expose une API stable (search, play, pause, …). Les
spécificités Deezer restent confinées dans ``providers.deezer``.
"""

from __future__ import annotations

from .manager import MusicManager, get_default_music_manager, set_default_music_manager
from .models import Album, Artist, Playlist, SearchResults, Track

__all__ = [
    "Album",
    "Artist",
    "MusicManager",
    "Playlist",
    "SearchResults",
    "Track",
    "get_default_music_manager",
    "set_default_music_manager",
]
