"""Widgets « Jarvis » : état global, audio en direct, modèles, actions rapides."""

from __future__ import annotations

import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QComboBox, QGridLayout, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from src.runtime_status import ENGINE_LABELS, HEALTH_LABELS, EngineState

from ..actions import ActionHost, build_actions, mic_toggle, voice_stop
from ..glyphs import glyph_icon
from ..widget_api import STATE_EMPTY, STATE_READY, DashboardWidget, WidgetAction
from .common import Ring, StatusDot, button, clock, hrow, label

PRESENCE_LABELS = {
    "hidden": "En veille",
    "loading": "Initialisation",
    "listening": "À l'écoute",
    "thinking": "Réfléchit",
    "speaking": "Parle",
}

HEALTH_STATUS = {"operational": "ok", "degraded": "warning", "unavailable": "error", "unknown": "unknown"}


# ---------------------------------------------------------------------------
# État de Jarvis
# ---------------------------------------------------------------------------


class StateWidget(DashboardWidget):
    """Synthèse opérationnel / dégradé / indisponible / inconnu, avec preuves."""

    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        tm = context.theme_manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.ring = Ring(tm, 76)
        self.health = label("Inconnu", "metric")
        self.presence = label("", "dim")
        self.since = label("", "dim")
        head = QVBoxLayout()
        head.addWidget(self.health)
        head.addWidget(self.presence)
        head.addWidget(self.since)
        top = hrow(self.ring, 8)
        top.addLayout(head, 1)
        layout.addLayout(top)
        self.rows: dict[str, tuple[StatusDot, object]] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(3)
        for row, (key, title) in enumerate((("engine", "Modèle"), ("audio", "Audio"), ("wake", "Mot d'éveil"),
                                            ("memory", "Mémoire"), ("tasks", "Tâches"), ("ui", "Interface"))):
            dot = StatusDot(tm, size=9)
            name = label(title, "dim")
            value = label("", wrap=True)
            grid.addWidget(dot, row, 0)
            grid.addWidget(name, row, 1)
            grid.addWidget(value, row, 2)
            self.rows[key] = (dot, value)
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)
        layout.addStretch(1)
        self._memory = None

    def on_activate(self) -> None:
        hub = self.context.hub
        self.listen(hub.engine_changed, lambda _s: self.refresh_view())
        self.listen(hub.presence_changed, lambda _s: self.refresh_view())
        self.listen(hub.tasks_attached, lambda _s: self.refresh_view())
        self.listen(hub.interface_mode_changed, lambda _s: self.refresh_view())
        self.listen(hub.memory_changed, self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self.refresh_view()
        self.run_job(self.context.hub.memory.status, on_done=self._on_memory, on_error=lambda _e: None)

    def _on_memory(self, status: dict) -> None:
        self._memory = status
        self.refresh_view()

    def refresh_view(self) -> None:
        snap = self.context.hub.engine()
        health = snap.get("health", "unknown")
        status = HEALTH_STATUS.get(health, "unknown")
        self.health.setText(HEALTH_LABELS.get(health, "Inconnu"))
        presence = self.context.hub.presence()
        self.presence.setText(f"Présence : {PRESENCE_LABELS.get(presence, presence)}")
        since = snap.get("engine_since")
        self.since.setText(f"{ENGINE_LABELS.get(snap.get('engine_state'), '?')} depuis {clock(since)}")
        fraction = {"operational": 1.0, "degraded": 0.6, "unavailable": 0.15}.get(health)
        self.ring.set_value(fraction, status, {"operational": "OK", "degraded": "!", "unavailable": "✕"}.get(health, "?"))
        state = snap.get("engine_state")
        engine_status = "ok" if state == EngineState.READY else (
            "unknown" if state in (EngineState.NOT_STARTED, EngineState.STARTING, EngineState.CONNECTING) else
            "warning" if state == EngineState.RECONNECTING else "error")
        model = snap.get("model") or "modèle non publié"
        self._set_row("engine", engine_status, f"{ENGINE_LABELS.get(state, state)} — {model}")
        audio = snap.get("audio_state")
        self._set_row("audio", {"ok": "ok", "failed": "error"}.get(audio, "unknown"),
                      {"ok": "Flux démarrés", "failed": snap.get("audio_detail") or "Échec"}.get(audio, "Non confirmé"))
        wake = snap.get("wake_word")
        self._set_row("wake", {"loaded": "ok", "unavailable": "warning"}.get(wake, "unknown"),
                      {"loaded": "Chargé", "unavailable": "Indisponible (écoute continue)"}.get(wake, "Non publié"))
        memory = self._memory
        if memory is None:
            self._set_row("memory", "unknown", "Vérification…")
        elif not memory.get("enabled", True):
            self._set_row("memory", "warning", "Désactivée")
        elif memory.get("available"):
            self._set_row("memory", "ok", "Disponible")
        else:
            self._set_row("memory", "error", memory.get("error") or "Indisponible")
        if self.context.hub.tasks_available():
            counts = self.context.hub.tasks.counts()
            self._set_row("tasks", "ok", f"{counts.get('active', 0)} active(s), {counts.get('unread', 0)} nouveau(x)")
        else:
            self._set_row("tasks", "unknown", self.context.hub.tasks.unavailable_reason() or "Non initialisé")
        mode = self.context.hub.interface_mode() or "inconnu"
        self._set_row("ui", "ok", {"blob": "Orbe + Dashboard", "desktop": "Desktop + Dashboard"}.get(mode, mode))
        self.set_state(STATE_READY)

    def _set_row(self, key: str, status: str, text: str) -> None:
        dot, value = self.rows[key]
        dot.set_status(status)
        value.setText(text)


# ---------------------------------------------------------------------------
# Audio en direct
# ---------------------------------------------------------------------------


class LevelMeter(QWidget):
    def __init__(self, theme_manager, title: str, parent=None) -> None:
        super().__init__(parent)
        self.theme_manager = theme_manager
        self.title = title
        self.level = 0.0
        self.peak = 0.0
        self.setMinimumHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, event) -> None:  # noqa: N802
        theme = self.theme_manager.theme
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0, 14, -1, -2)
        p.setPen(theme.text_dim)
        p.drawText(QRectF(0, 0, self.width(), 14), Qt.AlignLeft | Qt.AlignVCenter, self.title)
        bars = 28
        gap = 2.0
        bw = (r.width() - gap * (bars - 1)) / bars
        lit = int(round(self.level * bars))
        for i in range(bars):
            color = QColor(theme.accent if i < bars * 0.7 else (theme.warning if i < bars * 0.9 else theme.danger))
            color.setAlphaF(0.95 if i < lit else 0.13)
            p.fillRect(QRectF(r.left() + i * (bw + gap), r.top(), bw, r.height()), color)
        if self.peak > 0.01:
            x = r.left() + min(bars - 1, int(self.peak * bars)) * (bw + gap)
            p.fillRect(QRectF(x, r.top() - 2, bw, r.height() + 4), theme.text)
        p.end()


