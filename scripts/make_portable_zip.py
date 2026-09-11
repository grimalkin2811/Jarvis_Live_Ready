#!/usr/bin/env python3
"""Création robuste de l'archive portable Jarvis.

Remplace Compress-Archive PowerShell qui peut avoir des comportements
incohérents selon les versions. Utilise zipfile Python qui garantit la
préservation de l'arborescence _internal/.

Usage:
    python scripts/make_portable_zip.py --app-dir dist/app --output dist/Jarvis-v1.0.1-portable.zip
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.logging_setup import force_utf8_stdio
from src.packaging_validation import validate_app_dir

# Script exécuté en CI Windows (console cp1252) : force un affichage sans
# risque d'UnicodeEncodeError quel que soit le texte loggé.
force_utf8_stdio()


def create_portable_zip(app_dir: Path, output_zip: Path) -> None:
    app_dir = app_dir.resolve()
    output_zip = output_zip.resolve()

    # Validation préalable
    is_valid, missing, forbidden = validate_app_dir(app_dir)
    if not is_valid:
        print(f"ERROR: app dir invalide {app_dir}")
        for m in missing:
            print(f"  MISSING: {m}")
        for f in forbidden:
            print(f"  FLATTENED: {f}")
        raise RuntimeError(f"App dir invalide: {app_dir}")

    if output_zip.exists():
        output_zip.unlink()

    output_zip.parent.mkdir(parents=True, exist_ok=True)

    print(f"Creating portable ZIP: {output_zip}")
    print(f"  Source: {app_dir}")

    # Crée le ZIP avec l'arborescence exacte
    with zipfile.ZipFile(output_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        count = 0
        for file_path in app_dir.rglob("*"):
            if file_path.is_file():
                # Chemin relatif à app_dir, doit être préservé
                rel = file_path.relative_to(app_dir)
                # Utilise / comme séparateur dans le ZIP (standard)
                arcname = str(rel).replace("\\", "/")
                zf.write(file_path, arcname)
                count += 1
                if count % 500 == 0:
                    print(f"  ... {count} files added")

        print(f"  Total files: {count}")

    # Validation du ZIP créé
    from src.packaging_validation import validate_zip

    is_valid, missing, forbidden = validate_zip(output_zip)
    if not is_valid:
        print(f"ERROR: ZIP créé invalide {output_zip}")
        for m in missing:
            print(f"  MISSING: {m}")
        for f in forbidden:
            print(f"  FLATTENED: {f}")
        raise RuntimeError(f"ZIP invalide après création: {output_zip}")

    print(f"[OK] ZIP créé et validé: {output_zip} ({output_zip.stat().st_size} bytes)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Crée l'archive portable Jarvis")
    parser.add_argument("--app-dir", type=Path, required=True, help="Dossier app à archiver (dist/app)")
    parser.add_argument("--output", type=Path, required=True, help="Chemin du ZIP à créer")
    args = parser.parse_args()

    try:
        create_portable_zip(args.app_dir, args.output)
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
