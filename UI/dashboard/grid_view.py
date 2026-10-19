"""Grille d'une page : positionnement responsive, édition, activation."""

from __future__ import annotations

import time

from collections.abc import Callable

from PySide6.QtCore import QPoint, QRect, QTimer, Signal
from PySide6.QtWidgets import QSizePolicy, QWidget

from .frame import WidgetFrame
from .layout_model import (
    GRID_COLUMNS,
    Page,
    Placement,
    clamp_rect,
    columns_for_width,
    content_rows,
    pack,
    reflow,
    settle,
)


class GridCanvas(QWidget):
    """Affiche les placements d'une page sous forme de cadres positionnés."""

    layout_edited = Signal()
    settings_requested = Signal(object)  # Placement
    hide_requested = Signal(object)
    remove_requested = Signal(object)

    #: Intervalle entre deux activations de widgets (ms). L'activation exécute
    #: le premier rafraîchissement du widget ; activer toute une page d'un bloc
    #: gelait la boucle Qt (C10, phase « pages » : retour sur une page déjà
    #: construite = 7 activations mesurées à 139 ms sous charge). Une seule
    #: activation par tour de boucle borne la latence au coût d'un widget.
    ACTIVATION_TICK_MS = 5

    def __init__(self, page: Page, registry, theme_manager, context_factory: Callable[[Placement], object],
                 parent: QWidget | None = None, *, build: bool = True) -> None:
        super().__init__(parent)
        self.page = page
        self.registry = registry
        self.theme_manager = theme_manager
        self.context_factory = context_factory
        self.frames: dict[str, WidgetFrame] = {}
        self.edit_mode = False
        self._active = False
        self._columns = GRID_COLUMNS
        self._display: dict[str, tuple[int, int, int, int]] = {}
        self._drag_origin: tuple[QPoint, Placement] | None = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.built = False
        # Activation DIFFÉRÉE (C10) : file d'attente traitée un widget par
        # tour de boucle. ``_wanted`` = widgets qui DOIVENT être actifs
        # (page visible, ou zone visible en OnePage).
        self._wanted: set = set()
        self._activation_queue: list = []
        self._visibility_filtered = False
        self._activation_timer = QTimer(self)
        self._activation_timer.setSingleShot(True)
        self._activation_timer.setInterval(self.ACTIVATION_TICK_MS)
        self._activation_timer.timeout.connect(self._activate_next)
        if build:
            self.rebuild()
        else:
            # Hauteur provisoire réaliste (construction différée, OnePage).
            from .layout_model import content_rows as _rows

            row_h = self.theme_manager.theme.row_height + self.theme_manager.theme.gap
            self.setMinimumHeight(max(row_h * 2, _rows(self.page.widgets) * row_h))

    # -- construction ----------------------------------------------------------------

    def rebuild(self) -> None:
        """(Re)crée les cadres manquants ; détruit ceux des placements disparus."""
        self.built = True
        wanted = {p.id: p for p in self.page.widgets if not p.hidden}
        for instance_id in list(self.frames):
            if instance_id not in wanted:
                self._destroy_frame(instance_id)
        for instance_id, placement in wanted.items():
            frame = self.frames.get(instance_id)
            if frame is None:
                frame = self._create_frame(placement)
                self.frames[instance_id] = frame
            else:
                frame.placement = placement
        self.relayout()

    def build_step(self, budget_ms: float = 12.0) -> bool:
        """Crée des cadres manquants dans la limite d'un budget de temps.

        Construction INCRÉMENTALE (OnePage, correctif C10 — validation Windows
        PR #50) : une section entière construite d'un bloc gelait la boucle Qt
        (premier affichage OnePage 179 ms sur le Pentium G5400, seuil de
        latence 150 ms). Au moins un cadre est créé à chaque appel (progrès
        garanti). Résultat final identique à ``rebuild()``, qui est appelé pour
        finaliser. Retourne True quand la page est complète.
        """
        deadline = time.perf_counter() + max(0.0, budget_ms) / 1000.0
        wanted = [p for p in self.page.widgets if not p.hidden]
        created = False
        for placement in wanted:
            if placement.id in self.frames:
                continue
            self.frames[placement.id] = self._create_frame(placement)
            created = True
            if time.perf_counter() >= deadline:
                break
        if all(p.id in self.frames for p in wanted):
            self.rebuild()  # finalisation : cadres disparus retirés, disposition
            return True
        if created:
            self.relayout()
        return False

    def _create_frame(self, placement: Placement) -> WidgetFrame:
        spec = self.registry.spec(placement.type)
        widget = None
        if spec is not None:
            # Ne JAMAIS normaliser ``placement.settings`` ici : la construction
            # d'une vue ne doit pas modifier la configuration persistée. Le
            # registre valide déjà les réglages pour la fabrique du widget
            # (copie), et les points d'écriture légitimes (ajout, dialogue de
            # réglages, rechargement) les valident eux-mêmes. Cette mutation
            # tardive rendait l'instantané « Annuler » du mode édition
            # dépendant du moment où la construction différée s'exécutait
            # (échec Windows : {} avant, valeurs par défaut après).
            widget = self.registry.create(placement, self.context_factory(placement))
        frame = WidgetFrame(placement, spec, widget, self.theme_manager, self)
        frame.set_edit_mode(self.edit_mode)
        frame.move_started.connect(self._on_move_started)
        frame.move_dragged.connect(self._on_move_dragged)
        frame.move_finished.connect(self._on_move_finished)
        frame.resize_started.connect(self._on_move_started)
        frame.resize_dragged.connect(self._on_resize_dragged)
        frame.resize_finished.connect(self._on_resize_finished)
        frame.settings_requested.connect(lambda f: self.settings_requested.emit(f.placement))
        frame.hide_requested.connect(lambda f: self.hide_requested.emit(f.placement))
        frame.remove_requested.connect(lambda f: self.remove_requested.emit(f.placement))
        frame.show()
        if self._active and widget is not None and not self._visibility_filtered:
            self._want(widget)
        return frame

    def _destroy_frame(self, instance_id: str) -> None:
        frame = self.frames.pop(instance_id, None)
        if frame is None:
            return
        if frame.widget is not None:
            self._wanted.discard(frame.widget)
            self._activation_queue = [w for w in self._activation_queue if w is not frame.widget]
            frame.widget.dispose()
        frame.hide()
        frame.setParent(None)
        frame.deleteLater()

    def dispose(self) -> None:
        self._activation_timer.stop()
        self._activation_queue.clear()
        self._wanted.clear()
        for instance_id in list(self.frames):
            self._destroy_frame(instance_id)

    # -- activation ----------------------------------------------------------------

    def _want(self, widget) -> None:
        """Demande l'activation d'un widget, sans bloquer le tour courant."""
        if widget is None or widget.is_active:
            return
        self._wanted.add(widget)
        if widget not in self._activation_queue:
            self._activation_queue.append(widget)
        if not self._activation_timer.isActive():
            self._activation_timer.start()

    def _activate_next(self) -> None:
        while self._activation_queue:
            widget = self._activation_queue.pop(0)
            if widget in self._wanted and not widget.is_active:
                widget.activate()
                break
        if self._activation_queue:
            self._activation_timer.start()

    def activation_pending(self) -> bool:
        """Reste-t-il des widgets à activer (contenu réellement « prêt ») ?"""
        return bool(self._activation_queue)

    def flush_activations(self) -> None:
        """Active immédiatement les widgets demandés (accès programmatique complet).

        Le chemin normal reste incrémental (C10) ; ``build_all()`` est le
        contrat explicite « tout, tout de suite » (tests, capture, inspection).
        """
        self._activation_timer.stop()
        pending = [w for w in self._activation_queue if w in self._wanted and not w.is_active]
        self._activation_queue.clear()
        for widget in pending:
            widget.activate()

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        widgets = [f.widget for f in self.frames.values() if f.widget is not None]
        if self._active:
            self._visibility_filtered = False
            for widget in widgets:
                self._want(widget)
        else:
            self._visibility_filtered = False
            self._wanted.clear()
            self._activation_queue.clear()
            self._activation_timer.stop()
            for widget in widgets:
                widget.deactivate()

    def update_visibility(self, visible: QRect | None) -> None:
        """OnePage : seuls les widgets dans (ou près de) la zone visible sont actifs."""
        if visible is None:
            self.set_active(False)
            return
        self._active = True
        self._visibility_filtered = True
        margin = 200
        zone = visible.adjusted(0, -margin, 0, margin)
        self._wanted = set()
        for frame in self.frames.values():
            widget = frame.widget
            if widget is None:
                continue
            if frame.geometry().intersects(zone):
                self._wanted.add(widget)
            else:
                widget.deactivate()
        # Un widget sorti de la zone visible ne doit pas être activé ensuite.
        self._activation_queue = [w for w in self._activation_queue if w in self._wanted]
        for widget in list(self._wanted):
            self._want(widget)

    def active_widget_count(self) -> int:
        return sum(1 for f in self.frames.values() if f.widget is not None and f.widget.is_active)

    # -- géométrie -------------------------------------------------------------------

    @property
    def columns(self) -> int:
        return self._columns

    def _metrics(self) -> tuple[int, float, int]:
        theme = self.theme_manager.theme
        gap = theme.gap
        width = max(200, self.width())
        cell = (width - (self._columns - 1) * gap) / self._columns
        return gap, cell, theme.row_height

    def relayout(self) -> None:
        self._columns = GRID_COLUMNS if self.edit_mode else columns_for_width(max(200, self.width()))
        min_sizes = {}
        for p in self.page.widgets:
            spec = self.registry.spec(p.type)
            if spec is not None:
                min_sizes[p.type] = spec.min_size
        placements = pack(self.page.widgets, GRID_COLUMNS) if self._columns >= GRID_COLUMNS else reflow(
            self.page.widgets, self._columns, min_sizes)
        self._display = {p.id: p.rect() for p in placements if not p.hidden}
        self._apply_geometry()

    def _apply_geometry(self) -> None:
        gap, cell, row_h = self._metrics()
        # Hauteur de la disposition COMPLÈTE (pas des seuls cadres déjà créés) :
        # pas de saut de mise en page pendant une construction incrémentale.
        rows = max((y + h for (_x, y, _w, h) in self._display.values()), default=0)
        for instance_id, frame in self.frames.items():
            rect = self._display.get(instance_id)
            if rect is None:
                continue
            x, y, w, h = rect
            frame.setGeometry(QRect(round(x * (cell + gap)), round(y * (row_h + gap)),
                                    max(40, round(w * cell + (w - 1) * gap)), max(40, h * row_h + (h - 1) * gap)))
        height = rows * (row_h + gap) + (row_h if self.edit_mode else 0)
        self.setMinimumHeight(max(row_h * 2, height))

    def resizeEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().resizeEvent(event)
        self.relayout()

    def apply_theme(self) -> None:
        for frame in self.frames.values():
            frame.apply_theme()
        self.relayout()

    def set_edit_mode(self, enabled: bool) -> None:
        self.edit_mode = bool(enabled)
        for frame in self.frames.values():
            frame.set_edit_mode(self.edit_mode)
        self.relayout()

    # -- édition ---------------------------------------------------------------------

    def _cell_delta(self, start: QPoint, now: QPoint) -> tuple[int, int]:
        gap, cell, row_h = self._metrics()
        dx = (now.x() - start.x()) / (cell + gap)
        dy = (now.y() - start.y()) / (row_h + gap)
        return round(dx), round(dy)

    def _on_move_started(self, frame: WidgetFrame, global_pos: QPoint) -> None:
        self._drag_origin = (global_pos, frame.placement.copy())
        frame.raise_()

    def _preview(self, candidate: Placement, final: bool) -> None:
        others = [p if p.id != candidate.id else candidate for p in self.page.widgets]
        result = settle(others, GRID_COLUMNS, fixed_id=candidate.id) if final else pack(
            others, GRID_COLUMNS, fixed_id=candidate.id)
        if final:
            by_id = {p.id: p for p in result}
            for placement in self.page.widgets:
                updated = by_id.get(placement.id)
                if updated is not None:
                    placement.x, placement.y, placement.w, placement.h = updated.x, updated.y, updated.w, updated.h
        self._display = {p.id: p.rect() for p in result if not p.hidden}
        self._apply_geometry()

    def _candidate(self, frame: WidgetFrame, global_pos: QPoint, resize: bool) -> Placement | None:
        if self._drag_origin is None:
            return None
        start, origin = self._drag_origin
        dx, dy = self._cell_delta(start, global_pos)
        spec = self.registry.spec(origin.type)
        min_size = spec.min_size if spec else (2, 2)
        max_size = spec.max_size if spec else None
        candidate = origin.copy()
        if resize:
            x, y, w, h = clamp_rect(origin.x, origin.y, origin.w + dx, origin.h + dy, GRID_COLUMNS, min_size, max_size)
        else:
            x, y, w, h = clamp_rect(origin.x + dx, origin.y + dy, origin.w, origin.h, GRID_COLUMNS, min_size, max_size)
        candidate.x, candidate.y, candidate.w, candidate.h = x, y, w, h
        return candidate

    def _on_move_dragged(self, frame: WidgetFrame, global_pos: QPoint) -> None:
        candidate = self._candidate(frame, global_pos, resize=False)
        if candidate is not None:
            self._preview(candidate, final=False)

    def _on_resize_dragged(self, frame: WidgetFrame, global_pos: QPoint) -> None:
        candidate = self._candidate(frame, global_pos, resize=True)
        if candidate is not None:
            self._preview(candidate, final=False)

    def _finish(self, frame: WidgetFrame, global_pos: QPoint, resize: bool) -> None:
        candidate = self._candidate(frame, global_pos, resize=resize)
        self._drag_origin = None
        if candidate is None:
            return
        self._preview(candidate, final=True)
        self.layout_edited.emit()

    def _on_move_finished(self, frame: WidgetFrame, global_pos: QPoint) -> None:
        self._finish(frame, global_pos, resize=False)

    def _on_resize_finished(self, frame: WidgetFrame, global_pos: QPoint) -> None:
        self._finish(frame, global_pos, resize=True)

    def move_placement(self, instance_id: str, x: int, y: int, w: int | None = None, h: int | None = None) -> bool:
        """Déplacement programmatique (clavier, tests) avec résolution des collisions."""
        placement = self.page.find(instance_id)
        if placement is None:
            return False
        spec = self.registry.spec(placement.type)
        candidate = placement.copy()
        cx, cy, cw, ch = clamp_rect(x, y, w if w is not None else placement.w, h if h is not None else placement.h,
                                    GRID_COLUMNS, spec.min_size if spec else (1, 1), spec.max_size if spec else None)
        candidate.x, candidate.y, candidate.w, candidate.h = cx, cy, cw, ch
        self._preview(candidate, final=True)
        self.layout_edited.emit()
        return True

    def rows(self) -> int:
        return content_rows(self.page.widgets)
