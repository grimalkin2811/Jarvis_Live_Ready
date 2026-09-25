"""Fournisseurs musicaux (Deezer, …)."""

from __future__ import annotations

from .base import MusicProvider
from .deezer import DeezerProvider

__all__ = ["DeezerProvider", "MusicProvider"]
