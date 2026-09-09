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
import _ci_diag  # noqa: E402,F401  (diagnostic CI temporaire)

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
        self.assertEqual(local_version(self.root), "1.0.0")
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

    def test_download_verifies_sha256(self):
        import urllib.request

        dest = self.root / "file.bin"
        fake = self.root / "src.bin"
        fake.write_bytes(b"payload")

        payload = fake.read_bytes()

        def fake_urlopen(req, timeout=None):
            class Resp:
                def __init__(self):
                    self._served = False

                def read(self, size=None):
                    if self._served:
                        return b""
                    self._served = True
                    return payload

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return Resp()

        digest = sha256_file(fake)
        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            out = download("https://example.com/x", dest, sha256=digest)
        self.assertEqual(out.read_bytes(), b"payload")

        # Empreinte erronée -> UpdateError et destination intacte.
        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            with self.assertRaises(UpdateError):
                download("https://example.com/x", dest, sha256="0" * 64)

    def test_apply_update_atomic_and_rollback(self):
        install_dir = self.root / "app"
        install_dir.mkdir(parents=True)
        (install_dir / "Jarvis.exe").write_bytes(b"old")
        marker_dir = self.root

        # Archive contenant la nouvelle version de l'app.
        zip_path = self.root / "new.zip"
        _make_zip(
            zip_path,
            {
                "Jarvis.exe": b"new-exe",
                "_internal/data.txt": b"new-internal",
            },
        )

        apply_update(zip_path, install_dir, version="2.0.0", marker_dir=marker_dir)

        self.assertEqual((install_dir / "Jarvis.exe").read_bytes(), b"new-exe")
        self.assertTrue((install_dir / "_internal").is_dir())
        marker = json.loads((marker_dir / "version.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["version"], "2.0.0")
        self.assertEqual(local_version(marker_dir), "2.0.0")

    def test_rollback_restores_previous(self):
        install_dir = self.root / "app"
        install_dir.mkdir(parents=True)
        (install_dir / "Jarvis.exe").write_bytes(b"old")

        zip_path = self.root / "new.zip"
        _make_zip(zip_path, {"Jarvis.exe": b"new-exe"})
        apply_update(zip_path, install_dir, version="2.0.0", marker_dir=self.root)
        self.assertEqual((install_dir / "Jarvis.exe").read_bytes(), b"new-exe")

        restored = rollback(install_dir)
        self.assertIsNotNone(restored)
        self.assertEqual((install_dir / "Jarvis.exe").read_bytes(), b"old")

    @patch("src.updater.check_for_update")
    def test_build_update_plan(self, mock_check):
        mock_check.return_value = (
            "1.0.0",
            {
                "tag_name": "v1.1.0",
                "assets": [
                    {"name": "Jarvis-v1.1.0-portable.zip", "browser_download_url": "u"},
                    {"name": "Jarvis-v1.1.0-portable.zip.sha256", "browser_download_url": "s"},
                ],
            },
        )
        plan = build_update_plan(current="1.0.0")
        self.assertEqual(plan["current"], "1.0.0")
        self.assertEqual(plan["latest"], "1.1.0")
        self.assertTrue(plan["update_available"])
        self.assertIsNotNone(plan["asset"])
        self.assertIsNotNone(plan["asset_sha256"])

    def test_is_app_running_on_non_windows(self):
        self.assertFalse(updater.is_app_running(self.root))


if __name__ == "__main__":
    unittest.main()
