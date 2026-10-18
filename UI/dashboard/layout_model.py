"""Modèle de mise en page du Dashboard (pages, placements, grille) — sans Qt.

La grille canonique compte ``GRID_COLUMNS`` colonnes (12). Chaque widget posé
sur une page est un ``Placement`` : position et taille en unités de grille,
réglages propres à CETTE instance, état masqué. Les états étroit / normal de
la fenêtre ne modifient jamais la mise en page canonique : ils en calculent
une projection (``reflow``) pour l'affichage.

Garanties
---------
* Un placement de type inconnu (widget retiré d'une future version, fichier
  édité à la main…) est **conservé** tel quel ; l'interface affiche un
  emplacement « widget indisponible » au lieu de le supprimer.
* Les clés inconnues d'un placement ou d'une page sont préservées
  (``extras``) et réécrites à l'identique.
* Retirer un widget d'une page ne supprime JAMAIS de données métier : seul
  le placement disparaît.
"""

from __future__ import annotations

import math
import secrets
from dataclasses import dataclass, field
from typing import Any, Iterable

GRID_COLUMNS = 12
MAX_ROWS = 400
MAX_WIDGETS_PER_PAGE = 40
MAX_CUSTOM_PAGES = 12
MAX_TITLE_CHARS = 40

#: Colonnes effectives selon la largeur de la fenêtre.
NARROW_COLUMNS = 4
NORMAL_COLUMNS = 8
WIDE_COLUMNS = 12

#: Seuils de largeur de la ZONE DE GRILLE (pixels logiques) → colonnes.
NARROW_MAX_WIDTH = 760
NORMAL_MAX_WIDTH = 1240

#: Seuils de largeur de la FENÊTRE → état étroit / normal / large (barre
#: latérale compacte, en-tête réduit). Distincts des seuils de grille pour
#: éviter toute oscillation (la barre latérale change la largeur de grille).
WINDOW_NARROW_MAX = 900
WINDOW_NORMAL_MAX = 1480


def columns_for_width(width: int) -> int:
    """Nombre de colonnes affichées pour une largeur de zone de grille."""
    if width < NARROW_MAX_WIDTH:
        return NARROW_COLUMNS
    if width < NORMAL_MAX_WIDTH:
        return NORMAL_COLUMNS
    return WIDE_COLUMNS


def size_class_for_width(window_width: int) -> str:
    """État de la fenêtre : ``narrow`` / ``normal`` / ``wide``."""
    if window_width < WINDOW_NARROW_MAX:
        return "narrow"
    if window_width < WINDOW_NORMAL_MAX:
        return "normal"
    return "wide"


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def new_instance_id(existing: Iterable[str] = ()) -> str:
    taken = set(existing)
    while True:
        candidate = "w-" + secrets.token_hex(3)
        if candidate not in taken:
            return candidate


def new_page_id(existing: Iterable[str] = ()) -> str:
    taken = set(existing)
    while True:
        candidate = "page-" + secrets.token_hex(3)
        if candidate not in taken:
            return candidate


def clean_title(value: Any, fallback: str = "Page") -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return fallback
    return text[:MAX_TITLE_CHARS]


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

_PLACEMENT_KEYS = {"id", "type", "x", "y", "w", "h", "hidden", "settings"}


