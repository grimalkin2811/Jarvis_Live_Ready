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
        # Doit contenir les fichiers critiques pour passer la validation.
        zip_path = self.root / "new.zip"
        _make_zip(
            zip_path,
            {
                "Jarvis.exe": b"new-exe",
                "_internal/python311.dll": b"fake-dll",
                "_internal/base_library.zip": b"fake-base",
                "_internal/data.txt": b"new-internal",
            },
        )

        apply_update(zip_path, install_dir, version="2.0.0", marker_dir=marker_dir)

        self.assertEqual((install_dir / "Jarvis.exe").read_bytes(), b"new-exe")
        self.assertTrue((install_dir / "_internal").is_dir())
        self.assertTrue((install_dir / "_internal" / "python311.dll").exists())
        marker = json.loads((marker_dir / "version.json").read_text(encoding="utf-8"))
        self.assertEqual(marker["version"], "2.0.0")
        self.assertEqual(local_version(marker_dir), "2.0.0")

    def test_rollback_restores_previous(self):
        install_dir = self.root / "app"
        install_dir.mkdir(parents=True)
        (install_dir / "Jarvis.exe").write_bytes(b"old")

        zip_path = self.root / "new.zip"
        _make_zip(
            zip_path,
            {
                "Jarvis.exe": b"new-exe",
                "_internal/python311.dll": b"fake-dll",
                "_internal/base_library.zip": b"fake-base",
            },
        )
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

    def test_apply_update_rejects_invalid_zip(self):
        """Une archive invalide (flattened) doit être rejetée."""
        install_dir = self.root / "app"
        install_dir.mkdir(parents=True)
        (install_dir / "Jarvis.exe").write_bytes(b"old")

        # Archive aplatie : python311.dll à la racine au lieu de _internal/
        zip_path = self.root / "invalid.zip"
        _make_zip(
            zip_path,
            {
                "Jarvis.exe": b"new-exe",
                "python311.dll": b"flattened dll (BAD)",
            },
        )

        with self.assertRaises(UpdateError):
            apply_update(zip_path, install_dir, version="2.0.0", marker_dir=self.root)

        # L'ancienne version doit toujours être là
        self.assertEqual((install_dir / "Jarvis.exe").read_bytes(), b"old")

    def test_apply_update_rejects_missing_internal(self):
        """Une archive sans _internal doit être rejetée."""
        install_dir = self.root / "app"
        install_dir.mkdir(parents=True)
        (install_dir / "Jarvis.exe").write_bytes(b"old")

        zip_path = self.root / "no_internal.zip"
        _make_zip(
            zip_path,
            {
                "Jarvis.exe": b"new-exe",
                # _internal manquant
            },
        )

        with self.assertRaises(UpdateError):
            apply_update(zip_path, install_dir, version="2.0.0", marker_dir=self.root)

        self.assertEqual((install_dir / "Jarvis.exe").read_bytes(), b"old")

    def test_download_reports_progress(self):
        """Le rappel de progression reçoit (recus, total) sans casser le flux."""
        import urllib.request

        dest = self.root / "file.bin"
        payload = b"0123456789abcdef"  # 16 octets en 2 morceaux

        def fake_urlopen(req, timeout=None):
            class Resp:
                def __init__(self):
                    self._chunks = [payload[:10], payload[10:]]

                def getheader(self, name):
                    if name == "Content-Length":
                        return str(len(payload))
                    return None

                def read(self, size=None):
                    if not self._chunks:
                        return b""
                    return self._chunks.pop(0)

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return Resp()

        calls: list[tuple[int, int | None]] = []
        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            out = download(
                "https://example.com/x", dest, progress=lambda r, t: calls.append((r, t))
            )
        self.assertEqual(out.read_bytes(), payload)
        self.assertTrue(calls)
        self.assertEqual(calls[0], (0, 16))
        self.assertEqual(calls[-1], (16, 16))
        received_values = [r for r, _ in calls]
        self.assertEqual(received_values, sorted(received_values))
        self.assertTrue(all(total == 16 for _, total in calls))

    def test_download_progress_unknown_total(self):
        """Sans Content-Length, le total vaut None mais les recus avancent."""
        import urllib.request

        dest = self.root / "file.bin"

        def fake_urlopen(req, timeout=None):
            class Resp:
                def __init__(self):
                    self._served = False

                def getheader(self, name):
                    return None

                def read(self, size=None):
                    if self._served:
                        return b""
                    self._served = True
                    return b"payload"

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return Resp()

        calls: list[tuple[int, int | None]] = []
        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            download("https://example.com/x", dest, progress=lambda r, t: calls.append((r, t)))
        self.assertTrue(calls)
        self.assertTrue(all(total is None for _, total in calls))
        self.assertEqual(calls[-1][0], 7)

    def test_download_progress_exception_is_ignored(self):
        """Un rappel défaillant ne doit pas interrompre le téléchargement."""
        import urllib.request

        dest = self.root / "file.bin"

        def fake_urlopen(req, timeout=None):
            class Resp:
                def __init__(self):
                    self._served = False

                def getheader(self, name):
                    return None

                def read(self, size=None):
                    if self._served:
                        return b""
                    self._served = True
                    return b"payload"

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

            return Resp()

        def bad_progress(received, total):
            raise RuntimeError("callback HS")

        with patch.object(urllib.request, "urlopen", side_effect=fake_urlopen):
            out = download("https://example.com/x", dest, progress=bad_progress)
        self.assertEqual(out.read_bytes(), b"payload")


if __name__ == "__main__":
    unittest.main()
