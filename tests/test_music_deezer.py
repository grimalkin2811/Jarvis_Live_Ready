"""Tests unitaires Deezer (mocks HTTP, sans compte réel ni réseau)."""

from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def re_search_generic(url: str) -> bool:
    """True pour ``/search?q=...`` mais pas ``/search/artist`` etc."""
    return bool(re.search(r"/search\?", url)) and "/search/" not in url

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.music.manager import MusicManager, set_default_music_manager  # noqa: E402
from src.music.models import Album, Artist, Playlist, Track  # noqa: E402
from src.music.providers.deezer import (  # noqa: E402
    DeezerAPIError,
    DeezerHTTPClient,
    DeezerProvider,
    build_deezer_uri,
    build_deezer_web_url,
    normalize_text,
    pick_best_named,
    pick_best_track,
    rank_tracks,
    resolve_access_token,
    similarity,
    split_title_artist,
)
from src import tools  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures Deezer (réponses API réalistes)
# ---------------------------------------------------------------------------

TRACK_ATW = {
    "id": 3135556,
    "title": "Around the World",
    "title_short": "Around the World",
    "duration": 214,
    "link": "https://www.deezer.com/track/3135556",
    "preview": "https://cdns-preview-x.deezer.com/x.mp3",
    "artist": {"id": 27, "name": "Daft Punk"},
    "album": {"id": 302127, "title": "Discovery"},
    "type": "track",
}

TRACK_HALO_BEYONCE = {
    "id": 111,
    "title": "Halo",
    "duration": 200,
    "link": "https://www.deezer.com/track/111",
    "artist": {"id": 1, "name": "Beyoncé"},
    "album": {"id": 10, "title": "I Am... Sasha Fierce"},
    "type": "track",
}

TRACK_HALO_PHOENIX = {
    "id": 222,
    "title": "Halo",
    "duration": 180,
    "link": "https://www.deezer.com/track/222",
    "artist": {"id": 2, "name": "Bethany Joy Lenz"},
    "album": {"id": 20, "title": "Halo"},
    "type": "track",
}

ARTIST_DP = {
    "id": 27,
    "name": "Daft Punk",
    "link": "https://www.deezer.com/artist/27",
    "nb_fan": 4000000,
    "type": "artist",
}

ALBUM_DISCOVERY = {
    "id": 302127,
    "title": "Discovery",
    "link": "https://www.deezer.com/album/302127",
    "nb_tracks": 14,
    "artist": {"id": 27, "name": "Daft Punk"},
    "type": "album",
}

PLAYLIST_CHILL = {
    "id": 908622995,
    "title": "Chill",
    "link": "https://www.deezer.com/playlist/908622995",
    "nb_tracks": 40,
    "public": True,
    "user": {"id": 1, "name": "Deezer"},
    "type": "playlist",
}

PLAYLIST_CYBER = {
    "id": 123456,
    "title": "Cyberpunk",
    "link": "https://www.deezer.com/playlist/123456",
    "nb_tracks": 25,
    "public": False,
    "user": {"id": 99, "name": "JarvisUser"},
    "type": "playlist",
}

CHART_TRACK = TRACK_ATW