class AudioWidget(DashboardWidget):
    """Niveaux micro / voix issus des évènements RÉELS (aucune capture audio)."""

    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        tm = context.theme_manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.presence_dot = StatusDot(tm)
        self.presence = label("", "title")
        self.mic_state = label("", "dim")
        layout.addLayout(hrow(self.presence_dot, self.presence, None, self.mic_state))
        self.mic = LevelMeter(tm, "Micro (entrée)")
        self.voice = LevelMeter(tm, "Voix de Jarvis (sortie)")
        layout.addWidget(self.mic)
        layout.addWidget(self.voice)
        self.note = label("Niveaux issus du pipeline audio existant : aucun second accès au micro.", "dim", wrap=True)
        layout.addWidget(self.note)
        self.mute = button("Couper le micro")
        self.mute.clicked.connect(self._toggle_mic)
        self.stop = button("Interrompre")
        self.stop.clicked.connect(self._stop)
        layout.addLayout(hrow(self.mute, self.stop, None))
        layout.addStretch(1)
        self._decay_timer = None
        self._last_level_at = 0.0

    def on_activate(self) -> None:
        hub = self.context.hub
        self.listen(hub.level_changed, self._on_level)
        self.listen(hub.presence_changed, lambda _s: self._update_labels())
        self.listen(hub.engine_changed, lambda _s: self._update_labels())
        self._update_labels()
        self.set_state(STATE_READY)

    def on_deactivate(self) -> None:
        self._decay_timer = None  # libéré par _release
        self.mic.level = self.voice.level = 0.0

    def _ensure_decay(self) -> None:
        if self._decay_timer is None and self.is_active:
            self._decay_timer = self.every(33, self._decay)

    def _decay(self) -> None:
        idle = True
        for meter in (self.mic, self.voice):
            meter.level *= 0.82
            meter.peak = max(0.0, meter.peak - 0.02)
            if meter.level > 0.01 or meter.peak > 0.01:
                idle = False
            meter.update()
        if idle and time.monotonic() - self._last_level_at > 0.6 and self._decay_timer is not None:
            # Plus rien ne bouge : le minuteur s'arrête (coût nul au repos).
            self._decay_timer.stop()
            self._timers.remove(self._decay_timer)
            self._decay_timer.deleteLater()
            self._decay_timer = None

    def _on_level(self, value: float, source: str) -> None:
        meter = self.voice if source == "output" else self.mic
        meter.level = max(meter.level, float(value))
        meter.peak = max(meter.peak, float(value))
        self._last_level_at = time.monotonic()
        meter.update()
        self._ensure_decay()

    def _update_labels(self) -> None:
        from UI import menu_state

        presence = self.context.hub.presence()
        self.presence.setText(PRESENCE_LABELS.get(presence, presence))
        self.presence_dot.set_status({"listening": "ok", "speaking": "ok", "thinking": "warning"}.get(presence, "unknown"))
        enabled = menu_state.LIVE.get_mic_enabled()
        listen = menu_state.LIVE.get_listen_mode()
        self.mic_state.setText(("Micro actif" if enabled else "Micro coupé") + (" · écoute continue" if listen else ""))
        self.mute.setText("Couper le micro" if enabled else "Rétablir le micro")
        snap = self.context.hub.engine()
        if snap.get("audio_state") == "failed":
            self.note.setText(f"Audio indisponible : {snap.get('audio_detail') or 'échec du démarrage'}")

    def _toggle_mic(self) -> None:
        if not self.context.confirm("mic.toggle", "Changer l'état du micro ?", ""):
            return
        result = mic_toggle()
        self.context.toast(result.message, "success" if result.success else "error")
        self._update_labels()

    def _stop(self) -> None:
        result = voice_stop()
        self.context.toast(result.message, "info" if result.success else "warning")


