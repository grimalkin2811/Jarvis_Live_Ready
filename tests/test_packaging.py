"""Tests de validation du packaging Jarvis (structure PyInstaller).

Ces tests vérifient que le système de validation détecte correctement :
- une structure valide
- une structure aplatie (bug python311.dll à la racine)
- une archive ZIP invalide
- une archive ZIP valide

Ils sont utilisés en CI pour prévenir les régressions de type
python311.dll manquant.
"""

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.packaging_validation import (
    CRITICAL_APP_FILES,
    FORBIDDEN_FLATTENED_FILES,
    format_validation_result,
    validate_app_dir,
    validate_install_dir,
    validate_zip,
)
from src.updater import UpdateError, apply_update, validate_extracted_app_structure


def _make_fake_app_dir(base: Path, *, flattened: bool = False, missing_internal: bool = False) -> Path:
    """Crée un faux dossier app pour tests."""
    app_dir = base / "app"
    app_dir.mkdir(parents=True, exist_ok=True)

    if flattened:
        # Bug : python311.dll à la racine
        (app_dir / "Jarvis.exe").write_bytes(b"fake exe")
        (app_dir / "python311.dll").write_bytes(b"fake dll")
        (app_dir / "base_library.zip").write_bytes(b"fake zip")
    elif missing_internal:
        # _internal manquant
        (app_dir / "Jarvis.exe").write_bytes(b"fake exe")
    else:
        # Structure valide
        (app_dir / "Jarvis.exe").write_bytes(b"fake exe")
        internal = app_dir / "_internal"
        internal.mkdir(parents=True, exist_ok=True)
        (internal / "python311.dll").write_bytes(b"fake dll")
        (internal / "base_library.zip").write_bytes(b"fake zip")
        (internal / "PySide6").mkdir(exist_ok=True)

    return app_dir


