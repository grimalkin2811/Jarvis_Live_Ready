"""Grille d'une page : positionnement responsive, édition, activation."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, QRect, Signal
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

    def _create_frame(self, placement: Placement) -> WidgetFrame:
        spec = self.registry.spec(placement.type)
        widget = None
        if spec is not None:
            placement.settings = spec.validate_settings(placement.settings)
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
        if self._active and widget is not None:
            widget.activate()
        return frame

    def _destroy_frame(self, instance_id: str) -> None:
        frame = self.frames.pop(instance_id, None)
        if frame is None:
            return
        if frame.widget is not None:
            frame.widget.dispose()
        frame.hide()
        frame.setParent(None)
        frame.deleteLater()

    def dispose(self) -> None:
        for instance_id in list(self.frames):
            self._destroy_frame(instance_id)

    # -- activation ----------------------------------------------------------------

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        for frame in self.frames.values():
            if frame.widget is None:
                continue
            if self._active:
                frame.widget.activate()
            else:
                frame.widget.deactivate()

    def update_visibility(self, visible: QRect | None) -> None:
        """OnePage : seuls les widgets dans (ou près de) la zone visible sont actifs."""
        if visible is None:
            self.set_active(False)
            return
        self._active = True
        margin = 200
        zone = visible.adjusted(0, -margin, 0, margin)
        for frame in self.frames.values():
            if frame.widget is None:
                continue
            if frame.geometry().intersects(zone):
                frame.widget.activate()
            else:
                frame.widget.deactivate()

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
        rows = 0
        for instance_id, frame in self.frames.items():
            rect = self._display.get(instance_id)
            if rect is None:
                continue
            x, y, w, h = rect
            rows = max(rows, y + h)
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
