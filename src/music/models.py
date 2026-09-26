"""Modèles de données musicaux indépendants du fournisseur."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


def _clean(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    artist: str = ""
    album: str = ""
    duration: int = 0
    link: str = ""
    preview: str = ""
    provider: str = "deezer"
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        return data

    def label(self) -> str:
        if self.artist:
            return f"{self.title} de {self.artist}"
        return self.title


@dataclass(frozen=True)
class Artist:
    id: str
    name: str
    link: str = ""
    nb_fans: int = 0
    provider: str = "deezer"
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        return data


@dataclass(frozen=True)
class Album:
    id: str
    title: str
    artist: str = ""
    link: str = ""
    nb_tracks: int = 0
    provider: str = "deezer"
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        return data

    def label(self) -> str:
        if self.artist:
            return f"{self.title} de {self.artist}"
        return self.title


@dataclass(frozen=True)
class Playlist:
    id: str
    title: str
    link: str = ""
    nb_tracks: int = 0
    owner: str = ""
    public: bool = True
    personal: bool = False
    provider: str = "deezer"
    raw: dict[str, Any] = field(default_factory=dict, repr=False, compare=False)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("raw", None)
        return data


@dataclass
class SearchResults:
    """Résultats d'une recherche multi-types."""

    query: str = ""
    tracks: list[Track] = field(default_factory=list)
    artists: list[Artist] = field(default_factory=list)
    albums: list[Album] = field(default_factory=list)
    playlists: list[Playlist] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.tracks or self.artists or self.albums or self.playlists)

    def to_dict(self, limit: int = 5) -> dict[str, Any]:
        return {
            "query": self.query,
            "tracks": [t.to_dict() for t in self.tracks[:limit]],
            "artists": [a.to_dict() for a in self.artists[:limit]],
            "albums": [a.to_dict() for a in self.albums[:limit]],
            "playlists": [p.to_dict() for p in self.playlists[:limit]],
            "counts": {
                "tracks": len(self.tracks),
                "artists": len(self.artists),
                "albums": len(self.albums),
                "playlists": len(self.playlists),
            },
        }


def track_from_deezer(payload: dict[str, Any]) -> Track:
    artist = payload.get("artist") or {}
    album = payload.get("album") or {}
    return Track(
        id=str(payload.get("id", "")),
        title=_clean(payload.get("title") or payload.get("title_short")),
        artist=_clean(artist.get("name") if isinstance(artist, dict) else artist),
        album=_clean(album.get("title") if isinstance(album, dict) else album),
        duration=int(payload.get("duration") or 0),
        link=_clean(payload.get("link") or payload.get("share")),
        preview=_clean(payload.get("preview")),
        provider="deezer",
        raw=dict(payload),
    )


def artist_from_deezer(payload: dict[str, Any]) -> Artist:
    return Artist(
        id=str(payload.get("id", "")),
        name=_clean(payload.get("name")),
        link=_clean(payload.get("link")),
        nb_fans=int(payload.get("nb_fan") or payload.get("nb_fans") or 0),
        provider="deezer",
        raw=dict(payload),
    )


def album_from_deezer(payload: dict[str, Any]) -> Album:
    artist = payload.get("artist") or {}
    return Album(
        id=str(payload.get("id", "")),
        title=_clean(payload.get("title")),
        artist=_clean(artist.get("name") if isinstance(artist, dict) else artist),
        link=_clean(payload.get("link")),
        nb_tracks=int(payload.get("nb_tracks") or 0),
        provider="deezer",
        raw=dict(payload),
    )


def playlist_from_deezer(payload: dict[str, Any], *, personal: bool = False) -> Playlist:
    user = payload.get("user") or payload.get("creator") or {}
    owner = ""
    if isinstance(user, dict):
        owner = _clean(user.get("name") or user.get("fullname"))
    return Playlist(
        id=str(payload.get("id", "")),
        title=_clean(payload.get("title")),
        link=_clean(payload.get("link")),
        nb_tracks=int(payload.get("nb_tracks") or 0),
        owner=owner,
        public=bool(payload.get("public", True)),
        personal=personal,
        provider="deezer",
        raw=dict(payload),
    )
