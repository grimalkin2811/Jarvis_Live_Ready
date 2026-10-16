"""Glyphes de l'interface rendus avec une police de SYMBOLES comme police principale.

Correctif D01 (validation Windows PR #50) : sous Windows, un symbole absent de
la police principale d'un texte (⌂ ◈ ◎ ☑ ⚙ ✎ ▤ ▦ ◫ ⤢ ✉ ⋯ ✕ ne sont pas dans
Segoe UI) fait construire à Qt la liste de repli des polices du système — 155
fichiers de police chargés, 3,5 s à froid sur un Windows natif — et une police
à plusieurs familles déclenche la même chose ; les familles secondaires ne sont
pas consultées avant. Un glyphe dessiné avec la police de symboles comme
police PRINCIPALE ne demande aucun repli (une seule police chargée).

* ``glyph_icon`` : icône peinte (boutons « symbole + libellé ») ;
* ``mark`` : élément qui n'affiche qu'un glyphe (feuille de style ``glyph``) ;
* ``span`` / ``rich_title`` : glyphe dans un texte riche (libellés, HTML).
"""

from __future__ import annotations

import html

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPixmap

from .theme import symbol_family

_ICONS: dict[tuple, QIcon] = {}


def glyph_font(pixel_size: int) -> QFont:
    family = symbol_family()
    font = QFont(family) if family else QFont()
    font.setPixelSize(max(6, int(pixel_size)))
    return font


def glyph_icon(symbol: str, color: QColor, size: int = 16, dpr: float = 2.0) -> QIcon:
    """Icône du glyphe ``symbol`` (couleur du texte), mise en cache."""
    key = (symbol, QColor(color).rgba(), int(size), float(dpr), symbol_family())
    icon = _ICONS.get(key)
    if icon is None:
        pixmap = QPixmap(max(1, round(size * dpr)), max(1, round(size * dpr)))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.TextAntialiasing, True)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(QColor(color))
        painter.setFont(glyph_font(round(size * 0.92)))
        painter.drawText(QRectF(0, 0, size, size), Qt.AlignCenter, symbol)
        painter.end()
        icon = QIcon(pixmap)
        _ICONS[key] = icon
    return icon


def mark(widget):
    """L'élément n'affiche qu'un glyphe : police de symboles principale (feuille ``glyph``)."""
    widget.setProperty("glyph", True)
    return widget


def span(symbol: str) -> str:
    """Glyphe pour un texte riche (HTML), dans la police de symboles."""
    family = symbol_family()
    escaped = html.escape(symbol)
    if not symbol or not family:
        return escaped
    return f"<span style=\"font-family:'{family}'\">{escaped}</span>"


def rich_title(symbol: str, text: str) -> str:
    """« glyphe  titre » en texte riche (en-têtes de section)."""
    title = html.escape(text)
    return f"{span(symbol)}&nbsp;&nbsp;{title}" if symbol else title