@dataclass
class Placement:
    id: str
    type: str
    x: int = 0
    y: int = 0
    w: int = 4
    h: int = 4
    hidden: bool = False
    settings: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)

    def rect(self) -> tuple[int, int, int, int]:
        return self.x, self.y, self.w, self.h

    def copy(self) -> "Placement":
        return Placement(
            self.id, self.type, self.x, self.y, self.w, self.h, self.hidden,
            dict(self.settings), dict(self.extras),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extras)
        payload.update(
            {
                "id": self.id,
                "type": self.type,
                "x": self.x,
                "y": self.y,
                "w": self.w,
                "h": self.h,
                "hidden": bool(self.hidden),
                "settings": dict(self.settings),
            }
        )
        return payload

    @classmethod
    def from_dict(cls, raw: Any, *, taken_ids: set[str] | None = None) -> "Placement | None":
        if not isinstance(raw, dict):
            return None
        widget_type = str(raw.get("type") or "").strip()
        if not widget_type:
            return None
        taken = taken_ids if taken_ids is not None else set()
        instance_id = str(raw.get("id") or "").strip()
        if not instance_id or instance_id in taken:
            instance_id = new_instance_id(taken)
        taken.add(instance_id)
        w = _int(raw.get("w"), 4, 1, GRID_COLUMNS)
        h = _int(raw.get("h"), 4, 1, 24)
        x = _int(raw.get("x"), 0, 0, GRID_COLUMNS - 1)
        if x + w > GRID_COLUMNS:
            x = max(0, GRID_COLUMNS - w)
        y = _int(raw.get("y"), 0, 0, MAX_ROWS)
        settings = raw.get("settings") if isinstance(raw.get("settings"), dict) else {}
        extras = {k: v for k, v in raw.items() if k not in _PLACEMENT_KEYS}
        return cls(instance_id, widget_type, x, y, w, h, bool(raw.get("hidden", False)), dict(settings), extras)


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

_PAGE_KEYS = {"id", "title", "builtin", "icon", "widgets"}


@dataclass
class Page:
    id: str
    title: str
    builtin: bool = False
    icon: str = "◇"
    widgets: list[Placement] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)

    def copy(self) -> "Page":
        return Page(self.id, self.title, self.builtin, self.icon,
                    [p.copy() for p in self.widgets], dict(self.extras))

    def visible_widgets(self) -> list[Placement]:
        return [p for p in self.widgets if not p.hidden]

    def find(self, instance_id: str) -> Placement | None:
        for placement in self.widgets:
            if placement.id == instance_id:
                return placement
        return None

    def to_dict(self) -> dict[str, Any]:
        payload = dict(self.extras)
        payload.update(
            {
                "id": self.id,
                "title": self.title,
                "builtin": bool(self.builtin),
                "icon": self.icon,
                "widgets": [p.to_dict() for p in self.widgets],
            }
        )
        return payload

    @classmethod
    def from_dict(cls, raw: Any, *, taken_ids: set[str]) -> "Page | None":
        if not isinstance(raw, dict):
            return None
        page_id = str(raw.get("id") or "").strip()
        if not page_id:
            return None
        widgets: list[Placement] = []
        for item in raw.get("widgets") or []:
            placement = Placement.from_dict(item, taken_ids=taken_ids)
            if placement is not None and len(widgets) < MAX_WIDGETS_PER_PAGE:
                widgets.append(placement)
        extras = {k: v for k, v in raw.items() if k not in _PAGE_KEYS}
        icon = str(raw.get("icon") or "◇")[:2]
        return cls(page_id, clean_title(raw.get("title"), page_id), bool(raw.get("builtin", False)),
                   icon, widgets, extras)


# ---------------------------------------------------------------------------
# Algorithmes de grille
# ---------------------------------------------------------------------------


def overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def clamp_rect(x: int, y: int, w: int, h: int, columns: int,
               min_size: tuple[int, int] = (1, 1),
               max_size: tuple[int, int] | None = None) -> tuple[int, int, int, int]:
    """Contraint un rectangle à la grille et aux tailles min/max du widget."""
    min_w = max(1, min(columns, int(min_size[0])))
    min_h = max(1, int(min_size[1]))
    max_w = columns if max_size is None else max(min_w, min(columns, int(max_size[0])))
    max_h = 24 if max_size is None else max(min_h, int(max_size[1]))
    w = max(min_w, min(max_w, int(w)))
    h = max(min_h, min(max_h, int(h)))
    x = max(0, min(columns - w, int(x)))
    y = max(0, min(MAX_ROWS, int(y)))
    return x, y, w, h


def _first_free_y(placed: list[tuple[int, int, int, int]], x: int, w: int, h: int, start: int = 0) -> int:
    y = max(0, start)
    while y <= MAX_ROWS:
        candidate = (x, y, w, h)
        blocker = None
        for rect in placed:
            if overlaps(candidate, rect):
                blocker = rect
                break
        if blocker is None:
            return y
        y = blocker[1] + blocker[3]
    return MAX_ROWS


