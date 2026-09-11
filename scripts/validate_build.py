#!/usr/bin/env python3
"""Validation de build Jarvis - utilisé par build_windows.ps1 et CI.

Usage:
    python scripts/validate_build.py --app-dir dist/app
    python scripts/validate_build.py --zip dist/Jarvis-v1.0.1-portable.zip
    python scripts/validate_build.py --install-dir /tmp/jarvis-install
    python scripts/validate_build.py --pyinstaller dist/Jarvis
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Permet d'importer src/ depuis scripts/
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.logging_setup import force_utf8_stdio
from src.packaging_validation import (
    format_validation_result,
    validate_app_dir,
    validate_install_dir,
    validate_zip,
)

# Un script de validation ne doit JAMAIS mourir sur son propre log :
# sous Windows la console peut être en cp1252/cp850, incapable d'encoder
# certains caractères. On force un stdout/stderr UTF-8 sans plantage.
force_utf8_stdio()


def _validate_app_dir(path: Path, strict: bool = False) -> int:
    print(f"=== Validating application layout: {path} ===")
    is_valid, missing, forbidden = validate_app_dir(path, strict=strict)
    print(format_validation_result(is_valid, missing, forbidden))
    if is_valid:
        # Logs détaillés des fichiers critiques
        for rel in ["Jarvis.exe", "_internal/python311.dll", "_internal/base_library.zip"]:
            target = path / rel
            status = "OK" if target.exists() else "MISSING"
            print(f"  {rel}: {status}")
        # Vérifie aussi qu'il n'y a pas de flatten
        print(f"  Checking for flattened files (should NOT exist at root)...")
        for forbidden_name in ["python311.dll", "base_library.zip"]:
            exists = (path / forbidden_name).exists()
            print(f"    {forbidden_name} at root: {'FOUND (BAD)' if exists else 'OK (not present)'}")
        return 0
    else:
        print(f"ERROR: Expected file missing or flattened structure detected in {path}")
        if missing:
            for m in missing:
                print(f"  MISSING: {m}")
        if forbidden:
            for f in forbidden:
                print(f"  FLATTENED (should be in _internal/): {f}")
        return 1


def _validate_zip(path: Path) -> int:
    print(f"=== Validating portable ZIP: {path} ===")
    is_valid, missing, forbidden = validate_zip(path)
    print(format_validation_result(is_valid, missing, forbidden))
    if is_valid:
        import zipfile

        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()
            print(f"  ZIP contains {len(names)} entries")
            # Affiche quelques entrées critiques
            for critical in ["Jarvis.exe", "_internal/python311.dll", "_internal/base_library.zip"]:
                found = any(n.replace("\\", "/").endswith(critical) or n.replace("\\", "/") == critical for n in names)
                print(f"  {critical}: {'OK' if found else 'MISSING'}")
        return 0
    else:
        print(f"ERROR: ZIP validation failed for {path}")
        return 1


def _validate_install_dir(path: Path) -> int:
    print(f"=== Validating installed application: {path} ===")
    is_valid, missing, forbidden = validate_install_dir(path)
    print(format_validation_result(is_valid, missing, forbidden))
    if is_valid:
        print(f"  app/Jarvis.exe: OK")
        print(f"  app/_internal/python311.dll: OK")
        return 0
    else:
        print(f"ERROR: Installation validation failed for {path}")
        return 1


def _validate_pyinstaller(path: Path) -> int:
    print(f"=== Validating PyInstaller output: {path} ===")
    return _validate_app_dir(path, strict=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Valide la structure de build Jarvis")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--app-dir", type=Path, help="Valide dist/app/")
    group.add_argument("--pyinstaller", type=Path, help="Valide dist/Jarvis/ (sortie PyInstaller)")
    group.add_argument("--zip", type=Path, help="Valide le ZIP portable")
    group.add_argument("--install-dir", type=Path, help="Valide une installation complète")
    parser.add_argument("--strict", action="store_true", help="Vérifie aussi les fichiers recommandés")
    args = parser.parse_args()

    if args.app_dir:
        return _validate_app_dir(args.app_dir, strict=args.strict)
    if args.pyinstaller:
        return _validate_pyinstaller(args.pyinstaller)
    if args.zip:
        return _validate_zip(args.zip)
    if args.install_dir:
        return _validate_install_dir(args.install_dir)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
