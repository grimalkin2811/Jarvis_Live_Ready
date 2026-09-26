"""MusicManager — façade musicale de Jarvis.

Orchestre le fournisseur actif (Deezer par défaut) et expose une API stable
consommée par ``src.tools`` / Gemini Live.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from .intents import (
    AUTH_STATUS,
    CURRENT_TRACK,
    LIST_PLAYLISTS,
    NEXT,
    PAUSE,
    PLAY_ALBUM,
    PLAY_ARTIST,
    PLAY_MUSIC,
    PLAY_PLAYLIST,
    PLAY_TRACK,
    PREVIOUS,
    RESUME,
    SEARCH,
    STOP,
    MusicIntent,
    parse_music_intent,
)
from .models import SearchResults
from .providers.deezer import DeezerAPIError, DeezerProvider

log = logging.getLogger("jarvis.music")

_DEFAULT: MusicManager | None = None
_DEFAULT_LOCK = threading.RLock()


def _ok(**payload: Any) -> dict[str, Any]:
    result = {"success": True, "provider": "deezer"}
    result.update(payload)
    return result


def _err(message: Any, **payload: Any) -> dict[str, Any]:
    result = {"success": False, "error": str(message), "provider": "deezer"}
    result.update(payload)
    return result


class MusicManager:
    """Gestionnaire musical multi-intentions, provider Deezer par défaut."""

    def __init__(self, provider: DeezerProvider | None = None) -> None:
        self.provider = provider or DeezerProvider()

    # ------------------------------------------------------------------
    # API haute niveau
    # ------------------------------------------------------------------

    def handle_intent(self, intent: MusicIntent | str | dict[str, Any]) -> dict[str, Any]:
        """Point d'entrée unique : intention déjà parsée, texte brut, ou dict."""
        if isinstance(intent, str):
            parsed = parse_music_intent(intent)
        elif isinstance(intent, dict):
            parsed = MusicIntent(
                intent=str(intent.get("intent") or SEARCH),
                query=str(intent.get("query") or ""),
                track=intent.get("track"),
                artist=intent.get("artist"),
                album=intent.get("album"),
                playlist=intent.get("playlist"),
                personal=bool(intent.get("personal", False)),
                raw=str(intent.get("raw") or intent.get("query") or ""),
                confidence=float(intent.get("confidence") or 0.0),
            )
        else:
            parsed = intent

        name = parsed.intent
        try:
            if name == PLAY_TRACK:
                return self.play_track(parsed.track or parsed.query, artist=parsed.artist)
            if name == PLAY_ARTIST:
                # Si on a aussi un track, préférer play_track.
                if parsed.track and parsed.artist:
                    return self.play_track(parsed.track, artist=parsed.artist)
                return self.play_artist(parsed.artist or parsed.query)
            if name == PLAY_ALBUM:
                return self.play_album(parsed.album or parsed.query, artist=parsed.artist)
            if name == PLAY_PLAYLIST:
                return self.play_playlist(parsed.playlist or parsed.query, personal=parsed.personal)
            if name == PLAY_MUSIC:
                return self.play_music()
            if name == SEARCH:
                return self.search(parsed.query or parsed.raw)
            if name == PAUSE:
                return self.pause()
            if name == RESUME:
                return self.resume()
            if name == NEXT:
                return self.next()
            if name == PREVIOUS:
                return self.previous()
            if name == STOP:
                return self.stop()
            if name == CURRENT_TRACK:
                return self.get_current_track()
            if name == LIST_PLAYLISTS:
                return self.get_playlists(personal=True)
            if name == AUTH_STATUS:
                return self.auth_status()
            return _err(
                "Intention musicale non reconnue.",
                intent=name,
                hint="Utilise music_play / music_search / music_pause etc.",
            )
        except DeezerAPIError as exc:
            return _err(f"Erreur Deezer : {exc.message}", code=exc.code)
        except Exception as exc:  # pragma: no cover - filet de sécurité
            log.exception("MusicManager failure")
            return _err(f"Erreur musicale inattendue : {exc}")

    # ------------------------------------------------------------------
    # Opérations
    # ------------------------------------------------------------------

    def search(self, query: str, *, limit: int = 8) -> dict[str, Any]:
        q = (query or "").strip()
        if not q:
            return _err("Requête de recherche vide.")
        try:
            results = self.provider.search(q, limit=limit)
        except DeezerAPIError as exc:
            return self._provider_err(exc)
        if results.is_empty():
            return _ok(
                action="search",
                query=q,
                results=results.to_dict(),
                count=0,
                message="Je n'ai trouvé aucun résultat correspondant.",
            )
        summary = _summarize_results(results)
        return _ok(
            action="search",
            query=q,
            results=results.to_dict(limit=limit),
            count=(
                len(results.tracks)
                + len(results.artists)
                + len(results.albums)
                + len(results.playlists)
            ),
            message=summary,
        )

    def play_track(self, track: str | None, *, artist: str | None = None) -> dict[str, Any]:
        if not track and not artist:
            return _err("Précise un morceau ou un artiste.")
        if not track and artist:
            return self.play_artist(artist)
        try:
            return self._tag(self.provider.play_track(track or "", artist=artist))
        except DeezerAPIError as exc:
            return self._provider_err(exc)

    def play_artist(self, artist: str | None) -> dict[str, Any]:
        if not artist:
            return _err("Précise un artiste.")
        try:
            # Heuristique : si la recherche artiste échoue, tenter un morceau.
            result = self.provider.play_artist(artist)
            if result.get("success"):
                return self._tag(result)
            # Fallback track si artiste introuvable
            if result.get("error") and "aucun artiste" in str(result.get("error", "")).lower():
                fallback = self.provider.play_track(artist)
                if fallback.get("success"):
                    return self._tag(fallback)
            return self._tag(result)
        except DeezerAPIError as exc:
            return self._provider_err(exc)

    def play_album(self, album: str | None, *, artist: str | None = None) -> dict[str, Any]:
        if not album:
            return _err("Précise un album.")
        try:
            return self._tag(self.provider.play_album(album, artist=artist))
        except DeezerAPIError as exc:
            return self._provider_err(exc)

    def play_playlist(self, playlist: str | None, *, personal: bool = False) -> dict[str, Any]:
        if not playlist:
            return _err("Précise une playlist.")
        try:
            return self._tag(self.provider.play_playlist(playlist, personal=personal))
        except DeezerAPIError as exc:
            return self._provider_err(exc)

    def play_music(self) -> dict[str, Any]:
        """« Mets de la musique » — Flow si auth, sinon tops Deezer."""
        try:
            if hasattr(self.provider, "play_flow"):
                result = self.provider.play_flow()
                if result.get("success"):
                    return self._tag(result)
            return self._tag(self.provider.play_chart())
        except DeezerAPIError as exc:
            return self._provider_err(exc)

    def pause(self) -> dict[str, Any]:
        return self._tag(self.provider.pause())

    def resume(self) -> dict[str, Any]:
        return self._tag(self.provider.resume())

    def next(self) -> dict[str, Any]:
        return self._tag(self.provider.next())

    def previous(self) -> dict[str, Any]:
        return self._tag(self.provider.previous())

    def stop(self) -> dict[str, Any]:
        if hasattr(self.provider, "stop"):
            return self._tag(self.provider.stop())
        return self.pause()

    def get_current_track(self) -> dict[str, Any]:
        return self._tag(self.provider.get_current_track())

    def get_playlists(self, *, personal: bool = True, limit: int = 30) -> dict[str, Any]:
        return self._tag(self.provider.get_playlists(personal=personal, limit=limit))

    def auth_status(self) -> dict[str, Any]:
        return self._tag(self.provider.auth_status())

    def connect(self, access_token: str) -> dict[str, Any]:
        if hasattr(self.provider, "connect_with_token"):
            return self._tag(self.provider.connect_with_token(access_token))
        return _err("Connexion Deezer non supportée par ce fournisseur.")

    def disconnect(self) -> dict[str, Any]:
        return self._tag(self.provider.disconnect())

    def status(self) -> dict[str, Any]:
        """Diagnostic minimal (UI / outils)."""
        auth = self.provider.auth_status()
        running = False
        try:
            if hasattr(self.provider, "_running_checker"):
                running = bool(self.provider._running_checker())
        except Exception:
            running = False
        available = True
        # Ne pas spammer le réseau ici : on se fie à l'état auth + config.
        return _ok(
            provider=getattr(self.provider, "name", "deezer"),
            deezer_running=running,
            authenticated=bool(auth.get("authenticated")),
            auth=auth,
            available=available,
        )

    # ------------------------------------------------------------------
    # Internes
    # ------------------------------------------------------------------

    def _tag(self, result: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(result, dict):
            return _err("Réponse fournisseur invalide.")
        result.setdefault("provider", getattr(self.provider, "name", "deezer"))
        return result

    def _provider_err(self, exc: DeezerAPIError) -> dict[str, Any]:
        return _err(
            f"Erreur Deezer : {exc.message}",
            code=exc.code,
            status=exc.status,
            network_error=exc.status == 0,
        )


def _summarize_results(results: SearchResults) -> str:
    parts: list[str] = []
    if results.tracks:
        t = results.tracks[0]
        parts.append(f"morceau « {t.label()} »")
    if results.artists:
        parts.append(f"artiste « {results.artists[0].name} »")
    if results.albums:
        a = results.albums[0]
        parts.append(f"album « {a.label()} »")
    if results.playlists:
        parts.append(f"playlist « {results.playlists[0].title} »")
    if not parts:
        return "Je n'ai trouvé aucun résultat correspondant."
    if len(parts) == 1:
        return f"J'ai trouvé : {parts[0]}."
    return "J'ai trouvé : " + ", ".join(parts) + "."


# ---------------------------------------------------------------------------
# Singleton (même schéma que memory / modes / routines)
# ---------------------------------------------------------------------------


def get_default_music_manager() -> MusicManager:
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            _DEFAULT = MusicManager()
        return _DEFAULT


def set_default_music_manager(manager: MusicManager | None) -> None:
    global _DEFAULT
    with _DEFAULT_LOCK:
        _DEFAULT = manager
