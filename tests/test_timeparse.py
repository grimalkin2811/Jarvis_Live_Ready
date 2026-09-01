"""Tests des expressions temporelles françaises.

Aucune dépendance externe, aucune écriture disque : le module ``src.timeparse``
est volontairement pur.

    python -m unittest discover tests
"""

import datetime as dt
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import timeparse  # noqa: E402


# Mardi 1er septembre 2026, 14h00 — repère stable pour tous les tests.
NOW = dt.datetime(2026, 9, 1, 14, 0)


class TestParseWhen(unittest.TestCase):
    def assertWhen(self, text, expected):
        self.assertEqual(timeparse.parse_when(text, now=NOW), expected, text)

    def test_relative_delays(self):
        self.assertWhen("dans 20 minutes", dt.datetime(2026, 9, 1, 14, 20))
        self.assertWhen("dans 2 heures", dt.datetime(2026, 9, 1, 16, 0))
        self.assertWhen("dans une heure et demie", dt.datetime(2026, 9, 1, 15, 30))
        self.assertWhen("dans un quart d'heure", dt.datetime(2026, 9, 1, 14, 15))
        self.assertWhen("dans 3 jours", dt.datetime(2026, 9, 4, 14, 0))

    def test_named_days(self):
        self.assertWhen("demain à 9h", dt.datetime(2026, 9, 2, 9, 0))
        self.assertWhen("après-demain à 10h", dt.datetime(2026, 9, 3, 10, 0))
        self.assertWhen("aujourd'hui à 18h", dt.datetime(2026, 9, 1, 18, 0))
        # Le 1er septembre 2026 est un mardi : « lundi » vise le suivant.
        self.assertWhen("lundi à 8h30", dt.datetime(2026, 9, 7, 8, 30))

    def test_time_formats(self):
        self.assertWhen("demain à 9h05", dt.datetime(2026, 9, 2, 9, 5))
        self.assertWhen("demain à 09:30", dt.datetime(2026, 9, 2, 9, 30))
        self.assertWhen("demain à midi", dt.datetime(2026, 9, 2, 12, 0))
        self.assertWhen("demain à minuit", dt.datetime(2026, 9, 2, 0, 0))

    def test_explicit_dates(self):
        self.assertWhen("12/03/2027 à 14h", dt.datetime(2027, 3, 12, 14, 0))
        self.assertWhen("2026-09-15T08:45", dt.datetime(2026, 9, 15, 8, 45))
        # Une date sans année déjà passée bascule sur l'année suivante.
        self.assertWhen("le 12 mars", dt.datetime(2027, 3, 12, 9, 0))

    def test_time_only_rolls_over(self):
        # 9h est déjà passé à 14h : l'échéance vise le lendemain.
        self.assertWhen("à 9h", dt.datetime(2026, 9, 2, 9, 0))
        self.assertWhen("à 18h", dt.datetime(2026, 9, 1, 18, 0))

    def test_iso_keeps_seconds(self):
        self.assertWhen("2026-09-01T14:00:30", dt.datetime(2026, 9, 1, 14, 0, 30))

    def test_unparseable(self):
        for text in ("", None, "un de ces jours", "quand tu veux"):
            self.assertIsNone(timeparse.parse_when(text, now=NOW), repr(text))


class TestExtractWhen(unittest.TestCase):
    def test_separates_message_and_deadline(self):
        moment, message = timeparse.extract_when(
            "rappelle-moi d'appeler Paul demain à 9h", now=NOW
        )
        self.assertEqual(moment, dt.datetime(2026, 9, 2, 9, 0))
        self.assertEqual(message, "appeler Paul")

    def test_keeps_message_when_no_deadline(self):
        moment, message = timeparse.extract_when("acheter du pain", now=NOW)
        self.assertIsNone(moment)
        self.assertEqual(message, "acheter du pain")


class TestParseSchedule(unittest.TestCase):
    def test_every_day(self):
        self.assertEqual(
            timeparse.parse_schedule("tous les jours à 9h"),
            {"time": "09:00", "days": [0, 1, 2, 3, 4, 5, 6]},
        )

    def test_weekdays(self):
        self.assertEqual(
            timeparse.parse_schedule("en semaine à 8h30"),
            {"time": "08:30", "days": [0, 1, 2, 3, 4]},
        )

    def test_weekend_with_hyphen(self):
        self.assertEqual(
            timeparse.parse_schedule("le week-end à 10h"),
            {"time": "10:00", "days": [5, 6]},
        )

    def test_specific_days(self):
        self.assertEqual(
            timeparse.parse_schedule("lundi et vendredi à 18h"),
            {"time": "18:00", "days": [0, 4]},
        )

    def test_requires_a_time(self):
        self.assertIsNone(timeparse.parse_schedule("tous les jours"))
        self.assertIsNone(timeparse.parse_schedule(""))

    def test_roundtrip_from_dict(self):
        schedule = timeparse.parse_schedule("en semaine à 8h30")
        self.assertEqual(timeparse.parse_schedule(schedule), schedule)

    def test_description(self):
        self.assertEqual(
            timeparse.describe_schedule(timeparse.parse_schedule("en semaine à 8h30")),
            "en semaine à 08:30",
        )
        self.assertEqual(timeparse.describe_schedule(None), "aucune")


class TestRecurrence(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(timeparse.parse_recurrence("tous les jours"), "daily")
        self.assertEqual(timeparse.parse_recurrence("chaque semaine"), "weekly")
        self.assertEqual(timeparse.parse_recurrence("en semaine"), "weekdays")
        self.assertEqual(timeparse.parse_recurrence(""), "")

    def test_next_occurrence(self):
        self.assertEqual(
            timeparse.next_occurrence(NOW, "daily"), dt.datetime(2026, 9, 2, 14, 0)
        )
        self.assertIsNone(timeparse.next_occurrence(NOW, ""))

    def test_weekdays_skips_weekend(self):
        friday = dt.datetime(2026, 9, 4, 9, 0)
        self.assertEqual(
            timeparse.next_occurrence(friday, "weekdays"), dt.datetime(2026, 9, 7, 9, 0)
        )


if __name__ == "__main__":
    unittest.main()
