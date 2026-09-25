"""Tests du parseur d'intentions musicales (formulations naturelles FR)."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.music.intents import (  # noqa: E402
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
    parse_music_intent,
)


class TestMusicIntents(unittest.TestCase):
    def test_play_music_generic(self):
        for phrase in (
            "mets de la musique",
            "lance de la musique",
            "joue de la musique",
            "de la musique",
        ):
            intent = parse_music_intent(phrase)
            self.assertEqual(intent.intent, PLAY_MUSIC, phrase)

    def test_play_artist(self):
        for phrase in (
            "lance Daft Punk",
            "joue Daft Punk",
            "mets Daft Punk",
            "play Daft Punk",
        ):
            intent = parse_music_intent(phrase)
            self.assertEqual(intent.intent, PLAY_ARTIST, phrase)
            self.assertIn("daft punk", (intent.artist or "").lower())

    def test_play_track_with_artist(self):
        intent = parse_music_intent("joue Around the World de Daft Punk")
        self.assertEqual(intent.intent, PLAY_TRACK)
        self.assertIn("around the world", (intent.track or "").lower())
        self.assertIn("daft punk", (intent.artist or "").lower())

    def test_play_album(self):
        intent = parse_music_intent("joue l'album Discovery de Daft Punk")
        self.assertEqual(intent.intent, PLAY_ALBUM)
        self.assertIn("discovery", (intent.album or "").lower())
        self.assertIn("daft punk", (intent.artist or "").lower())

    def test_play_personal_playlist(self):
        for phrase in (
            "joue ma playlist Chill",
            "lance ma playlist Cyberpunk",
            "mets ma playlist lo-fi",
        ):
            intent = parse_music_intent(phrase)
            self.assertEqual(intent.intent, PLAY_PLAYLIST, phrase)
            self.assertTrue(intent.personal, phrase)
            self.assertTrue(intent.playlist, phrase)

    def test_controls(self):
        self.assertEqual(parse_music_intent("pause").intent, PAUSE)
        self.assertEqual(parse_music_intent("mets en pause").intent, PAUSE)
        self.assertEqual(parse_music_intent("reprends").intent, RESUME)
        self.assertEqual(parse_music_intent("suivant").intent, NEXT)
        self.assertEqual(parse_music_intent("passe au morceau suivant").intent, NEXT)
        self.assertEqual(parse_music_intent("morceau précédent").intent, PREVIOUS)
        self.assertEqual(parse_music_intent("reviens").intent, PREVIOUS)

    def test_current_track(self):
        for phrase in (
            "quel morceau est en train de jouer ?",
            "qu'est-ce qui joue ?",
            "c'est quoi ce morceau",
        ):
            intent = parse_music_intent(phrase)
            self.assertEqual(intent.intent, CURRENT_TRACK, phrase)

    def test_list_playlists(self):
        intent = parse_music_intent("liste mes playlists")
        self.assertEqual(intent.intent, LIST_PLAYLISTS)
        self.assertTrue(intent.personal)

    def test_search(self):
        intent = parse_music_intent("cherche Daft Punk")
        self.assertEqual(intent.intent, SEARCH)
        self.assertIn("daft punk", intent.query.lower())

    def test_accents_and_case(self):
        intent = parse_music_intent("JOUE ma PLAYLIST Cyberpünk")
        self.assertEqual(intent.intent, PLAY_PLAYLIST)
        self.assertTrue(intent.playlist)

    def test_politeness_fillers(self):
        intent = parse_music_intent("Jarvis, joue Daft Punk s'il te plaît")
        self.assertEqual(intent.intent, PLAY_ARTIST)
        self.assertIn("daft punk", (intent.artist or "").lower())

    def test_empty(self):
        intent = parse_music_intent("")
        self.assertEqual(intent.intent, "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
