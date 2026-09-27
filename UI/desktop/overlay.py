"""Overlay Desktop et son contrôleur (v1.7.0).

Architecture
------------
::

    backend vocal ──► DESKTOP_EVENTS (sans Qt)
                          │
                          ▼
              DesktopOverlayController        (états, minuteurs, politique)
                          │
                          ▼
                    DesktopOverlay            (fenêtre traversante + halo)
                          ├── StatusChip
                          ├── TranscriptCard
                          ├── ResponseCard
                          ├── ToolChip
                          └── AudioBars
                          │
              DesktopControlsWindow           (uniquement si l'utilisateur
                          └── ControlsBar      active les contrôles)

Non-intrusion
-------------
L'overlay principal est, par défaut et par construction, **traversant** :
``Qt.WindowTransparentForInput`` + ``WA_TransparentForMouseEvents`` +
``WA_ShowWithoutActivating`` + ``Qt.WindowDoesNotAcceptFocus``. Il ne prend
jamais le focus, n'intercepte ni clavier ni raccourcis Windows, et ne change
jamais la fenêtre active.

Les widgets cliquables ne sont **pas** posés sur cet overlay : ils vivent dans
une petite fenêtre outil séparée, créée uniquement quand l'utilisateur les
active. C'est ce qui permet d'avoir des contrôles cliquables *sans* rendre
l'écran entier interceptant, et sans masquer le halo (un ``setMask`` sur la
fenêtre principale découperait aussi son rendu).
"""

from __future__ import annotations

import time

from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QTimer,
    Property,
    Qt,
    Signal,
    Slot,
)
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import QWidget

from src.logging_setup import get_logger

from . import config as cfg
from .design import Metrics, Motion, text_color_for
from .halo import HaloRenderer
from .state import (
    STATE_LABELS,
    DesktopEvent,
    DesktopState,
    DesktopStateMachine,
    normalize_state,
)
from .widgets import (
    AudioBars,
    ControlsBar,
    ResponseCard,
    StatusChip,
    ToolChip,
    TranscriptCard,
)

log = get_logger("desktop")


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _default_appearance():
    from ..appearance_actions import AppearanceState

    return AppearanceState()


# ---------------------------------------------------------------------------
# Fenêtre des contrôles interactifs
# ---------------------------------------------------------------------------


class DesktopControlsWindow(QWidget):
    """Petite fenêtre outil qui héberge les widgets cliquables.

    Séparée de l'overlay pour que l'écran entier reste traversant. Elle ne
    prend jamais le focus et n'apparaît pas dans la barre des tâches.
    """

    def __init__(self) -> None:
        super().__init__()
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        if hasattr(Qt, "WindowDoesNotAcceptFocus"):
            flags |= Qt.WindowDoesNotAcceptFocus
        self.setWindowFlags(flags)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.controls = ControlsBar(self)
        self.controls.move(0, 0)
        self.hide()

    def fit(self) -> None:
        size = self.controls.sizeHint()
        self.controls.resize(size)
        self.resize(size)

    def show_at(self, top_left: QPoint) -> None:
        self.fit()
        self.move(top_left)
        if not self.isVisible():
            self.show()
        self.controls.show_hud()

    def hide_controls(self, *, immediate: bool = False) -> None:
        self.controls.hide_hud(immediate=immediate)
        if immediate:
            self.hide()


# ---------------------------------------------------------------------------
# Overlay principal
# ---------------------------------------------------------------------------


