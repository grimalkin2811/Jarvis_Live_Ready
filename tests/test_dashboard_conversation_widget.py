"""Widget Conversation : envoi non bloquant, concurrence, hors-ligne, rendu."""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from src.runtime_status import RUNTIME, EngineState  # noqa: E402
from tests.dashboard_support import FakeTextEngine, isolated_jarvis, wait_until  # noqa: E402
from UI.dashboard.config import ConfigStore  # noqa: E402
from UI.dashboard.hub import DashboardHub  # noqa: E402
from UI.dashboard.theme import ThemeManager  # noqa: E402
from UI.dashboard.widget_api import WidgetContext  # noqa: E402
from UI.dashboard.widgets.conversation_widget import ConversationWidget  # noqa: E402


@pytest.fixture
def setup(tmp_path):
    app = QApplication.instance() or QApplication([])
    from src.conversation import get_default_conversation_context

    with isolated_jarvis():
        RUNTIME.reset()
        context = get_default_conversation_context()
        context.start_new_conversation("test")
        store = ConfigStore(tmp_path / "s.json")
        hub = DashboardHub(store)
        hub.start()
        toasts = []
        widget = ConversationWidget(WidgetContext(hub=hub, theme_manager=ThemeManager(store), instance_id="w",
                                                  toast=lambda m, l="info": toasts.append((m, l)),
                                                  confirm=lambda *a: True))
        widget.resize(600, 500)
        widget.show()
        widget.activate()
        yield {"app": app, "widget": widget, "context": context, "hub": hub, "toasts": toasts}
        widget.dispose()
        hub.shutdown()
        RUNTIME.reset()


def _ready(engine):
    RUNTIME.set_engine_state(EngineState.READY)
    RUNTIME.register_text_handler(engine)


def test_offline_send_is_refused_with_real_reason_and_text_kept(setup):
    widget = setup["widget"]
    assert not widget.send_button.isEnabled()
    assert "pas démarré" in widget.status.text()
    widget.input.setPlainText("Bonjour")
    widget.send()
    assert widget.banner.isVisible() and "Hors ligne" in widget.banner.text()
    assert widget.input.toPlainText() == "Bonjour"
    assert setup["context"].get_messages() == ()


def test_send_success_uses_shared_context_and_clears_input(setup):
    engine = FakeTextEngine(setup["context"], reply="Il fait beau.")
    _ready(engine)
    widget = setup["widget"]
    assert wait_until(setup["app"], widget.send_button.isEnabled, 2)
    widget.input.setPlainText("Quel temps fait-il ?")
    started = time.monotonic()
    widget.send()
    assert time.monotonic() - started < 0.2, "l'envoi ne bloque jamais le thread graphique"
    assert wait_until(setup["app"], lambda: widget.input.toPlainText() == "", 2)
    assert engine.calls == ["Quel temps fait-il ?"]
    assert wait_until(setup["app"], lambda: "Il fait beau." in widget.view.toPlainText(), 3)
    roles = [m.role for m in setup["context"].get_messages()]
    assert roles == ["user", "assistant"], "une seule source de vérité : le contexte partagé"


def test_concurrent_send_is_handled_explicitly(setup):
    engine = FakeTextEngine(setup["context"], mode="hold")
    _ready(engine)
    widget = setup["widget"]
    widget.input.setPlainText("Premier")
    widget.send()
    assert widget._pending and not widget.send_button.isEnabled()
    widget.input.setPlainText("Second")
    widget.send()
    assert engine.calls == ["Premier"], "aucun second envoi tant que le premier n'est pas résolu"
    assert "déjà en cours" in widget.banner.text()
    engine.release(ok=True)
    assert wait_until(setup["app"], lambda: not widget._pending, 2)
    assert widget.input.toPlainText() == "Second", "le texte saisi entre-temps n'est jamais perdu"


def test_engine_busy_refusal_keeps_text_and_explains(setup):
    engine = FakeTextEngine(setup["context"], mode="busy")
    _ready(engine)
    widget = setup["widget"]
    widget.input.setPlainText("Interromps-toi")
    widget.send()
    assert wait_until(setup["app"], lambda: widget.banner.isVisible(), 2)
    assert "occupé" in widget.banner.text() and "texte conservé" in widget.banner.text()
    assert widget.input.toPlainText() == "Interromps-toi"


def test_send_failure_is_reported_as_error(setup):
    engine = FakeTextEngine(setup["context"], mode="fail")
    _ready(engine)
    widget = setup["widget"]
    widget.input.setPlainText("x")
    widget.send()
    assert wait_until(setup["app"], lambda: "Envoi interrompu" in widget.banner.text(), 2)


def test_too_long_message_is_refused_locally(setup):
    engine = FakeTextEngine(setup["context"])
    _ready(engine)
    widget = setup["widget"]
    widget.input.setPlainText("x" * 4001)
    widget.send()
    assert engine.calls == []
    assert "trop long" in widget.banner.text().lower() or "maximum" in widget.banner.text()


def test_voice_turns_and_partial_transcripts_are_rendered(setup):
    widget = setup["widget"]
    hub = setup["hub"]
    hub.handle_voice_event("transcript", {"text": "je parle à la voix", "final": False})
    assert wait_until(setup["app"], lambda: "je parle à la voix" in widget.view.toPlainText(), 2)
    hub.handle_voice_event("response", {"text": "Réponse en cours"})
    assert wait_until(setup["app"], lambda: "Réponse en cours" in widget.view.toPlainText(), 2)
    setup["context"].add_user_message("je parle à la voix")
    setup["context"].add_assistant_message("Réponse finale")
    assert wait_until(setup["app"], lambda: "Réponse finale" in widget.view.toPlainText(), 2)
    assert "Réponse en cours…" not in widget.view.toPlainText()


def test_failed_turn_is_flagged_and_html_is_escaped(setup):
    widget = setup["widget"]
    setup["context"].add_user_message("<script>alert(1)</script>")
    setup["context"].fail_open_turn("coupure")
    assert wait_until(setup["app"], lambda: "sans réponse" in widget.view.toPlainText(), 2)
    assert "<script>" in widget.view.toPlainText()  # affiché comme texte, jamais interprété
    assert "<script>" not in widget.view.toHtml()