USER_ME = {"id": 99, "name": "JarvisUser", "firstname": "Jarvis"}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    def read(self):
        if isinstance(self._payload, (bytes, bytearray)):
            return bytes(self._payload)
        return json.dumps(self._payload).encode("utf-8")

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeDeezerOpener:
    """Routeur HTTP fake pour DeezerAPI."""

    def __init__(self):
        self.calls: list[str] = []
        self.fail_network = False
        self.fail_timeout = False
        self.token_rejected = False
        self.routes: dict[str, object] = {}

    def set(self, contains: str, payload):
        self.routes[contains] = payload

    def __call__(self, request, timeout=None):
        url = request.full_url if hasattr(request, "full_url") else str(request)
        self.calls.append(url)
        if self.fail_network:
            import urllib.error

            raise urllib.error.URLError("Network down")
        if self.fail_timeout:
            raise TimeoutError("timeout")

        # Auth check
        if "access_token=" in url and self.token_rejected:
            return FakeResponse({"error": {"type": "OAuthException", "message": "Invalid token", "code": 200}})

        # Routes exactes d'abord (clés plus longues prioritaires).
        for key, payload in sorted(self.routes.items(), key=lambda kv: -len(kv[0])):
            if key in url:
                if isinstance(payload, Exception):
                    raise payload
                return FakeResponse(payload)

        # Defaults intelligents (endpoints dédiés avant /search générique).
        if "/search/artist" in url:
            return FakeResponse({"data": [ARTIST_DP], "total": 1})
        if "/search/album" in url:
            return FakeResponse({"data": [ALBUM_DISCOVERY], "total": 1})
        if "/search/playlist" in url:
            return FakeResponse({"data": [PLAYLIST_CHILL], "total": 1})
        if "/search/track" in url:
            return FakeResponse({"data": [TRACK_ATW], "total": 1})
        if re_search_generic(url):
            return FakeResponse({"data": [TRACK_ATW], "total": 1})
        if "/artist/27/top" in url:
            return FakeResponse({"data": [TRACK_ATW]})
        if "/artist/27" in url:
            return FakeResponse(ARTIST_DP)
        if "/album/302127" in url:
            return FakeResponse(ALBUM_DISCOVERY)
        if "/track/3135556" in url:
            return FakeResponse(TRACK_ATW)
        if "/playlist/908622995" in url:
            return FakeResponse(PLAYLIST_CHILL)
        if "/playlist/123456" in url:
            return FakeResponse(PLAYLIST_CYBER)
        if "/chart/" in url:
            return FakeResponse({"data": [CHART_TRACK]})
        if "/user/me/playlists" in url:
            return FakeResponse({"data": [PLAYLIST_CYBER, PLAYLIST_CHILL]})
        if "/user/me" in url:
            return FakeResponse(USER_ME)
        return FakeResponse({"data": []})


def _provider(opener=None, token=None, token_path=None, media_ok=True):
    opener = opener or FakeDeezerOpener()
    client = DeezerHTTPClient(access_token=token, opener=opener)
    opened = []

    def content_opener(resource, resource_id, **kwargs):
        opened.append((resource, str(resource_id), kwargs))
        return {
            "success": True,
            "opened": True,
            "via": "app",
            "uri": f"deezer://www.deezer.com/{resource}/{resource_id}",
            "url": f"https://www.deezer.com/{resource}/{resource_id}",
            "resource": resource,
            "resource_id": str(resource_id),
        }

    media_calls = []

    def media_sender(vk):
        media_calls.append(vk)
        return media_ok

    provider = DeezerProvider(
        client=client,
        access_token=token,
        token_path=token_path,
        content_opener=content_opener,
        media_key_sender=media_sender,
        running_checker=lambda: True,
    )
    provider._test_opened = opened  # type: ignore[attr-defined]
    provider._test_media = media_calls  # type: ignore[attr-defined]
    return provider


# ===========================================================================
# Similarité / ranking
# ===========================================================================


class TestNormalizeAndRank(unittest.TestCase):
    def test_normalize_accents(self):
        self.assertEqual(normalize_text("Cyberpünk"), "cyberpunk")
        self.assertEqual(normalize_text("  CHILL!! "), "chill")

    def test_similarity(self):
        self.assertEqual(similarity("Chill", "chill"), 1.0)
        self.assertGreater(similarity("cyberpunk", "Cyber Punk"), 0.7)
        self.assertGreater(similarity("daft punk", "Daft Punk Alive"), 0.5)
        self.assertLess(similarity("daft punk", "metallica"), 0.3)

    def test_split_title_artist(self):
        title, artist = split_title_artist("Around the World de Daft Punk")
        self.assertEqual(title.lower(), "around the world")
        self.assertEqual(artist.lower(), "daft punk")
        title, artist = split_title_artist("Halo")
        self.assertEqual(title, "Halo")
        self.assertIsNone(artist)

    def test_pick_best_track_clear(self):
        tracks = [
            Track(id="1", title="Around the World", artist="Daft Punk"),
            Track(id="2", title="One More Time", artist="Daft Punk"),
        ]
        best, alts, reason = pick_best_track(
            tracks, title="Around the World", artist="Daft Punk"
        )
        self.assertIsNotNone(best)
        self.assertEqual(best.id, "1")
        self.assertEqual(alts, [])
        self.assertIsNone(reason)

    def test_pick_best_track_ambiguous(self):
        tracks = [
            Track(id="1", title="Halo", artist="Beyoncé"),
            Track(id="2", title="Halo", artist="Bethany Joy Lenz"),
        ]
        best, alts, reason = pick_best_track(tracks, title="Halo")
        self.assertIsNone(best)
        self.assertEqual(reason, "ambiguous")
        self.assertGreaterEqual(len(alts), 2)

    def test_pick_best_named_playlist(self):
        items = [
            Playlist(id="1", title="Chill"),
            Playlist(id="2", title="Cyberpunk"),
            Playlist(id="3", title="Workout"),
        ]
        best, alts, reason = pick_best_named(items, "cyberpunk")
        self.assertIsNotNone(best)
        self.assertEqual(best.title, "Cyberpunk")

    def test_rank_empty(self):
        best, alts, reason = pick_best_track([], title="x")
        self.assertIsNone(best)
        self.assertEqual(reason, "empty")


