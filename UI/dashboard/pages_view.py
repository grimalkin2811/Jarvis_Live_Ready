"""Vues de pages : une page (multipage) ou toutes les pages empilées (OnePage)."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRect, Qt, QTimer
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from .glyphs import rich_title
from .grid_view import GridCanvas
from .layout_model import Page

ONEPAGE_LAST = "systeme"  # diagnostics TOUJOURS en bas (cahier des charges §5)


def onepage_order(pages: list[Page]) -> list[Page]:
    """Ordre OnePage : pages dans l'ordre de l'utilisateur, Paramètres puis
    Système (diagnostics) en dernier."""
    visible = [p for p in pages if not p.extras.get("_hidden_overflow")]
    head = [p for p in visible if p.id not in ("parametres", ONEPAGE_LAST)]
    tail = [p for p in visible if p.id == "parametres"] + [p for p in visible if p.id == ONEPAGE_LAST]
    return head + tail


class PageView(QScrollArea):
    """Une page (vue multipage).

    ``build=False`` (page de la première ouverture) : les cadres sont créés
    quelques-uns par tour de boucle Qt, dans un budget de temps, tant que la
    vue est active — jamais la page entière d'un bloc (correctif C10 : la
    construction de la page d'accueil était le plus long blocage de la boucle
    Qt mesuré sous Windows natif). ``build_all()`` termine sur-le-champ.
    """

    #: Budget de construction par tour de boucle Qt (ms), comme la vue OnePage.
    BUILD_BUDGET_MS = 12.0

    def __init__(self, page: Page, registry, theme_manager, context_factory: Callable, parent=None,
                 build: bool = True) -> None:
        super().__init__(parent)
        self.page = page
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setAttribute(Qt.WA_TranslucentBackground, True)
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(4, 4, 8, 16)
        self.canvas = GridCanvas(page, registry, theme_manager, context_factory, holder, build=build)
        layout.addWidget(self.canvas)
        layout.addStretch(1)
        self.setWidget(holder)
        self.viewport().setAutoFillBackground(False)
        holder.setAutoFillBackground(False)
        self._active = False
        self._build_timer = QTimer(self)
        self._build_timer.setSingleShot(True)
        self._build_timer.setInterval(0)
        self._build_timer.timeout.connect(self._build_next)

    def fully_built(self) -> bool:
        return self.canvas.built

    def _build_next(self) -> None:
        # Vue masquée ou désactivée : aucune construction (reprise à l'activation).
        if self.canvas.built or not self._active:
            return
        if not self.canvas.build_step(self.BUILD_BUDGET_MS):
            self._build_timer.start()

    def build_all(self) -> None:
        """Termine immédiatement la construction (accès programmatique, navigation)."""
        self._build_timer.stop()
        if not self.canvas.built:
            self.canvas.rebuild()

    def canvases(self) -> list[GridCanvas]:
        return [self.canvas]

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        self.canvas.set_active(active)
        if self._active and not self.canvas.built:
            self._build_timer.start()
        elif not self._active:
            self._build_timer.stop()

    def dispose(self) -> None:
        self._build_timer.stop()
        self.canvas.dispose()


class OnePageView(QScrollArea):
    """Toutes les pages en sections ; seuls les widgets visibles sont actifs."""

    def __init__(self, pages: list[Page], registry, theme_manager, context_factory: Callable, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.theme_manager = theme_manager
        holder = QWidget()
        holder.setAttribute(Qt.WA_TranslucentBackground, True)
        self._layout = QVBoxLayout(holder)
        self._layout.setContentsMargins(4, 4, 8, 24)
        self._layout.setSpacing(10)
        self.sections: list[tuple[Page, QLabel, GridCanvas]] = []
        for page in onepage_order(pages):
            header = QLabel(rich_title(page.icon, theme_manager.theme.title(page.title)))
            header.setTextFormat(Qt.RichText)
            header.setProperty("role", "title")
            header.setObjectName(f"section-{page.id}")
            # Construction DIFFÉRÉE : une section par tour de boucle Qt, pour
            # ne jamais bloquer l'interface pendant la création des widgets.
            canvas = GridCanvas(page, registry, theme_manager, context_factory, holder, build=False)
            self._layout.addWidget(header)
            self._layout.addWidget(canvas)
            self.sections.append((page, header, canvas))
        self._layout.addStretch(1)
        self._pending = [canvas for _p, _h, canvas in self.sections]
        self.setWidget(holder)
        self.viewport().setAutoFillBackground(False)
        self._active = False
        self._visibility_timer = QTimer(self)
        self._visibility_timer.setSingleShot(True)
        self._visibility_timer.setInterval(80)
        self._visibility_timer.timeout.connect(self._update_visibility)
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._schedule())
        self._build_timer = QTimer(self)
        self._build_timer.setSingleShot(True)
        self._build_timer.setInterval(0)
        self._build_timer.timeout.connect(self._build_next)
        self._build_timer.start()

    #: Budget de construction par tour de boucle Qt (ms) : quelques widgets à
    #: la fois, jamais une section entière d'un bloc (correctif C10).
    BUILD_BUDGET_MS = 12.0

    def _build_next(self) -> None:
        if not self._pending:
            return
        canvas = self._pending[0]
        if canvas.build_step(self.BUILD_BUDGET_MS):
            self._pending.pop(0)
        if self._active:
            self._update_visibility()
        if self._pending:
            self._build_timer.start()

    def build_all(self) -> None:
        """Termine immédiatement la construction (tests, captures)."""
        while self._pending:
            self._pending.pop(0).rebuild()
        if self._active:
            self._update_visibility()

    def fully_built(self) -> bool:
        return not self._pending

    def canvases(self) -> list[GridCanvas]:
        return [canvas for _page, _header, canvas in self.sections]

    def section_order(self) -> list[str]:
        return [page.id for page, _h, _c in self.sections]

    def _schedule(self) -> None:
        if self._active:
            self._visibility_timer.start()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule()

    def set_active(self, active: bool) -> None:
        self._active = bool(active)
        if not self._active:
            self._visibility_timer.stop()
            for canvas in self.canvases():
                canvas.set_active(False)
        else:
            self._update_visibility()

    def _update_visibility(self) -> None:
        if not self._active:
            return
        viewport = self.viewport()
        for canvas in self.canvases():
            top_left = canvas.mapTo(viewport, canvas.rect().topLeft())
            visible_in_canvas = QRect(-top_left.x(), -top_left.y(), viewport.width(), viewport.height())
            if visible_in_canvas.bottom() < -200 or visible_in_canvas.top() > canvas.height() + 200:
                canvas.update_visibility(None)
            else:
                canvas.update_visibility(visible_in_canvas)

    def scroll_to_page(self, page_id: str) -> None:
        for page, header, _canvas in self.sections:
            if page.id == page_id:
                self.verticalScrollBar().setValue(max(0, header.y() - 4))
                break

    def dispose(self) -> None:
        self._build_timer.stop()
        self._pending = []
        for canvas in self.canvases():
            canvas.dispose()
