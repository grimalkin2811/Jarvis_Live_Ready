"""Génère l'icône ``assets/jarvis.ico`` (un orbe bleu) sans dépendance externe.

Utilisé par le build local et par la CI (PyInstaller + Inno Setup). L'icône est
générée à partir de zéro (PNG embarqué dans un conteneur ICO, format supporté
par Windows Vista+ et par PyInstaller).
"""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

#: Taille de l'icône (carré).
SIZE = 256


def _px(x: int, y: int) -> tuple[int, int, int, int]:
    """Pixel de l'orbe : dégradé radial bleu sur fond transparent."""
    cx = SIZE / 2.0
    cy = SIZE / 2.0
    dx = x + 0.5 - cx
    dy = y + 0.5 - cy
    dist = (dx * dx + dy * dy) ** 0.5
    radius = SIZE / 2.0 - 16
    if dist > radius:
        return (0, 0, 0, 0)
    # Atténuation douce au bord.
    edge = max(0.0, min(1.0, (radius - dist) / 40.0))
    # Dégradé : cœur clair -> bord bleu profond.
    t = dist / radius
    base = (40, 150, 255)
    hi = (200, 240, 255)
    r = int(hi[0] + (base[0] - hi[0]) * t)
    g = int(hi[1] + (base[1] - hi[1]) * t)
    b = int(hi[2] + (base[2] - hi[2]) * t)
    a = int(255 * edge)
    return (r, g, b, a)


def _chunk(tag: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + tag
        + data
        + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    )


def _png() -> bytes:
    """Construit un PNG RGBA non entrelacé."""
    raw = b""
    for y in range(SIZE):
        row = b"\x00"
        for x in range(SIZE):
            row += bytes(_px(x, y))
        raw += row
    ihdr = struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def _ico() -> bytes:
    """Construit un conteneur ICO contenant une image PNG 256x256."""
    png = _png()
    # En-tête ICO (6 octets) + entrée de répertoire (16 octets).
    header = struct.pack("<HHH", 0, 1, 1)
    # width/height : 0 signifie 256 dans le format ICO.
    entry = struct.pack(
        "<BBBBHHII",
        0,
        0,
        0,
        0,
        1,
        32,
        len(png),
        22,  # offset des données (6 + 16)
    )
    return header + entry + png


def main() -> None:
    target = Path(__file__).resolve().parents[1] / "assets" / "jarvis.ico"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(_ico())
    print(f"Icône générée : {target} ({target.stat().st_size} octets)")


if __name__ == "__main__":
    main()