# ===========================================================================
# Client HTTP
# ===========================================================================


class TestDeezerHTTPClient(unittest.TestCase):
    def test_search_tracks(self):
        opener = FakeDeezerOpener()
        client = DeezerHTTPClient(opener=opener)
        data = client.search("daft punk", limit=3)
        self.assertTrue(data)
        self.assertEqual(data[0]["title"], "Around the World")

    def test_network_error(self):
        opener = FakeDeezerOpener()
        opener.fail_network = True
        client = DeezerHTTPClient(opener=opener)
        with self.assertRaises(DeezerAPIError) as ctx:
            client.search("x")
        self.assertIn("Réseau", str(ctx.exception))

    def test_timeout(self):
        opener = FakeDeezerOpener()
        opener.fail_timeout = True
        client = DeezerHTTPClient(opener=opener)
        with self.assertRaises(DeezerAPIError) as ctx:
            client.search("x")
        self.assertIn("timeout", str(ctx.exception).lower())

    def test_api_error_payload(self):
        opener = FakeDeezerOpener()
        opener.set("/search", {"error": {"message": "Quota", "code": 4}})
        client = DeezerHTTPClient(opener=opener)
        with self.assertRaises(DeezerAPIError) as ctx:
            client.search("x")
        self.assertEqual(ctx.exception.code, 4)

    def test_auth_required(self):
        client = DeezerHTTPClient(access_token=None, opener=FakeDeezerOpener())
        with self.assertRaises(DeezerAPIError):
            client.get_me()


# ===========================================================================
# Provider — recherche & lecture
# ===========================================================================


class TestDeezerProviderSearch(unittest.TestCase):
    def setUp(self):
        self.provider = _provider()

    def test_search_artist_found(self):
        artists = self.provider.search_artists("Daft Punk")
        self.assertTrue(artists)
        self.assertEqual(artists[0].name, "Daft Punk")

    def test_search_track_found(self):
        tracks = self.provider.search_tracks("Around the World")
        self.assertTrue(tracks)
        self.assertEqual(tracks[0].title, "Around the World")

    def test_search_album_found(self):
        albums = self.provider.search_albums("Discovery")
        self.assertTrue(albums)
        self.assertEqual(albums[0].title, "Discovery")

    def test_search_empty(self):
        opener = FakeDeezerOpener()
        opener.set("/search", {"data": [], "total": 0})
        opener.set("/search/artist", {"data": []})
        opener.set("/search/album", {"data": []})
        opener.set("/search/playlist", {"data": []})
        opener.set("/search/track", {"data": []})
        provider = _provider(opener)
        results = provider.search("zzz_inexistant_xyz")
        self.assertTrue(results.is_empty())

    def test_search_multi(self):
        results = self.provider.search("Daft Punk")
        self.assertFalse(results.is_empty())
        data = results.to_dict()
        self.assertIn("tracks", data)
        self.assertIn("artists", data)