# ---------------------------------------------------------------------------
# Modèles IA et routage
# ---------------------------------------------------------------------------


class ModelsWidget(DashboardWidget):
    """Informations RÉELLES de routage ; sélecteur manuel visible mais verrouillé."""

    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.live = label("", wrap=True)
        self.live_state = label("", "dim", wrap=True)
        layout.addWidget(label("Conversation (temps réel)", "dim"))
        layout.addWidget(self.live)
        layout.addWidget(self.live_state)
        layout.addWidget(label("Tâches d'arrière-plan (routage automatique)", "dim"))
        self.routing = label("", wrap=True)
        layout.addWidget(self.routing)
        self.quota = label("", "dim", wrap=True)
        layout.addWidget(self.quota)
        self.selector = QComboBox()
        self.selector.setEnabled(False)
        self.selector.setToolTip("Sélection manuelle verrouillée : le routage automatique reste actif.")
        self.lock = label("⊘ Sélection manuelle verrouillée — le routage automatique reste actif.", "dim", wrap=True)
        layout.addWidget(self.selector)
        layout.addWidget(self.lock)
        layout.addStretch(1)

    def on_activate(self) -> None:
        self.listen(self.context.hub.engine_changed, lambda _s: self.refresh())
        self.listen(self.context.hub.tasks_attached, lambda _s: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        snap = self.context.hub.engine()
        model = snap.get("model") or ""
        if not model:
            try:
                from src.settings import DEFAULT_MODEL

                configured = DEFAULT_MODEL
            except Exception:
                configured = ""
            self.live.setText(configured + " (configuré par défaut — non confirmé par une session)" if configured
                              else "Aucun modèle publié")
        else:
            self.live.setText(model)
        self.live_state.setText(f"État : {ENGINE_LABELS.get(snap.get('engine_state'), '?')}")
        self.selector.clear()
        self.selector.addItem(model or "Modèle courant (non publié)")
        self.run_job(self.context.hub.tasks.model_status, on_done=self._on_status,
                     on_error=lambda exc: self.routing.setText(f"Indisponible : {exc}"))

    def _on_status(self, info: dict) -> None:
        if not info.get("available"):
            self.routing.setText(info.get("reason") or "Task Manager non initialisé.")
            self.quota.setText("")
            self.set_state(STATE_READY)
            return
        live = info.get("live") or {}
        live_model = live.get("model") or "non découvert (découverte au premier besoin)"
        validated = " (validé)" if live.get("validated") else ""
        self.routing.setText(
            f"Simple / normale → {live_model}{validated}\n"
            f"Complexe → {info.get('complex_model') or '—'}\n"
            f"Tâches simultanées max : {info.get('max_concurrent_tasks') or '—'}"
        )
        quota = info.get("quota")
        if quota:
            self.quota.setText(
                f"Quota modèle complexe : {quota.get('calls_today')}/{quota.get('daily_limit')} appels aujourd'hui · "
                f"{quota.get('calls_last_minute')}/{quota.get('rpm_limit')} par minute · "
                f"{quota.get('tokens_last_minute')}/{quota.get('tpm_limit')} tokens/min"
            )
        else:
            self.quota.setText("Quota : " + (info.get("quota_error") or "non initialisé"))
        self.set_state(STATE_READY)


# ---------------------------------------------------------------------------
# Actions rapides
# ---------------------------------------------------------------------------


class QuickActionsWidget(DashboardWidget):
    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        self._layout.addWidget(self._grid_host)
        self._layout.addStretch(1)
        self._buttons: dict[str, tuple[QPushButton, object]] = {}
        self._running: set[str] = set()

    def _host(self) -> ActionHost:
        controller = self.context.controller
        return ActionHost(
            set_interface_mode=getattr(controller, "set_interface_mode", None),
            current_interface_mode=getattr(controller, "current_interface_mode", None),
            open_path=self.context.open_path,
        )

    def on_activate(self) -> None:
        self.listen(self.context.hub.interface_mode_changed, lambda _m: self._update_enabled())
        self.refresh()

    def refresh(self) -> None:
        self.run_job(build_actions, self._host(), on_done=self._build,
                     on_error=lambda exc: self.set_state("error", str(exc)))

    def on_theme_changed(self, theme) -> None:
        # Glyphes peints à la couleur du texte du thème (correctif D01) : repeints.
        for btn, action in self._buttons.values():
            if getattr(action, "icon", ""):
                btn.setIcon(glyph_icon(action.icon, theme.text, 16))
        super().on_theme_changed(theme)

    def _build(self, actions) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._buttons.clear()
        show_routines = bool(self.settings.get("show_routines", True))
        show_protocols = bool(self.settings.get("show_protocols", False))
        visible = [a for a in actions if (a.group != "Routines" or show_routines)
                   and (a.group != "Protocoles" or show_protocols)]
        columns = 2
        for index, action in enumerate(visible):
            btn = QPushButton(action.label)
            if action.icon:
                btn.setIcon(glyph_icon(action.icon, self.context.theme.text, 16))
            btn.setToolTip(action.description)
            btn.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            btn.clicked.connect(lambda _=False, a=action: self._trigger(a))
            self._grid.addWidget(btn, index // columns, index % columns)
            self._buttons[action.key] = (btn, action)
        self._update_enabled()
        self.set_state(STATE_READY if visible else STATE_EMPTY)

    def _update_enabled(self) -> None:
        for key, (btn, action) in self._buttons.items():
            try:
                ok, reason = action.availability()
            except Exception as exc:
                ok, reason = False, str(exc)
            policy = self.context.hub.security.policy(action.action_id)
            level = self.context.hub.security.effective_level(action.action_id)
            badge = {2: " · confirmation", 3: " · destructif"}.get(level, "")
            btn.setEnabled(ok and key not in self._running)
            btn.setToolTip((action.description + badge) if ok else f"Indisponible : {reason}")
            btn.setProperty("level", policy.level)

    def _trigger(self, action) -> None:
        if not self.context.confirm(action.action_id, f"{action.label} ?", action.description):
            return
        self._running.add(action.key)
        self._update_enabled()
        if action.blocking:
            self.run_job(action.run, on_done=lambda r, a=action: self._done(a, r),
                         on_error=lambda e, a=action: self._failed(a, e))
        else:
            try:
                result = action.run()
            except Exception as exc:
                self._failed(action, exc)
                return
            self._done(action, result)

    def _done(self, action, result) -> None:
        self._running.discard(action.key)
        self.context.toast(result.message, "success" if result.success else "error")
        self._update_enabled()

    def _failed(self, action, exc) -> None:
        self._running.discard(action.key)
        self.context.toast(f"{action.label} : échec ({exc})", "error")
        self._update_enabled()

    def actions(self) -> list[WidgetAction]:
        return [WidgetAction("refresh", "Recharger les routines", self.refresh)]
