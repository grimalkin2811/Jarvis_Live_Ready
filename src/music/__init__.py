"""Intégration musicale de Jarvis (v1.5.2).

Architecture :

    MusicManager  →  providers (DeezerProvider, …)

Le gestionnaire expose une API stable (search, play, pause, …). Les
spécificités Deezer restent confinées dans ``providers.deezer``.
"""

from __future__ import annotations

from .local_playlists import LocalPlaylistStore, extract_playlist_id
from .manager import MusicManager, get_default_music_manager, set_default_music_manager
from .models import Album, Artist, Playlist, SearchResults, Track

__all__ = [
    "Album",
    "Artist",
    "LocalPlaylistStore",
    "MusicManager",
    "Playlist",
    "SearchResults",
    "Track",
    "get_default_music_manager",
    "set_default_music_manager",
    "extract_playlist_id",
]
