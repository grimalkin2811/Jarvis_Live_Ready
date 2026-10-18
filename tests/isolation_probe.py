"""Sonde lancée par ``python -m unittest tests.isolation_probe`` (voir test_unittest_isolation.py).

Nom volontairement hors du motif ``test_*`` : pytest ne la collecte pas.
"""

import os
import unittest

from tests._isolation import REAL_PATH_VARIABLES


class IsolationProbe(unittest.TestCase):
    def test_real_data_is_never_used(self):
        root = os.environ.get("JARVIS_DATA_DIR", "")
        self.assertTrue(os.path.basename(root).startswith("jarvis-tests-data-"), root)
        for variable in REAL_PATH_VARIABLES:
            self.assertNotIn(variable, os.environ)
        from src import paths

        self.assertTrue(os.path.realpath(str(paths.data_dir())).startswith(os.path.realpath(root)))


if __name__ == "__main__":
    unittest.main()