def pack(placements: list[Placement], columns: int = GRID_COLUMNS, fixed_id: str | None = None) -> list[Placement]:
    """Place les widgets sans chevauchement, avec gravité vers le haut.

    ``fixed_id`` : widget en cours de déplacement/redimensionnement, posé en
    premier à sa position exacte ; les autres s'écoulent autour de lui dans
    l'ordre de lecture (y, x). Retourne de NOUVEAUX placements (l'entrée
    n'est pas modifiée). Les widgets masqués sont conservés à l'identique.
    """
    result = [p.copy() for p in placements]
    visible = [p for p in result if not p.hidden]
    placed: list[tuple[int, int, int, int]] = []
    fixed = None
    if fixed_id is not None:
        for p in visible:
            if p.id == fixed_id:
                fixed = p
                break
    if fixed is not None:
        fixed.w = max(1, min(columns, fixed.w))
        fixed.x = max(0, min(columns - fixed.w, fixed.x))
        fixed.y = max(0, fixed.y)
        placed.append(fixed.rect())
    others = sorted((p for p in visible if p is not fixed), key=lambda p: (p.y, p.x))
    for p in others:
        p.w = max(1, min(columns, p.w))
        p.x = max(0, min(columns - p.w, p.x))
        p.y = _first_free_y(placed, p.x, p.w, p.h, 0)
        placed.append(p.rect())
    return result


def settle(placements: list[Placement], columns: int = GRID_COLUMNS, fixed_id: str | None = None) -> list[Placement]:
    """Mise en page définitive après un déplacement : placement autour du widget
    manipulé, puis compaction complète (aucun trou, aucun chevauchement)."""
    return pack(pack(placements, columns, fixed_id=fixed_id), columns)


def find_free_slot(placements: list[Placement], w: int, h: int, columns: int = GRID_COLUMNS) -> tuple[int, int]:
    """Premier emplacement libre (ordre de lecture) pour un widget w×h."""
    w = max(1, min(columns, w))
    rects = [p.rect() for p in placements if not p.hidden]
    for y in range(0, MAX_ROWS):
        for x in range(0, columns - w + 1):
            candidate = (x, y, w, h)
            if not any(overlaps(candidate, rect) for rect in rects):
                return x, y
    return 0, MAX_ROWS


def reflow(placements: list[Placement], columns: int,
           min_sizes: dict[str, tuple[int, int]] | None = None) -> list[Placement]:
    """Projection d'une mise en page canonique (12 colonnes) vers ``columns``.

    N'altère pas la mise en page enregistrée : sert uniquement à l'affichage
    étroit / normal. Ordre de lecture conservé, aucune superposition.
    """
    if columns >= GRID_COLUMNS:
        return pack(placements, GRID_COLUMNS)
    min_sizes = min_sizes or {}
    projected: list[Placement] = []
    for p in sorted((q for q in placements), key=lambda q: (q.y, q.x)):
        q = p.copy()
        if not q.hidden:
            min_w = min_sizes.get(q.type, (1, 1))[0]
            if columns <= NARROW_COLUMNS:
                q.w = columns if p.w > 3 else max(1, columns // 2)
            else:
                scaled = round(p.w * columns / GRID_COLUMNS)
                minimum = max(2, math.ceil(min_w * columns / GRID_COLUMNS))
                q.w = max(min(minimum, columns), min(columns, scaled))
            q.x = min(columns - q.w, round(p.x * columns / GRID_COLUMNS))
        projected.append(q)
    return pack(projected, columns)


def has_overlap(placements: list[Placement]) -> bool:
    visible = [p for p in placements if not p.hidden]
    for index, a in enumerate(visible):
        for b in visible[index + 1:]:
            if overlaps(a.rect(), b.rect()):
                return True
    return False


def content_rows(placements: list[Placement]) -> int:
    rows = 0
    for p in placements:
        if not p.hidden:
            rows = max(rows, p.y + p.h)
    return rows