class TestDeezerProviderPlay(unittest.TestCase):
    def setUp(self):
        self.provider = _provider()

    def test_play_track(self):
        result = self.provider.play_track("Around the World", artist="Daft Punk")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["action"], "play_track")
        self.assertEqual(result["track"]["title"], "Around the World")
        self.assertTrue(self.provider._test_opened)

    def test_play_artist(self):
        result = self.provider.play_artist("Daft Punk")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["artist"]["name"], "Daft Punk")

    def test_play_album(self):
        result = self.provider.play_album("Discovery", artist="Daft Punk")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["album"]["title"], "Discovery")

    def test_play_playlist_public(self):
        result = self.provider.play_playlist("Chill")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["playlist"]["title"], "Chill")

    def test_play_track_not_found(self):
        opener = FakeDeezerOpener()
        opener.set("/search", {"data": []})
        opener.set("/search/track", {"data": []})
        provider = _provider(opener)
        result = provider.play_track("TitreInexistantXYZ123")
        self.assertFalse(result["success"])

    def test_play_track_ambiguous(self):
        opener = FakeDeezerOpener()
        halo = {"data": [TRACK_HALO_BEYONCE, TRACK_HALO_PHOENIX], "total": 2}
        opener.set("/search?", halo)
        opener.set("/search/track", halo)
        provider = _provider(opener)
        result = provider.play_track("Halo")
        self.assertFalse(result["success"], result)
        self.assertTrue(result.get("ambiguous"), result)
        self.assertGreaterEqual(len(result.get("candidates") or []), 2)
        blob = (result.get("message") or result.get("error") or "").lower()
        self.assertTrue("plusieurs" in blob or "halo" in blob, result)

    def test_play_chart(self):
        result = self.provider.play_chart()
        self.assertTrue(result["success"], result)


class TestDeezerProviderControls(unittest.TestCase):
    def setUp(self):
        self.provider = _provider()

    def test_pause_resume_next_previous(self):
        self.assertTrue(self.provider.pause()["success"])
        self.assertTrue(self.provider.resume()["success"])
        self.assertTrue(self.provider.next()["success"])
        self.assertTrue(self.provider.previous()["success"])
        self.assertEqual(len(self.provider._test_media), 4)

    def test_media_key_failure(self):
        provider = _provider(media_ok=False)
        result = provider.pause()
        self.assertFalse(result["success"])


class TestDeezerProviderState(unittest.TestCase):
    def test_current_after_play(self):
        provider = _provider()
        provider.play_track("Around the World", artist="Daft Punk")
        current = provider.get_current_track()
        self.assertTrue(current["success"])
        self.assertTrue(current["available"])
        self.assertEqual(current["track"]["title"], "Around the World")
        self.assertIn("Around the World", current["message"])

    def test_current_none(self):
        provider = _provider()
        current = provider.get_current_track()
        self.assertTrue(current["success"])
        self.assertFalse(current["available"])
        self.assertIn("ne peux pas", current["message"].lower())

    def test_current_context_only(self):
        provider = _provider()
        provider.play_album("Discovery", artist="Daft Punk")
        current = provider.get_current_track()
        self.assertTrue(current["available"])
        self.assertIn("Discovery", current["message"])


class TestDeezerAuth(unittest.TestCase):
    def test_not_authenticated(self):
        provider = _provider(token=None)
        status = provider.auth_status()
        self.assertTrue(status["success"])
        self.assertFalse(status["authenticated"])

    def test_valid_token(self):
        provider = _provider(token="valid-token-xyz")
        status = provider.auth_status()
        self.assertTrue(status["authenticated"])
        self.assertEqual(status["user_name"], "JarvisUser")

    def test_expired_token(self):
        opener = FakeDeezerOpener()
        opener.token_rejected = True
        provider = _provider(opener=opener, token="bad-token")
        status = provider.auth_status()
        self.assertFalse(status.get("authenticated"))
        self.assertTrue(status.get("token_present"))

    def test_connect_and_disconnect(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "deezer_auth.json"
            provider = _provider(token=None, token_path=path)
            result = provider.connect_with_token("new-token-abc")
            self.assertTrue(result["success"], result)
            self.assertTrue(path.is_file())
            # Le fichier ne doit pas être vide
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["access_token"], "new-token-abc")
            disc = provider.disconnect()
            self.assertTrue(disc["success"])
            self.assertFalse(path.is_file())

    def test_personal_playlists_require_auth(self):
        provider = _provider(token=None)
        result = provider.get_playlists(personal=True)
        self.assertFalse(result["success"])
        self.assertTrue(result.get("auth_required"))

    def test_personal_playlists_with_auth(self):
        provider = _provider(token="tok")
        result = provider.get_playlists(personal=True)
        self.assertTrue(result["success"], result)
        self.assertGreaterEqual(result["count"], 1)
        titles = [p["title"] for p in result["playlists"]]
        self.assertIn("Cyberpunk", titles)

    def test_play_personal_playlist(self):
        provider = _provider(token="tok")
        result = provider.play_playlist("cyberpunk", personal=True)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["playlist"]["title"], "Cyberpunk")

    def test_play_personal_playlist_without_auth(self):
        provider = _provider(token=None)
        result = provider.play_playlist("Cyberpunk", personal=True)
        self.assertFalse(result["success"])
        self.assertTrue(result.get("auth_required"))

    def test_resolve_token_env(self):
        token = resolve_access_token(
            env={"DEEZER_ACCESS_TOKEN": "from-env"},
            store_path=Path("/tmp/nope-does-not-exist-jarvis"),
        )
        self.assertEqual(token, "from-env")