def _make_zip(zip_path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(zip_path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)


class PackagingValidationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_validate_app_dir_valid(self):
        app_dir = _make_fake_app_dir(self.root, flattened=False)
        is_valid, missing, forbidden = validate_app_dir(app_dir)
        self.assertTrue(is_valid, f"Devrait être valide, mais: missing={missing}, forbidden={forbidden}")
        self.assertEqual(len(missing), 0)
        self.assertEqual(len(forbidden), 0)

    def test_validate_app_dir_flattened(self):
        app_dir = _make_fake_app_dir(self.root, flattened=True)
        is_valid, missing, forbidden = validate_app_dir(app_dir)
        self.assertFalse(is_valid, "Devrait être invalide (flattened)")
        # Doit détecter python311.dll à la racine comme forbidden
        self.assertIn("python311.dll", forbidden)
        # Et _internal/python311.dll comme manquant
        self.assertTrue(any("python311.dll" in m for m in missing))

    def test_validate_app_dir_missing_internal(self):
        app_dir = _make_fake_app_dir(self.root, missing_internal=True)
        is_valid, missing, forbidden = validate_app_dir(app_dir)
        self.assertFalse(is_valid, "Devrait être invalide (_internal manquant)")
        self.assertTrue(len(missing) > 0)

    def test_validate_zip_valid(self):
        zip_path = self.root / "valid.zip"
        _make_zip(
            zip_path,
            {
                "Jarvis.exe": b"fake exe",
                "_internal/python311.dll": b"fake dll",
                "_internal/base_library.zip": b"fake zip",
            },
        )
        is_valid, missing, forbidden = validate_zip(zip_path)
        self.assertTrue(is_valid, f"ZIP valide rejeté: missing={missing}, forbidden={forbidden}")

    def test_validate_zip_flattened(self):
        zip_path = self.root / "flattened.zip"
        _make_zip(
            zip_path,
            {
                "Jarvis.exe": b"fake exe",
                "python311.dll": b"fake dll at root (BAD)",
                "base_library.zip": b"fake zip at root (BAD)",
            },
        )
        is_valid, missing, forbidden = validate_zip(zip_path)
        self.assertFalse(is_valid, "ZIP aplati devrait être invalide")
        self.assertTrue(len(forbidden) > 0, "Devrait détecter flattening")
        self.assertIn("python311.dll", forbidden)

    def test_validate_zip_missing_internal(self):
        zip_path = self.root / "missing_internal.zip"
        _make_zip(
            zip_path,
            {
                "Jarvis.exe": b"fake exe",
                # _internal manquant
            },
        )
        is_valid, missing, forbidden = validate_zip(zip_path)
        self.assertFalse(is_valid, "ZIP sans _internal devrait être invalide")
        self.assertTrue(len(missing) > 0)

    def test_validate_install_dir_valid(self):
        install_dir = self.root / "install"
        install_dir.mkdir()
        app_dir = install_dir / "app"
        app_dir.mkdir()
        (install_dir / "JarvisLauncher.exe").write_bytes(b"fake launcher")
        (install_dir / "version.json").write_text('{"version": "1.0.0"}')
        (app_dir / "Jarvis.exe").write_bytes(b"fake exe")
        internal = app_dir / "_internal"
        internal.mkdir()
        (internal / "python311.dll").write_bytes(b"fake dll")
        (internal / "base_library.zip").write_bytes(b"fake zip")

        is_valid, missing, forbidden = validate_install_dir(install_dir)
        self.assertTrue(is_valid, f"Install valide rejetée: {missing}, {forbidden}")

    def test_validate_install_dir_flattened(self):
        install_dir = self.root / "install_flat"
        install_dir.mkdir()
        app_dir = install_dir / "app"
        app_dir.mkdir()
        (install_dir / "JarvisLauncher.exe").write_bytes(b"fake launcher")
        (install_dir / "version.json").write_text('{"version": "1.0.0"}')
        (app_dir / "Jarvis.exe").write_bytes(b"fake exe")
        # Flattened: dll à la racine de app/
        (app_dir / "python311.dll").write_bytes(b"fake dll at root")

        is_valid, missing, forbidden = validate_install_dir(install_dir)
        self.assertFalse(is_valid, "Install aplatie devrait être invalide")
        self.assertTrue(any("python311.dll" in f for f in forbidden))

    def test_updater_rejects_invalid_zip(self):
        """Test que apply_update rejette une archive invalide."""
        zip_path = self.root / "invalid.zip"
        _make_zip(
            zip_path,
            {
                "Jarvis.exe": b"fake exe",
                "python311.dll": b"flattened dll (BAD)",
            },
        )

        install_dir = self.root / "app"
        install_dir.mkdir()
        (install_dir / "Jarvis.exe").write_bytes(b"old")

        with self.assertRaises(UpdateError) as ctx:
            apply_update(zip_path, install_dir, version="2.0.0", marker_dir=self.root)

        self.assertIn("invalide", str(ctx.exception).lower() or "flatten" in str(ctx.exception).lower() or "manquant" in str(ctx.exception).lower())

        # Vérifie que l'ancienne version est toujours là (rollback)
        self.assertTrue((install_dir / "Jarvis.exe").exists())
        self.assertEqual((install_dir / "Jarvis.exe").read_bytes(), b"old")

    def test_updater_accepts_valid_zip(self):
        """Test que apply_update accepte une archive valide."""
        zip_path = self.root / "valid.zip"
        _make_zip(
            zip_path,
            {
                "Jarvis.exe": b"new exe",
                "_internal/python311.dll": b"new dll",
                "_internal/base_library.zip": b"new zip",
            },
        )

        install_dir = self.root / "app"
        install_dir.mkdir()
        (install_dir / "Jarvis.exe").write_bytes(b"old")
        (install_dir / "_internal").mkdir()
        (install_dir / "_internal" / "python311.dll").write_bytes(b"old dll")

        # Devrait réussir
        result = apply_update(zip_path, install_dir, version="2.0.0", marker_dir=self.root)
        self.assertTrue(result.is_dir())
        self.assertEqual((install_dir / "Jarvis.exe").read_bytes(), b"new exe")
        self.assertTrue((install_dir / "_internal" / "python311.dll").exists())

    def test_validate_extracted_app_structure(self):
        """Test la détection de la racine de l'app dans un dossier extrait."""
        # Cas 1: fichiers à la racine
        stage = self.root / "stage1"
        stage.mkdir()
        (stage / "Jarvis.exe").write_bytes(b"exe")
        (stage / "_internal").mkdir()
        (stage / "_internal" / "python311.dll").write_bytes(b"dll")
        (stage / "_internal" / "base_library.zip").write_bytes(b"zip")

        app_root, is_valid, missing, forbidden = validate_extracted_app_structure(stage)
        self.assertTrue(is_valid)
        self.assertEqual(app_root, stage)

        # Cas 2: un seul dossier contenant l'app
        stage2 = self.root / "stage2"
        stage2.mkdir()
        inner = stage2 / "app"
        inner.mkdir()
        (inner / "Jarvis.exe").write_bytes(b"exe")
        (inner / "_internal").mkdir()
        (inner / "_internal" / "python311.dll").write_bytes(b"dll")
        (inner / "_internal" / "base_library.zip").write_bytes(b"zip")

        app_root2, is_valid2, _, _ = validate_extracted_app_structure(stage2)
        self.assertTrue(is_valid2)
        self.assertEqual(app_root2, inner)

    def test_critical_files_constants(self):
        """Vérifie que les constantes critiques sont bien définies."""
        self.assertIn("Jarvis.exe", CRITICAL_APP_FILES)
        self.assertIn("_internal/python311.dll", CRITICAL_APP_FILES)
        self.assertIn("_internal/base_library.zip", CRITICAL_APP_FILES)
        self.assertIn("python311.dll", FORBIDDEN_FLATTENED_FILES)

    def test_format_validation_result_console_safe(self):
        """Régression run #45 : le rapport doit être encodable en cp1252.

        Le formateur utilisait « ✓ »/« ✗ », non encodables dans la page de
        codes cp1252 de la console Windows du CI : ``UnicodeEncodeError``
        dans scripts/validate_build.py faisait échouer le build même quand
        la structure PyInstaller était valide.
        """
        ok = format_validation_result(True, [], [])
        failure = format_validation_result(False, ["Jarvis.exe"], ["python311.dll"])
        for text in (ok, failure):
            # Doit passer sur toute console Windows ANSI (cp1252 en CI).
            text.encode("cp1252")
            self.assertTrue(text.isascii(), f"Rapport non ASCII: {text!r}")
        self.assertIn("Validation OK", ok)
        self.assertIn("FAIL", failure)
        self.assertIn("Jarvis.exe", failure)


if __name__ == "__main__":
    unittest.main()
