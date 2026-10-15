"""Widget « Conversation » : dialogue écrit avec la MÊME session que la voix.

* Historique : lu dans le ``ConversationContext`` partagé (source unique —
  aucune copie locale), rafraîchi par ses écouteurs de changement.
* Envoi : ``RUNTIME.submit_text`` → ``GeminiLive.submit_text`` sur la boucle
  du moteur. Le thread graphique ne bloque jamais : la confirmation (``Future``
  résolu sur la boucle du moteur) revient par le relais durable du hub
  (``JobRunner.watch_future``), jamais par un signal de ce widget — qui peut
  être détruit avant la confirmation (Dashboard fermé, fenêtre libérée).
* Concurrence explicite : un seul envoi en vol ; un refus « occupé » du
  moteur conserve le texte saisi et explique pourquoi.
* Hors ligne : envoi désactivé avec la raison réelle (état du moteur) ;
  aucune réponse simulée.
* La réponse de Jarvis arrive par le chemin normal : elle est **dite à voix
  haute** (modèle audio natif) et sa transcription s'affiche ici.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QTextBrowser, QVBoxLayout

from src.runtime_status import EngineState, EngineUnavailableError

from ..widget_api import STATE_EMPTY, STATE_READY, STATE_UNAVAILABLE, DashboardWidget, WidgetAction
from .common import StatusDot, button, esc, hrow, label

MAX_INPUT_CHARS = 4000


class _Input(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and not (event.modifiers() & Qt.ShiftModifier):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class ConversationWidget(DashboardWidget):
    overlay_states = ()

    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        self._pending = False
        self._awaiting_reply = False
        self._live_user = ""
        self._live_assistant = ""
        self._last_signature = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.dot = StatusDot(context.theme_manager)
        self.status = label("", "dim", wrap=True)
        self.context_info = label("", "dim")
        self.context_info.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        row = hrow(self.dot, self.status, self.context_info)
        row.setStretch(1, 1)
        layout.addLayout(row)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setOpenLinks(False)
        layout.addWidget(self.view, 1)
        self.banner = label("", "warning", wrap=True)
        self.banner.hide()
        layout.addWidget(self.banner)
        self.input = _Input()
        self.input.setPlaceholderText("Écrire à Jarvis…")
        self.input.setMaximumHeight(84)
        self.input.setTabChangesFocus(True)
        self.input.submitted.connect(self.send)
        self.input.textChanged.connect(self._on_text_changed)
        self.send_button = button("Envoyer", "primary", "Entrée pour envoyer, Maj+Entrée pour aller à la ligne")
        self.send_button.clicked.connect(self.send)
        self.stop_button = button("■", tooltip="Interrompre la réponse vocale en cours")
        self.stop_button.clicked.connect(self._stop)
        layout.addLayout(hrow(self.input, self.send_button, self.stop_button, stretch_last=False))
        self.hint = label("La réponse est aussi prononcée à voix haute (même session que la voix).", "dim", wrap=True)
        layout.addWidget(self.hint)
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(60)
        self._render_timer.timeout.connect(self.render)
        self._update_availability()

    # -- cycle de vie --------------------------------------------------------------------

    def on_activate(self) -> None:
        hub = self.context.hub
        self.listen(hub.conversation_changed, self._on_conversation_changed)
        self.listen(hub.engine_changed, lambda _snap: self._update_availability())
        self.listen(hub.voice_event, self._on_voice_event)
        self.listen(hub.presence_changed, lambda _state: self._update_status())
        self._update_availability()
        self.render(force=True)

    def on_deactivate(self) -> None:
        self._render_timer.stop()

    def refresh(self) -> None:
        self.render(force=True)

    def actions(self) -> list[WidgetAction]:
        return [WidgetAction("conversation.new", "Nouvelle conversation…", self._new_conversation)]

    # -- disponibilité -------------------------------------------------------------------

    def _update_availability(self) -> None:
        runtime = self.context.hub.runtime
        reason = runtime.unavailable_reason()
        snap = self.context.hub.engine()
        available = not reason
        self.send_button.setEnabled(available and not self._pending)
        self.send_button.setToolTip("Entrée pour envoyer, Maj+Entrée pour aller à la ligne" if available else reason)
        state = snap.get("engine_state")
        if available:
            self.dot.set_status("ok")
        elif state in (EngineState.STARTING, EngineState.CONNECTING, EngineState.NOT_STARTED):
            self.dot.set_status("unknown")
        else:
            self.dot.set_status("error" if state in (EngineState.AUTH_ERROR, EngineState.FAILED) else "warning")
        self._offline_reason = reason
        self._update_status()

    def _update_status(self) -> None:
        if self._pending:
            text = "Envoi…"
        elif self._offline_reason:
            text = self._offline_reason
        elif self._awaiting_reply or self.context.hub.presence() in ("thinking",):
            text = "Jarvis réfléchit…" if not self._live_assistant else "Jarvis répond…"
        elif self.context.hub.presence() == "speaking":
            text = "Jarvis répond…"
        else:
            text = "Prêt — écris ou parle à Jarvis."
        self.status.setText(text)

    # -- envoi ---------------------------------------------------------------------------

    def _on_text_changed(self) -> None:
        length = len(self.input.toPlainText())
        if length > MAX_INPUT_CHARS:
            self._show_banner(f"{length} caractères : maximum {MAX_INPUT_CHARS}.", "error")
        elif self.banner.isVisible() and self.banner.property("role") == "error" and "maximum" in self.banner.text():
            self.banner.hide()

    def _show_banner(self, text: str, level: str = "warning") -> None:
        self.banner.setText(text)
        self.banner.setProperty("role", "error" if level == "error" else "warning")
        self.banner.style().unpolish(self.banner)
        self.banner.style().polish(self.banner)
        self.banner.show()

    @Slot()
    def send(self) -> None:
        text = self.input.toPlainText().strip()
        if not text:
            return
        if self._pending:
            self._show_banner("Un message est déjà en cours d'envoi : attends sa confirmation.")
            return
        if len(text) > MAX_INPUT_CHARS:
            self._show_banner(f"Message trop long ({len(text)} caractères, maximum {MAX_INPUT_CHARS}).", "error")
            return
        try:
            future = self.context.hub.runtime.submit_text(text)
        except EngineUnavailableError as exc:
            self._show_banner(f"Hors ligne : {exc}", "error")
            return
        except Exception as exc:  # pragma: no cover - défensif
            self._show_banner(f"Envoi impossible : {exc}", "error")
            return
        self._pending = True
        self._sent_text = text
        self.send_button.setEnabled(False)
        self.banner.hide()
        self._update_status()
        # Relais du hub (vit tant que Jarvis tourne) : si ce widget est détruit
        # avant la confirmation, rien n'est émis vers lui depuis le thread du
        # moteur et ``_on_send_result`` est simplement ignoré.
        self.context.hub.jobs.watch_future(future, on_done=self._on_send_result, owner=self)

    @Slot(object)
    def _on_send_result(self, future) -> None:
        self._pending = False
        try:
            exc = future.exception()
        except Exception as error:  # annulé
            exc = error
        if exc is None:
            # Accepté par le moteur : le message est DÉJÀ dans le contexte.
            if self.input.toPlainText().strip() == getattr(self, "_sent_text", None):
                self.input.clear()
            self._awaiting_reply = True
            self.banner.hide()
        else:
            code = getattr(exc, "code", "")
            prefix = {
                "busy": "Jarvis est occupé",
                "not_connected": "Hors ligne",
                "not_ready": "Session en préparation",
                "send_failed": "Envoi interrompu",
                "loop_unavailable": "Moteur arrêté",
                "too_long": "Message trop long",
                "empty": "Message vide",
            }.get(code, "Envoi impossible")
            self._show_banner(f"{prefix} : {exc} (texte conservé)", "warning" if code == "busy" else "error")
        self._update_availability()
        self.render(force=True)

    def _stop(self) -> None:
        from ..actions import voice_stop

        result = voice_stop()
        self.context.toast(result.message, "info" if result.success else "warning")

    def _new_conversation(self) -> None:
        from ..actions import conversation_new

        if not self.context.confirm("conversation.new", "Démarrer une nouvelle conversation ?",
                                    "Le contexte conversationnel est effacé. La mémoire persistante est conservée."):
            return
        result = conversation_new()
        self.context.toast(result.message, "success" if result.success else "error")

    # -- évènements vocaux réels ------------------------------------------------------------

    def _on_conversation_changed(self, event: dict) -> None:
        reason = (event or {}).get("reason")
        if reason in ("assistant_message", "turn_closed", "turn_failed", "conversation_reset"):
            self._awaiting_reply = False
            self._live_assistant = ""
            self._live_user = ""
        elif reason == "user_message":
            self._live_user = ""
        self._render_timer.start()
        self._update_status()

    def _on_voice_event(self, name: str, payload: dict) -> None:
        if name == "response":
            self._live_assistant = str(payload.get("text") or "")
            self._render_timer.start()
        elif name == "transcript" and not payload.get("final"):
            self._live_user = str(payload.get("text") or "")
            self._render_timer.start()
        elif name in ("interrupted", "error"):
            self._awaiting_reply = False
            self._live_assistant = ""
            self._render_timer.start()
        self._update_status()

    # -- rendu ---------------------------------------------------------------------------

    def render(self, force: bool = False) -> None:
        context = self.context.hub.conversation()
        if context is None:
            self.set_state(STATE_UNAVAILABLE, "Contexte conversationnel indisponible.")
            return
        messages = context.get_messages()
        limit = int(self.settings.get("max_messages", 100) or 100)
        show_tools = bool(self.settings.get("show_tools", True))
        signature = (len(messages), messages[-1] if messages else None, self._live_user, self._live_assistant,
                     show_tools, limit, self.context.theme.spec.id, self.context.theme.accent.name())
        if not force and signature == self._last_signature:
            return
        self._last_signature = signature
        try:
            snap = context.snapshot()
            self.context_info.setText(f"{snap.get('turns', 0)} tour(s) · ~{snap.get('estimated_tokens', 0)} tokens")
            enabled = snap.get("enabled", True)
        except Exception:
            enabled = True
        bar = self.view.verticalScrollBar()
        at_bottom = bar.value() >= bar.maximum() - 24
        self.view.setHtml(self._html(messages[-limit:], show_tools, enabled))
        if at_bottom or force:
            self.view.moveCursor(QTextCursor.End)
            bar.setValue(bar.maximum())
        has_content = bool(messages) or self._live_user or self._live_assistant
        self.set_state(STATE_READY if has_content else STATE_EMPTY)

    def _html(self, messages, show_tools: bool, enabled: bool) -> str:
        t = self.context.theme
        text = t.text.name()
        dim = t.text_dim.name()
        accent = t.accent
        user_bg = {
            "jarvis": accent.darker(260).name(),
            "edith": accent.darker(300).name(),
            "friday": t.border.darker(260).name(),
        }.get(t.spec.id, accent.darker(260).name())
        bot_bg = t.panel_alt.name()
        rows = [f"<body style='color:{text};'>"]
        if not enabled:
            rows.append(f"<p style='color:{dim}'>Le contexte conversationnel est désactivé "
                        "(JARVIS_CONTEXT_ENABLED=0) : l'historique n'est pas conservé.</p>")
        if not messages and not self._live_user and not self._live_assistant:
            rows.append(f"<p style='color:{dim}; margin-top:24px' align='center'>Aucun échange dans cette conversation."
                        "<br>Écris un message ci-dessous ou parle à Jarvis : les deux alimentent le même fil.</p>")
        for message in messages:
            stamp = time.strftime("%H:%M", time.localtime(message.created_at))
            if message.role == "tool":
                if not show_tools or message.kind != "tool_result":
                    continue
                rows.append(f"<p style='color:{dim}; margin:2px 0 2px 12px'>⚙ {esc(message.text[:220])}</p>")
                continue
            body = esc(message.text).replace("\n", "<br>")
            if message.role == "user":
                failed = (f"<br><span style='color:{t.warning.name()}'>⚠ sans réponse (tour interrompu)</span>"
                          if message.failed else "")
                rows.append(
                    "<table width='100%' cellspacing='0' cellpadding='0'><tr><td width='18%'></td>"
                    f"<td align='right'><table cellpadding='8' bgcolor='{user_bg}'><tr><td>{body}{failed}"
                    f"<br><span style='color:{dim}; font-size:small'>Vous · {stamp}</span></td></tr></table>"
                    "</td></tr></table>"
                )
            else:
                rows.append(
                    "<table width='100%' cellspacing='0' cellpadding='0'><tr><td>"
                    f"<table cellpadding='8' bgcolor='{bot_bg}'><tr><td>{body}"
                    f"<br><span style='color:{dim}; font-size:small'>Jarvis · {stamp}</span></td></tr></table>"
                    "</td><td width='18%'></td></tr></table>"
                )
        if self._live_user:
            rows.append(f"<p align='right' style='color:{dim}'><i>{esc(self._live_user)}…</i></p>")
        if self._live_assistant:
            rows.append(f"<p style='color:{dim}'><i>{esc(self._live_assistant)}…</i></p>")
        rows.append("</body>")
        return "".join(rows)
