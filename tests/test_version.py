"""Tests de la source unique de version (`src/version.py`)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.version import (  # noqa: E402
    __version__,
    compare_versions,
    get_version,
    is_newer,
    version_tuple,
)


class VersionTests(unittest.TestCase):
    def test_version_format(self):
        parts = get_version().split(".")
        self.assertEqual(len(parts), 3)
        for p in parts:
            self.assertTrue(p.isdigit())

    def test_version_tuple(self):
        self.assertEqual(version_tuple("1.2.3"), (1, 2, 3))
        self.assertEqual(version_tuple("1.2"), (1, 2, 0))
        self.assertEqual(version_tuple("1.2.3-beta"), (1, 2, 3))

    def test_compare(self):
        self.assertEqual(compare_versions("1.0.0", "1.0.0"), 0)
        self.assertEqual(compare_versions("1.0.0", "1.2.0"), -1)
        self.assertEqual(compare_versions("2.0.0", "1.9.9"), 1)

    def test_is_newer(self):
        self.assertTrue(is_newer("1.3.0", "1.2.3"))
        self.assertFalse(is_newer("1.2.3", "1.2.3"))
        self.assertFalse(is_newer("1.1.0", "1.2.0"))

    def test_root_reexport(self):
        # Le module racine version.py re-exporte la source de vérité.
        import version as root_version

        self.assertEqual(root_version.__version__, __version__)
        self.assertEqual(root_version.get_version(), get_version())


if __name__ == "__main__":
    unittest.main()