class TestDeezerErrors(unittest.TestCase):
    def test_api_unavailable(self):
        opener = FakeDeezerOpener()
        opener.fail_network = True
        provider = _provider(opener)
        result = provider.play_track("Anything")
        self.assertFalse(result["success"])
        self.assertTrue(result.get("network_error") or "indisponible" in result.get("error", "").lower() or "Réseau" in result.get("error", ""))

    def test_deezer_links(self):
        self.assertIn("deezer://", build_deezer_uri("track", "1"))
        self.assertIn("autoplay", build_deezer_uri("track", "1"))
        self.assertTrue(build_deezer_web_url("album", "2").startswith("https://www.deezer.com/"))


# ===========================================================================
# MusicManager + tools
# ===========================================================================


class TestMusicManager(unittest.TestCase):
    def setUp(self):
        self.provider = _provider(token="tok")
        self.manager = MusicManager(provider=self.provider)
        set_default_music_manager(self.manager)

    def tearDown(self):
        set_default_music_manager(None)

    def test_handle_natural_phrases(self):
        cases = [
            ("mets de la musique", True),
            ("lance Daft Punk", True),
            ("joue Around the World de Daft Punk", True),
            ("joue l'album Discovery de Daft Punk", True),
            ("joue ma playlist Cyberpunk", True),
            ("pause", True),
            ("reprends", True),
            ("suivant", True),
            ("morceau précédent", True),
            ("qu'est-ce qui joue ?", True),
            ("liste mes playlists", True),
            ("cherche Daft Punk", True),
        ]
        for phrase, expect_ok in cases:
            result = self.manager.handle_intent(phrase)
            self.assertEqual(bool(result.get("success")), expect_ok, f"{phrase} -> {result}")

    def test_tools_registry_includes_music(self):
        names = {d["name"] for d in tools.TOOL_DECLARATIONS}
        for required in (
            "music_play",
            "music_search",
            "music_pause",
            "music_resume",
            "music_next",
            "music_previous",
            "music_current",
            "music_list_playlists",
            "music_play_track",
            "music_play_artist",
            "music_play_album",
            "music_play_playlist",
        ):
            self.assertIn(required, names)
            self.assertIn(required, tools.TOOL_FUNCTIONS)

    def test_tools_call_manager(self):
        result = tools.music_play(query="Daft Punk")
        self.assertTrue(result["success"], result)
        result = tools.music_search("Daft Punk")
        self.assertTrue(result["success"], result)
        result = tools.music_pause()
        self.assertTrue(result["success"], result)
        result = tools.music_current()
        self.assertTrue(result["success"], result)

    def test_music_play_structured(self):
        result = tools.music_play_track("Around the World", artist="Daft Punk")
        self.assertTrue(result["success"], result)
        result = tools.music_play_playlist("Cyberpunk", personal=True)
        self.assertTrue(result["success"], result)

    def test_deezer_in_apps_whitelist(self):
        self.assertIn("deezer", tools.APPS)


class TestFocusModeBlocksMusic(unittest.TestCase):
    def test_focus_blocks_music_tools(self):
        from src.modes import FOCUS_ALWAYS_BLOCKED_TOOLS

        for name in ("music_play", "music_search", "music_pause", "music_next"):
            self.assertIn(name, FOCUS_ALWAYS_BLOCKED_TOOLS)


if __name__ == "__main__":
    unittest.main()
