"""Tests for the v1.5.2 local Deezer playlist fallback."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.music.local_playlists import (  # noqa: E402
    LocalPlaylistStore,
    LocalPlaylistStoreError,
    extract_playlist_id,
)
from src.music.providers.deezer import DeezerHTTPClient, DeezerProvider  # noqa: E402


class _NoNetwork:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request, timeout=None):
        self.calls += 1
        raise AssertionError("Le fallback local ne doit pas appeler Deezer.")


class TestLocalPlaylistUrls(unittest.TestCase):
    def test_supported_urls(self):
        self.assertEqual(extract_playlist_id("https://www.deezer.com/playlist/123456"), "123456")
        self.assertEqual(extract_playlist_id("https://www.deezer.com/fr/playlist/123456"), "123456")
        self.assertEqual(extract_playlist_id("https://deezer.com/playlist/123456?autoplay=true"), "123456")

    def test_rejects_arbitrary_urls_and_ids(self):
        for value in (
            "https://example.com/playlist/123456",
            "https://www.deezer.com/track/123456",
            "https://www.deezer.com/playlist/not-a-number",
            "https://www.deezer.com/playlist/0",
            "123456",
            "not a url",
        ):
            self.assertIsNone(extract_playlist_id(value), value)


class TestLocalPlaylistStore(unittest.TestCase):
    def test_add_get_remove_list_and_clear(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "deezer_playlists.json"
            store = LocalPlaylistStore(path)
            self.assertEqual(store.list(), [])
            entry = store.add("  Cyberpunk  ", "123456")
            self.assertEqual(entry["id"], "123456")
            self.assertEqual(store.get("CYBER PUNK")["id"], "123456")
            self.assertEqual(len(store.list()), 1)
            self.assertTrue(path.is_file())
            self.assertTrue(store.remove("cyberpunk"))
            self.assertEqual(store.list(), [])
            store.add("D&D", "987654")
            store.add("Cinematic", "456789")
            self.assertEqual(store.clear(), 2)
            self.assertEqual(store.list(), [])

    def test_save_from_url_and_reject_invalid_id(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalPlaylistStore(Path(directory) / "playlists.json")
            entry = store.add("Cyberpunk", "", url="https://www.deezer.com/fr/playlist/123456")
            self.assertEqual(entry["id"], "123456")
            with self.assertRaises(LocalPlaylistStoreError):
                store.add("Invalid", "abc")
            with self.assertRaises(LocalPlaylistStoreError):
                store.add("Invalid URL", "123", url="https://example.com/playlist/123")

    def test_corrupt_json_is_ignored_and_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "playlists.json"
            path.write_text("{not valid json", encoding="utf-8")
            store = LocalPlaylistStore(path)
            self.assertEqual(store.list(), [])
            self.assertTrue(store.issues)
            store.add("Recovered", "42")
            self.assertEqual(store.get("recovered")["id"], "42")
            self.assertTrue(path.with_suffix(".json.bak").is_file())

    def test_ambiguous_fuzzy_name_is_not_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalPlaylistStore(Path(directory) / "playlists.json")
            store.add("Cinematic", "1")
            store.add("Cinematic Mix", "2")
            match, candidates, reason = store.resolve("Cinemati")
            self.assertIsNone(match)
            self.assertEqual(reason, "ambiguous")
            self.assertEqual({item["id"] for item in candidates}, {"1", "2"})


class TestLocalPlaylistPlayback(unittest.TestCase):
    def test_local_playlist_plays_without_oauth_or_network(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalPlaylistStore(Path(directory) / "playlists.json")
            store.add("Cyberpunk", "123456")
            network = _NoNetwork()
            opened = []
            provider = DeezerProvider(
                client=DeezerHTTPClient(access_token=None, opener=network),
                local_playlist_store=store,
                content_opener=lambda resource, resource_id, **kwargs: opened.append(
                    (resource, resource_id, kwargs)
                )
                or {
                    "success": True,
                    "via": "app",
                    "uri": f"deezer://www.deezer.com/{resource}/{resource_id}?autoplay=true",
                    "url": f"https://www.deezer.com/{resource}/{resource_id}?autoplay=true",
                },
            )
            result = provider.play_playlist("CYBERPUNK", personal=True)
            self.assertTrue(result["success"], result)
            self.assertEqual(opened[0][0:2], ("playlist", "123456"))
            self.assertTrue(opened[0][2]["autoplay"])
            self.assertEqual(network.calls, 0)

    def test_provider_saves_url_without_oauth(self):
        with tempfile.TemporaryDirectory() as directory:
            network = _NoNetwork()
            provider = DeezerProvider(
                client=DeezerHTTPClient(access_token=None, opener=network),
                local_playlists_path=Path(directory) / "playlists.json",
            )
            result = provider.save_local_playlist(
                "Cyberpunk", url="https://www.deezer.com/fr/playlist/123456"
            )
            self.assertTrue(result["success"], result)
            self.assertEqual(result["playlist"]["id"], "123456")
            self.assertEqual(network.calls, 0)

    def test_invalid_token_does_not_block_local_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalPlaylistStore(Path(directory) / "playlists.json")
            store.add("Cyberpunk", "123456")
            opened = []
            provider = DeezerProvider(
                client=DeezerHTTPClient(access_token="invalid", opener=_NoNetwork()),
                local_playlist_store=store,
                content_opener=lambda resource, resource_id, **kwargs: opened.append(resource_id)
                or {"success": True, "via": "app"},
            )
            result = provider.play_playlist("Cyberpunk", personal=True)
            self.assertTrue(result["success"], result)
            self.assertEqual(opened, ["123456"])


if __name__ == "__main__":
    unittest.main()