class DesktopOverlay(QWidget):
    """Fenêtre plein écran, traversante, qui porte le halo et le HUD.

    L'API historique (``show_listening`` / ``show_thinking`` / ``show_speaking``
    / ``show_idle`` / ``hide_overlay`` / ``current_presence``) est conservée :
    le reste de Jarvis (et les tests de non-régression 1.5.3/1.6.0) continue de
    fonctionner sans changement.
    """

    overlayIntensityChanged = Signal(float)
    stateChanged = Signal(str)

    #: Émis quand un bouton de la barre de contrôles est utilisé.
    stopRequested = Signal()
    muteToggled = Signal()
    hideRequested = Signal()

    def __init__(self, appearance_state=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._appearance_state = appearance_state if appearance_state is not None else _default_appearance()
        self._overlay_intensity = 0.0
        self._presence_state = DesktopState.HIDDEN
        self._animation_target = 0.0
        self._level = 0.0
        self._level_target = 0.0
        self._last_tick = time.monotonic()
        self._screen_name = ""
        self._controls_window: DesktopControlsWindow | None = None
        self._interaction = cfg.INTERACTION_WIDGETS

        self._apply_window_flags(click_through=True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(False)

        self.halo = HaloRenderer()

        # Widgets du HUD : créés une seule fois, jamais reconstruits.
        self.status = StatusChip(self)
        self.transcript = TranscriptCard(self)
        self.response = ResponseCard(self)
        self.tool = ToolChip(self)
        self.audio = AudioBars(self)
        self._widgets: dict[str, QWidget] = {
            cfg.STATUS: self.status,
            cfg.TRANSCRIPT: self.transcript,
            cfg.RESPONSE: self.response,
            cfg.TOOL: self.tool,
            cfg.AUDIO: self.audio,
        }

        self._fade_animation = QPropertyAnimation(self, b"overlay_intensity", self)
        self._fade_animation.setEasingCurve(QEasingCurve(Motion.APPEAR_CURVE))
        self._fade_animation.setDuration(Motion.APPEAR_MS)
        self._fade_animation.finished.connect(self._on_fade_finished)

        # Un seul minuteur pour toute l'animation. Il ne tourne QUE lorsque
        # quelque chose bouge — à l'état masqué, le coût CPU est nul.
        self._pulse_timer = QTimer(self)
        self._pulse_timer.setTimerType(Qt.PreciseTimer)
        self._pulse_timer.timeout.connect(self._tick)

        self._frames = 0
        self._paints = 0

        self.apply_appearance()
        self._connect_screen_signals()

    # ------------------------------------------------------------------
    # Apparence / configuration
    # ------------------------------------------------------------------

    @property
    def appearance_state(self):
        return self._appearance_state

    @appearance_state.setter
    def appearance_state(self, value) -> None:
        self._appearance_state = value if value is not None else _default_appearance()
        self.apply_appearance()

    @property
    def config(self) -> cfg.DesktopAppearanceConfig:
        """Réglages Desktop, toujours lus depuis l'état d'apparence courant."""
        desktop = getattr(self._appearance_state, "desktop", None)
        if isinstance(desktop, cfg.DesktopAppearanceConfig):
            return desktop
        fresh = cfg.DesktopAppearanceConfig()
        try:
            self._appearance_state.desktop = fresh
        except Exception:
            pass
        return fresh

    def apply_appearance(self) -> None:
        """Ré-applique thème, échelle, politique d'interaction et layout."""
        config = self.config
        glow = QColor(getattr(self._appearance_state, "glow_color", QColor(70, 180, 255)))
        text = text_color_for(getattr(self._appearance_state, "text_color", QColor(214, 236, 255)))
        self.halo.set_theme(glow)
        self.halo.configure(
            intensity=config.intensity,
            thickness=config.thickness,
            motion=config.motion_scale(),
            audio_reactive=config.audio_reactive,
        )
        for name, widget in self._widgets.items():
            slot = config.slot(name)
            widget.apply_theme(glow, text, scale=slot.scale, text_scale=config.text_scale)
        if self._controls_window is not None:
            slot = config.slot(cfg.CONTROLS)
            self._controls_window.controls.apply_theme(
                glow, text, scale=slot.scale, text_scale=config.text_scale
            )
        self._apply_interaction(config.interaction)
        self._layout_widgets()
        self._sync_widget_visibility()
        log.debug(
            "apparence appliquée état=%s interaction=%s reduced_motion=%s widgets=%s",
            self._presence_state,
            config.interaction,
            config.reduced_motion,
            ",".join(config.enabled_widgets()) or "-",
        )
        self.update()

    def _apply_window_flags(self, *, click_through: bool) -> None:
        # L'attribut est posé AVANT les drapeaux : Qt réintroduit
        # ``WindowTransparentForInput`` tant que ``WA_TransparentForMouseEvents``
        # est actif sur une fenêtre de premier niveau. Dans l'autre ordre, le
        # mode « overlay interactif » resterait traversant.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, bool(click_through))
        flags = Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        if hasattr(Qt, "WindowDoesNotAcceptFocus"):
            flags |= Qt.WindowDoesNotAcceptFocus
        if click_through and hasattr(Qt, "WindowTransparentForInput"):
            flags |= Qt.WindowTransparentForInput
        self.setWindowFlags(flags)

    def _apply_interaction(self, interaction: str) -> None:
        interaction = cfg.normalize_interaction(interaction)
        click_through = interaction != cfg.INTERACTION_FULL
        if interaction != self._interaction:
            log.info("interaction overlay : %s", interaction)
        self._interaction = interaction
        currently = bool(self.testAttribute(Qt.WA_TransparentForMouseEvents))
        if currently != click_through:
            visible = self.isVisible()
            self._apply_window_flags(click_through=click_through)
            if visible:
                self.show_overlay()

    def interaction_mode(self) -> str:
        return self._interaction

    def is_click_through(self) -> bool:
        """Vrai si les clics traversent la fenêtre principale."""
        return bool(self.testAttribute(Qt.WA_TransparentForMouseEvents))

    # ------------------------------------------------------------------
    # Écrans
    # ------------------------------------------------------------------

    def _connect_screen_signals(self) -> None:
        app = QGuiApplication.instance()
        if app is None:
            return
        try:
            app.primaryScreenChanged.connect(lambda _screen: self.follow_screen())
            app.screenAdded.connect(lambda _screen: self.follow_screen())
            app.screenRemoved.connect(lambda _screen: self.follow_screen())
        except Exception:  # pragma: no cover - dépend de la plateforme
            log.debug("signaux d'écran indisponibles")

    def target_screen(self):
        """Écran qui doit porter l'overlay.

        Priorité : l'écran sous le curseur (c'est là que l'utilisateur
        travaille), sinon l'écran principal. Choisi à chaque affichage, ce qui
        gère naturellement le multi-écran, le changement d'écran principal et
        les résolutions/DPI différents.
        """
        app = QGuiApplication.instance()
        if app is None:
            return None
        screen = None
        try:
            screen = app.screenAt(app.primaryScreen().geometry().center() if app.primaryScreen() else QPoint(0, 0))
        except Exception:
            screen = None
        try:
            from PySide6.QtGui import QCursor

            cursor_screen = app.screenAt(QCursor.pos())
            if cursor_screen is not None:
                screen = cursor_screen
        except Exception:
            pass
        return screen or app.primaryScreen()

    def follow_screen(self) -> bool:
        """Aligne la géométrie de l'overlay sur l'écran cible. Retourne True si changé."""
        screen = self.target_screen()
        if screen is None:
            return False
        geometry: QRect = screen.geometry()
        name = screen.name() or ""
        changed = name != self._screen_name or self.geometry() != geometry
        if not changed:
            return False
        self._screen_name = name
        self.setGeometry(geometry)
        self.halo.resize(geometry.size())
        self._layout_widgets()
        log.info(
            "écran cible : %s %dx%d dpr=%.2f",
            name or "principal",
            geometry.width(),
            geometry.height(),
            float(screen.devicePixelRatio()),
        )
        return True

    def screen_name(self) -> str:
        return self._screen_name

    # ------------------------------------------------------------------
    # Placement des widgets
    # ------------------------------------------------------------------

    def _layout_widgets(self) -> None:
        width = max(1, self.width())
        height = max(1, self.height())
        config = self.config
        max_card = max(Metrics.CARD_MIN_WIDTH, width * Metrics.CARD_MAX_WIDTH_RATIO)
        self.transcript.set_max_width(max_card)
        self.response.set_max_width(max_card)
        for name, widget in self._widgets.items():
            slot = config.slot(name)
            widget.refresh_geometry()
            self._place(widget, slot, width, height)
        if self._controls_window is not None:
            slot = config.slot(cfg.CONTROLS)
            self._controls_window.fit()
            size = self._controls_window.size()
            origin = self.geometry().topLeft()
            x, y = self._anchor(slot, width, height, size.width(), size.height())
            self._controls_window.move(origin + QPoint(x, y))

    def _anchor(self, slot: cfg.WidgetSlot, width: int, height: int, w: int, h: int) -> tuple[int, int]:
        margin = Metrics.SCREEN_MARGIN
        x = slot.x * width - w / 2.0
        y = slot.y * height - h / 2.0
        x = _clamp(x, margin, max(margin, width - w - margin))
        y = _clamp(y, margin, max(margin, height - h - margin))
        return int(round(x)), int(round(y))

    def _place(self, widget: QWidget, slot: cfg.WidgetSlot, width: int, height: int) -> None:
        size = widget.body_size() if hasattr(widget, "body_size") else widget.size()
        x, y = self._anchor(slot, width, height, size.width(), size.height())
        widget.move(x, y)

    def widget_rect(self, name: str) -> QRect:
        """Rectangle **visible** d'un widget (sans la marge d'animation).

        C'est la géométrie qui compte pour l'utilisateur, et donc celle que
        les tests de layout inspectent.
        """
        widget = self._widgets.get(name)
        if widget is None:
            if name == cfg.CONTROLS and self._controls_window is not None:
                return self._controls_window.geometry()
            return QRect()
        size = widget.body_size() if hasattr(widget, "body_size") else widget.size()
        return QRect(widget.pos(), size)

    # ------------------------------------------------------------------
    # Visibilité des widgets selon l'état
    # ------------------------------------------------------------------

    def _sync_widget_visibility(self) -> None:
        config = self.config
        state = self._presence_state
        for name, widget in self._widgets.items():
            should = config.is_visible(name, state)
            if should and name == cfg.TRANSCRIPT and not self.transcript.text():
                should = False
            if should and name == cfg.RESPONSE and not self.response.text():
                should = False
            if should:
                widget.show_hud()
            else:
                widget.hide_hud()
        self._sync_controls(config, state)

    def _sync_controls(self, config: cfg.DesktopAppearanceConfig, state: str) -> None:
        slot = config.slot(cfg.CONTROLS)
        should = slot.enabled and slot.visible_in(state) and config.interaction != cfg.INTERACTION_ALWAYS
        if not should:
            if self._controls_window is not None:
                self._controls_window.hide_controls()
            return
        window = self._ensure_controls_window()
        self._layout_widgets()
        window.show_at(window.pos())
        window.raise_()

    def _ensure_controls_window(self) -> DesktopControlsWindow:
        if self._controls_window is None:
            window = DesktopControlsWindow()
            glow = QColor(getattr(self._appearance_state, "glow_color", QColor(70, 180, 255)))
            text = text_color_for(getattr(self._appearance_state, "text_color", QColor(214, 236, 255)))
            slot = self.config.slot(cfg.CONTROLS)
            window.controls.apply_theme(glow, text, scale=slot.scale, text_scale=self.config.text_scale)
            window.controls.stopRequested.connect(self.stopRequested)
            window.controls.muteToggled.connect(self.muteToggled)
            window.controls.hideRequested.connect(self.hideRequested)
            self._controls_window = window
            log.info("fenêtre de contrôles créée (widgets interactifs activés)")
        return self._controls_window

    def controls_window(self) -> DesktopControlsWindow | None:
        return self._controls_window

    # ------------------------------------------------------------------
    # Contenu textuel
    # ------------------------------------------------------------------

    def set_transcript(self, text: str) -> None:
        self.transcript.set_text(text)
        self._layout_widgets()
        self._sync_widget_visibility()

    def set_response(self, text: str) -> None:
        self.response.set_text(text)
        self._layout_widgets()
        self._sync_widget_visibility()

    def set_tool(self, name: str) -> None:
        self.tool.set_tool(name)
        self._layout_widgets()

    def clear_texts(self) -> None:
        self.transcript.set_text("")
        self.response.set_text("")

    def set_muted(self, muted: bool) -> None:
        if self._controls_window is not None:
            self._controls_window.controls.set_muted(muted)

    def set_level(self, level: float) -> None:
        try:
            self._level_target = _clamp(float(level), 0.0, 1.0)
        except (TypeError, ValueError):
            self._level_target = 0.0

    def set_progress(self, fraction: float) -> None:
        self.halo.set_progress(fraction)

    # ------------------------------------------------------------------
    # États
    # ------------------------------------------------------------------

    def set_state(self, state: str, *, immediate: bool = False) -> None:
        """Applique un état visuel. C'est le point d'entrée unique du rendu."""
        target = normalize_state(state, DesktopState.HIDDEN)
        previous = self._presence_state
        self._presence_state = target

        if target == DesktopState.HIDDEN:
            self.halo.set_state(target, immediate=immediate)
            self._animate_to(0.0, Motion.DISAPPEAR_MS, immediate=immediate)
            for widget in self._widgets.values():
                widget.hide_hud(immediate=immediate)
            if self._controls_window is not None:
                self._controls_window.hide_controls()
        else:
            if not self.isVisible():
                self.show_overlay()
            self.halo.set_state(target, immediate=immediate)
            self.status.set_text(STATE_LABELS.get(target, ""))
            if target == DesktopState.TOOL_USE and not self.tool.text():
                self.tool.set_tool("")
            self._layout_widgets()
            self._sync_widget_visibility()
            self._animate_to(1.0, Motion.APPEAR_MS, immediate=immediate)
            self._start_animation()

        if previous != target:
            self.stateChanged.emit(target)
        self.update()

    def current_presence(self) -> str:
        """État visuel courant (API historique)."""
        return str(self._presence_state or DesktopState.HIDDEN)

    def current_state(self) -> str:
        return self.current_presence()

    # -- API historique -----------------------------------------------------

    def show_listening(self) -> None:
        self.set_state(DesktopState.LISTENING)

    def show_thinking(self) -> None:
        self.set_state(DesktopState.THINKING)

    def show_speaking(self) -> None:
        self.set_state(DesktopState.SPEAKING)

    def show_tool_use(self, name: str = "") -> None:
        if name:
            self.set_tool(name)
        self.set_state(DesktopState.TOOL_USE)

    def show_follow_up(self) -> None:
        self.set_state(DesktopState.FOLLOW_UP)

    def show_error(self) -> None:
        self.set_state(DesktopState.ERROR)

    def show_idle(self) -> None:
        """Présence discrète affichée à la demande (icône de notification)."""
        self.set_state(DesktopState.FOLLOW_UP)

    def hide_overlay(self) -> None:
        self.set_state(DesktopState.HIDDEN)

    def show_overlay(self) -> None:
        self.follow_screen()
        super().showFullScreen()
        self.raise_()
        self._start_animation()

    # ------------------------------------------------------------------
    # Animation
    # ------------------------------------------------------------------

    def _frame_interval(self) -> int:
        return Motion.FRAME_MS_REDUCED if self.config.reduced_motion else Motion.FRAME_MS

    def _start_animation(self) -> None:
        interval = self._frame_interval()
        if self._pulse_timer.isActive():
            if self._pulse_timer.interval() != interval:
                self._pulse_timer.setInterval(interval)
            return
        self._last_tick = time.monotonic()
        self._pulse_timer.start(interval)

    def _stop_animation(self) -> None:
        if self._pulse_timer.isActive():
            self._pulse_timer.stop()

    def is_animating(self) -> bool:
        return self._pulse_timer.isActive()

    def frame_count(self) -> int:
        return self._frames

    def paint_count(self) -> int:
        return self._paints

    def _tick(self) -> None:
        now = time.monotonic()
        dt = _clamp(now - self._last_tick, 0.0, 0.2)
        self._last_tick = now
        self._frames += 1

        self._level += (self._level_target - self._level) * Motion.LEVEL_SMOOTHING
        self.halo.set_level(self._level)
        blend_step = dt * (1000.0 / max(1.0, Motion.CROSSFADE_MS))
        self.halo.advance(dt, blend_step)

        if self.audio.is_showing():
            self.audio.set_level(self._level)
            self.audio.advance(dt, self.config.motion_scale())
        if self.status.is_showing():
            self.status.set_pulse(self._level)

        if (
            self._presence_state == DesktopState.HIDDEN
            and self._animation_target <= 0.0
            and self._overlay_intensity <= 0.002
        ):
            # Plus rien à animer : on coupe le minuteur. C'est ce qui garantit
            # un coût CPU nul quand Jarvis est en veille.
            if self._fade_animation.state() == QAbstractAnimation.State.Running:
                self._fade_animation.stop()
            super().hide()
            self._stop_animation()
            return
        # Repaint limité aux bandes du halo : l'intérieur de l'écran n'est
        # jamais retraité.
        self.update(self.halo.region())

    def _animate_to(self, target: float, duration: int, *, immediate: bool = False) -> None:
        target = _clamp(target, 0.0, 1.0)
        self._animation_target = target
        if target > 0.0 and not self.isVisible():
            self.show_overlay()
        self._fade_animation.stop()
        if immediate:
            # Utilisé par les tests et par les changements d'état sans
            # transition (reprise après suppression par le mode jeu).
            self.set_overlay_intensity(target)
            self._on_fade_finished()
            if target > 0.0:
                self._start_animation()
            return
        self._fade_animation.setEasingCurve(
            QEasingCurve(Motion.APPEAR_CURVE if target > 0.0 else Motion.DISAPPEAR_CURVE)
        )
        self._fade_animation.setDuration(max(1, int(duration)))
        self._fade_animation.setStartValue(self._overlay_intensity)
        self._fade_animation.setEndValue(target)
        self._fade_animation.start()
        if target > 0.0:
            self._start_animation()

    def _on_fade_finished(self) -> None:
        if self._animation_target <= 0.0 and self._overlay_intensity <= 0.002:
            super().hide()
            self._stop_animation()

    # -- propriété d'intensité (API historique) ------------------------------

    def get_overlay_intensity(self) -> float:
        return self._overlay_intensity

    def set_overlay_intensity(self, value: float) -> None:
        value = _clamp(float(value), 0.0, 1.0)
        if abs(self._overlay_intensity - value) < 1e-4:
            return
        self._overlay_intensity = value
        self.overlayIntensityChanged.emit(value)
        self.update(self.halo.region())

    overlay_intensity = Property(
        float,
        get_overlay_intensity,
        set_overlay_intensity,
        notify=overlayIntensityChanged,
    )

    # ------------------------------------------------------------------
    # Rendu
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.halo.resize(self.size())
        self._layout_widgets()
        self.update()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.halo.resize(self.size())
        self._layout_widgets()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        self._paints += 1
        painter = QPainter(self)
        try:
            region = event.region() if hasattr(event, "region") else None
            painter.setCompositionMode(QPainter.CompositionMode_Source)
            if region is not None and not region.isEmpty():
                for rect in region.rects() if hasattr(region, "rects") else [region.boundingRect()]:
                    painter.fillRect(rect, Qt.transparent)
            else:
                painter.fillRect(self.rect(), Qt.transparent)
            painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
            if not self.config.slot(cfg.HALO).visible_in(self._presence_state):
                return
            painter.setRenderHint(QPainter.Antialiasing, True)
            self.halo.paint(painter, self._overlay_intensity)
        finally:
            painter.end()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._stop_animation()
        if self._controls_window is not None:
            self._controls_window.close()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Contrôleur
# ---------------------------------------------------------------------------


class DesktopOverlayController(QObject):
    """Relie les évènements réels de Jarvis à l'overlay.

    Il ne devine rien : chaque changement d'état provient d'un fait du
    pipeline. Les seuls minuteurs sont des **filets de sécurité** (fenêtre
    d'écoute, effacement d'une erreur) dont les durées sont définies dans
    ``UI.desktop.state``.

    Le nom historique ``PresenceRouter`` reste un alias de cette classe.
    """

    def __init__(self, overlay: DesktopOverlay, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._overlay = overlay
        self.machine = DesktopStateMachine(on_transition=self._on_transition)

        # Filet de sécurité de l'état courant (nom historique conservé : il est
        # inspecté par les tests de non-régression 1.5.3).
        self._listen_hide_timer = QTimer(self)
        self._listen_hide_timer.setSingleShot(True)
        self._listen_hide_timer.timeout.connect(self._on_state_timeout)

        self._transcript_timer = QTimer(self)
        self._transcript_timer.setSingleShot(True)
        self._transcript_timer.timeout.connect(self._clear_transcript)

        self._response_timer = QTimer(self)
        self._response_timer.setSingleShot(True)
        self._response_timer.timeout.connect(self._clear_response)

        self._countdown_timer = QTimer(self)
        self._countdown_timer.timeout.connect(self._update_countdown)
        self._deadline = 0.0
        self._window = 0.0

    # -- accès --------------------------------------------------------------

    @property
    def overlay(self) -> DesktopOverlay:
        return self._overlay

    def current_state(self) -> str:
        return self.machine.state

    # -- entrées ------------------------------------------------------------

    @Slot(str)
    def handle_presence(self, state: str) -> None:
        """Entrée de compatibilité (``AudioIO.presence_hook``)."""
        if self._suppressed():
            self._force_hidden()
            return
        self.machine.handle_presence(str(state or ""))

    @Slot(str, object)
    def handle_event(self, event: str, payload: object = None) -> None:
        """Entrée riche : évènements réels du pipeline."""
        data = dict(payload) if isinstance(payload, dict) else {}
        name = str(event or "")

        if name == "level":
            self._overlay.set_level(float(data.get("value", 0.0) or 0.0))
            return

        if self._suppressed():
            self._force_hidden()
            return

        if name == DesktopEvent.TRANSCRIPT:
            self._apply_transcript(str(data.get("text") or ""))
        elif name == DesktopEvent.RESPONSE:
            self._apply_response(str(data.get("text") or ""))
        elif name in (DesktopEvent.SLEEP, DesktopEvent.RESET, DesktopEvent.READY):
            self._overlay.clear_texts()

        self.machine.handle(name, data)

    def reset(self) -> None:
        self._listen_hide_timer.stop()
        self._countdown_timer.stop()
        self._transcript_timer.stop()
        self._response_timer.stop()
        self._overlay.clear_texts()
        self.machine.reset()

    # -- compat 1.5.3 -------------------------------------------------------

    def _hide_if_listening(self) -> None:
        """Masque le halo s'il est resté en écoute (filet de sécurité)."""
        if self.machine.state in (DesktopState.LISTENING, DesktopState.FOLLOW_UP):
            self.machine.handle(DesktopEvent.SLEEP)

    # -- interne ------------------------------------------------------------

    def _suppressed(self) -> bool:
        """Mode jeu : aucun affichage au-dessus du jeu (règle 1.3.0)."""
        try:
            from src.modes import get_default_mode_manager

            return bool(get_default_mode_manager().should_suppress_visuals())
        except Exception:
            return False

    def _force_hidden(self) -> None:
        self._listen_hide_timer.stop()
        self._countdown_timer.stop()
        self.machine.reset()

    def _on_transition(self, transition) -> None:
        state = transition.current
        if state == DesktopState.TOOL_USE:
            self._overlay.set_tool(self.machine.tool_name)
        self._overlay.set_state(state)

        if transition.changed:
            log.info(
                "état desktop %s -> %s (%s)",
                transition.previous,
                state,
                transition.event,
            )

        self._arm_timers(state)

    def _arm_timers(self, state: str) -> None:
        self._listen_hide_timer.stop()
        self._countdown_timer.stop()
        seconds = self.machine.timeout_seconds()
        if not seconds:
            self._overlay.set_progress(1.0)
            return
        self._window = float(seconds)
        self._deadline = time.monotonic() + self._window
        self._listen_hide_timer.start(int(self._window * 1000))
        if state == DesktopState.FOLLOW_UP:
            self._overlay.set_progress(1.0)
            self._countdown_timer.start(100)

    def _on_state_timeout(self) -> None:
        self.machine.timeout()

    def _update_countdown(self) -> None:
        if self._window <= 0.0:
            return
        remaining = max(0.0, self._deadline - time.monotonic())
        self._overlay.set_progress(remaining / self._window)

    def _apply_transcript(self, text: str) -> None:
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return
        self._overlay.set_transcript(cleaned)
        seconds = self._overlay.config.transcript_seconds
        self._transcript_timer.start(int(max(1.0, seconds) * 1000))
        # Aucun contenu privé dans le journal : seulement la taille.
        log.debug("transcription desktop chars=%d", len(cleaned))

    def _apply_response(self, text: str) -> None:
        cleaned = " ".join(str(text or "").split())
        if not cleaned:
            return
        self._overlay.set_response(cleaned)
        seconds = self._overlay.config.response_seconds
        self._response_timer.start(int(max(1.0, seconds) * 1000))
        log.debug("réponse desktop chars=%d", len(cleaned))

    def _clear_transcript(self) -> None:
        self._overlay.set_transcript("")

    def _clear_response(self) -> None:
        self._overlay.set_response("")

    def close(self) -> None:
        self._listen_hide_timer.stop()
        self._countdown_timer.stop()
        self._transcript_timer.stop()
        self._response_timer.stop()


#: Nom historique (1.5.3) conservé : ``src.ui`` et les tests l'utilisent.
PresenceRouter = DesktopOverlayController
