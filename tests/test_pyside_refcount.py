"""Garde-fou : PySide6 ne doit pas fuir de références sur ``None``.

PySide6 6.12.0 libère une référence à ``None`` à chaque appel d'une méthode
qui ne renvoie rien (``QPointF.setX``, ``QPainter.save``…). Avant Python 3.12,
``None`` est un objet compté comme les autres : le compteur finit à zéro et
l'interpréteur s'arrête (« Fatal Python error: none_dealloc »). Constaté dans
ce dépôt : la suite de tests s'arrêtait net (code 134) dans les tests de
l'orbe, et toute interface qui peint en continu (orbe, Desktop, Dashboard)
était exposée. ``requirements.txt`` exclut donc PySide6 6.12 ; ce test
échoue si une version fautive est installée.
"""

from __future__ import annotations

import os
import sys

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="None est immortel à partir de Python 3.12")
def test_void_methods_do_not_leak_none_references():
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QImage, QPainter

    point = QPointF(1.0, 2.0)
    rect = QRectF(0.0, 0.0, 1.0, 1.0)
    image = QImage(4, 4, QImage.Format_ARGB32)
    painter = QPainter(image)
    try:
        before = sys.getrefcount(None)
        for index in range(500):
            point.setX(float(index))
            rect.setWidth(2.0)
            painter.save()
            painter.restore()
        drift = sys.getrefcount(None) - before
    finally:
        painter.end()
    assert drift > -50, f"PySide6 fuit des références sur None ({drift}) : version incompatible (voir requirements.txt)"
