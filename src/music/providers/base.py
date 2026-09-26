"""Interface minimale d'un fournisseur musical."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from ..models import Album, Artist, Playlist, SearchResults, Track


@runtime_checkable
class MusicProvider(Protocol):
    """Contrat attendu par ``MusicManager``.

    Les méthodes renvoient toujours un dict ``{\"success\": bool, ...}``
    cohérent avec le reste de la boîte à outils Jarvis.
    """

    name: str

    def is_available(self) -> bool:
        """Vrai si le fournisseur peut être interrogé (réseau, config…)."""
        ...

    def search(self, query: str, *, limit: int = 8) -> SearchResults:
        ...

    def search_tracks(self, query: str, *, limit: int = 8) -> list[Track]:
        ...

    def search_artists(self, query: str, *, limit: int = 5) -> list[Artist]:
        ...

    def search_albums(self, query: str, *, limit: int = 5) -> list[Album]:
        ...

    def search_playlists(self, query: str, *, limit: int = 5) -> list[Playlist]:
        ...

    def play_track(self, track: Track | str, **kwargs: Any) -> dict[str, Any]:
        ...

    def play_artist(self, artist: Artist | str, **kwargs: Any) -> dict[str, Any]:
        ...

    def play_album(self, album: Album | str, **kwargs: Any) -> dict[str, Any]:
        ...

    def play_playlist(self, playlist: Playlist | str, **kwargs: Any) -> dict[str, Any]:
        ...

    def pause(self) -> dict[str, Any]:
        ...

    def resume(self) -> dict[str, Any]:
        ...

    def next(self) -> dict[str, Any]:
        ...

    def previous(self) -> dict[str, Any]:
        ...

    def get_current_track(self) -> dict[str, Any]:
        ...

    def get_playlists(self, *, personal: bool = True, limit: int = 30) -> dict[str, Any]:
        ...

    def auth_status(self) -> dict[str, Any]:
        ...

    def disconnect(self) -> dict[str, Any]:
        ...
