"""Tests du système de mise à jour (`src/updater.py`)."""

import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import updater  # noqa: E402
from src.updater import (  # noqa: E402
    UpdateError,
    apply_update,
    build_update_plan,
    download,
    find_asset,
    find_asset_sha256,
    local_version,
    release_version,
    rollback,
    sha256_file,
    verify_sha256,
    write_version_file,
)
from src.version import get_version  # noqa: E402


def _make_zip(zip_path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(zip_path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_local_version_marker(self):
        # Pas de version.json -> retombe sur la version embarquée.
        self.assertEqual(local_version(self.root), get_version())
        write_version_file("1.2.0", self.root)
        self.assertEqual(local_version(self.root), "1.2.0")

    def test_release_version(self):
        self.assertEqual(release_version({"tag_name": "v1.2.3"}), "1.2.3")
        self.assertEqual(release_version({"tag_name": "1.4.0"}), "1.4.0")

    def test_find_asset(self):
        release = {
            "assets": [
                {"name": "Jarvis-v1.2.3-portable.zip", "browser_download_url": "u"},
                {"name": "Jarvis-v1.2.3-portable.zip.sha256", "browser_download_url": "s"},
                {"name": "JarvisSetup-1.2.3.exe", "browser_download_url": "x"},
            ]
        }
        self.assertEqual(find_asset(release, "1.2.3")["name"], "Jarvis-v1.2.3-portable.zip")
        self.assertEqual(find_asset_sha256(release, "1.2.3")["name"], "Jarvis-v1.2.3-portable.zip.sha256")

    def test_sha256_file_and_verify(self):
        f = self.root / "data.bin"
        f.write_bytes(b"hello world")
        digest = sha256_file(f)
        self.assertTrue(verify_sha256(f, digest))
        self.assertFalse(verify_sha256(f, "0" * 64))
