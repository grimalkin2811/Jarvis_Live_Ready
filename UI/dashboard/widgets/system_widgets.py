"""Widgets « Système » : journal, ressources, notifications, diagnostics."""

from __future__ import annotations

import logging
import os
import time

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QListWidget,
    QListWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from ..diagnostics import STATUS_LABELS, build_report, collect_checks
from ..log_buffer import LogEntry
from ..resources import describe_sample, human_bytes
from ..widget_api import STATE_EMPTY, STATE_READY, STATE_UNAVAILABLE, DashboardWidget, WidgetAction
from .common import ElidedLabel, Sparkline, button, hrow, label, set_role

LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}
LEVEL_GLYPH = {"DEBUG": "·", "INFO": "•", "WARNING": "▲", "ERROR": "✖", "CRITICAL": "✖"}


class ActivityWidget(DashboardWidget):
    """Journal applicatif + faits structurés (tâches, moteur, outils)."""

    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setUniformItemSizes(False)
        self.list.setWordWrap(True)
        layout.addWidget(self.list, 1)
        self.pause = button("Pause")
        self.pause.setCheckable(True)
        self.pause.toggled.connect(lambda checked: self.pause.setText("Reprendre" if checked else "Pause"))
        # Pied de widget : réduit avec « … » (texte complet en infobulle)
        # plutôt que tronqué net quand le widget est étroit.
        self.info = ElidedLabel("")
        set_role(self.info, "dim")
        layout.addLayout(hrow(self.info, None, self.pause))

    def _min_level(self) -> int:
        return LEVELS.get(str(self.settings.get("level", "info")), logging.INFO)

    def _source(self) -> str | None:
        source = str(self.settings.get("source", "all"))
        return None if source == "all" else source

    def _accept(self, entry: LogEntry) -> bool:
        return entry.levelno >= self._min_level() and (self._source() is None or entry.category == self._source())

    def on_activate(self) -> None:
        self.listen(self.context.hub.log_entry, self._on_entry)
        self.refresh()

    def refresh(self) -> None:
        entries = self.context.hub.logs.entries(min_level=self._min_level(), category=self._source(), limit=200)
        self.list.clear()
        for entry in reversed(entries):
            self.list.addItem(self._item(entry))
        persistent = os.environ.get("JARVIS_ACTIVITY_ENABLED", "").strip() in {"1", "true", "oui"}
        info = (f"{len(entries)} entrée(s) · journal persistant "
                f"{'activé' if persistent else 'désactivé (JARVIS_ACTIVITY_ENABLED=1)'}")
        self.info.setText(info)
        self.info.setToolTip(info)
        self.set_state(STATE_READY if entries else STATE_EMPTY)
        if not entries:
            self.list.addItem(QListWidgetItem("Aucune entrée pour ce filtre depuis l'ouverture du Dashboard."))

    def _item(self, entry: LogEntry) -> QListWidgetItem:
        stamp = time.strftime("%H:%M:%S", time.localtime(entry.created))
        item = QListWidgetItem(f"{LEVEL_GLYPH.get(entry.level, '•')} {stamp}  [{entry.source}]  {entry.message}")
        theme = self.context.theme
        if entry.levelno >= logging.ERROR:
            item.setForeground(theme.danger)
        elif entry.levelno >= logging.WARNING:
            item.setForeground(theme.warning)
        return item

    def _on_entry(self, entry: LogEntry) -> None:
        if self.pause.isChecked() or not self._accept(entry):
            return
        if self.state != STATE_READY:
            self.refresh()
            return
        self.list.insertItem(0, self._item(entry))
        while self.list.count() > 300:
            self.list.takeItem(self.list.count() - 1)

    def actions(self) -> list[WidgetAction]:
        return [WidgetAction("copy", "Copier le journal affiché", self._copy)]

    def _copy(self) -> None:
        text = "\n".join(self.list.item(i).text() for i in range(self.list.count()))
        QGuiApplication.clipboard().setText(text)
        self.context.toast("Journal copié (secrets masqués).", "success")


class ResourcesWidget(DashboardWidget):
    """CPU / RAM / disque / réseau : mesures psutil réelles, suspendables."""

    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        tm = context.theme_manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)
        self.values: dict[str, object] = {}
        self.graphs: dict[str, Sparkline] = {}
        for row, (key, title, graph) in enumerate((("cpu", "Processeur", True), ("ram", "Mémoire vive", True),
                                                   ("disk", "Disque", False), ("net", "Réseau", False))):
            grid.addWidget(label(title, "dim"), row, 0)
            value = label("—", "title")
            grid.addWidget(value, row, 1)
            self.values[key] = value
            if graph:
                spark = Sparkline(tm)
                spark.setMinimumHeight(18)
                spark.setMaximumHeight(34)
                grid.addWidget(spark, row, 2)
                self.graphs[key] = spark
            else:
                detail = label("", "dim")
                grid.addWidget(detail, row, 2)
                self.values[f"{key}_detail"] = detail
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)
        layout.addStretch(1)
        self.process = label("", "dim", wrap=True)
        self.pause = button("Suspendre")
        self.pause.setCheckable(True)
        self.pause.toggled.connect(self._on_pause)
        layout.addLayout(hrow(self.process, None, self.pause))
        self._key = f"widget:{context.instance_id}"
        self._subscribed = False

    def on_activate(self) -> None:
        hub = self.context.hub
        if not hub.resources.available():
            self.set_state(STATE_UNAVAILABLE, "psutil indisponible : mesures impossibles.")
            return
        if not hub.store.config.monitoring.enabled:
            self.set_state(STATE_UNAVAILABLE, "Surveillance désactivée (Paramètres → Fermeture & surveillance).")
            return
        self.listen(hub.resource_sample, self._on_sample)
        self._render_history()
        if not self.pause.isChecked():
            self._subscribe()
        self.set_state(STATE_READY)

    def on_deactivate(self) -> None:
        self._unsubscribe()

    def on_dispose(self) -> None:
        self._unsubscribe()

    def _subscribe(self) -> None:
        if not self._subscribed:
            self._subscribed = self.context.hub.subscribe_resources(self._key)

    def _unsubscribe(self) -> None:
        if self._subscribed:
            self.context.hub.unsubscribe_resources(self._key)
            self._subscribed = False

    def _on_pause(self, paused: bool) -> None:
        self.pause.setText("Reprendre" if paused else "Suspendre")
        if paused:
            self._unsubscribe()
            self.set_subtitle("en pause")
        elif self.is_active:
            self._subscribe()
            self.set_subtitle("")

    def refresh(self) -> None:
        if self.is_active:
            self.deactivate()
            self.activate()

    def _render_history(self) -> None:
        history = self.context.hub.resources.history()
        if history:
            self._on_sample(history[-1])
        else:
            self.process.setText("En attente de la première mesure réelle…")

    def _on_sample(self, sample) -> None:
        if self.pause.isChecked():
            return
        info = describe_sample(sample)
        history = self.context.hub.resources.history()[-60:]
        self.values["cpu"].setText(f"{sample.cpu_percent:.0f} %")
        self.values["ram"].setText(f"{sample.ram_percent:.0f} %")
        self.values["disk"].setText("n/d" if sample.disk_percent < 0 else f"{sample.disk_percent:.0f} %")
        self.values["disk_detail"].setText("" if sample.disk_percent < 0 else f"libre {human_bytes(sample.disk_free)}")
        self.values["net"].setText(f"↓ {human_bytes(sample.net_recv_rate)}/s")
        self.values["net_detail"].setText(f"↑ {human_bytes(sample.net_sent_rate)}/s")
        self.graphs["cpu"].set_values([s.cpu_percent for s in history])
        self.graphs["ram"].set_values([s.ram_percent for s in history])
        self.process.setText(f"{info['process']} · toutes les {self.context.hub.resources.interval:.0f} s")


class NotificationsWidget(DashboardWidget):
    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        layout.addWidget(self.list, 1)
        read = button("Tout marquer comme lu")
        read.clicked.connect(self._read_all)
        clear = button("Vider")
        clear.clicked.connect(self._clear)
        prefs = button("Préférences…")
        prefs.clicked.connect(lambda: self.context.open_page("parametres"))
        layout.addLayout(hrow(read, clear, None, prefs))

    def on_activate(self) -> None:
        self.listen(self.context.hub.notifications_changed, self.refresh)
        self.refresh()

    def refresh(self) -> None:
        center = self.context.hub.notifications
        items = list(reversed(center.items()))
        self.list.clear()
        theme = self.context.theme
        for item in items:
            stamp = time.strftime("%H:%M", time.localtime(item.created_at))
            # Mot plutôt que « ✓ » : un symbole absent de la police d'interface
            # fait charger toutes les polices du système sous Windows (D01).
            native = " · natif : envoyée" if item.native_sent else (f" · natif : {item.native_error}" if item.native_requested and item.native_error else "")
            entry = QListWidgetItem(f"{'●' if not item.read else '○'} {stamp}  {item.title}\n{item.message}{native}")
            if item.level == "error":
                entry.setForeground(theme.danger)
            elif item.level == "warning":
                entry.setForeground(theme.warning)
            elif item.level == "success":
                entry.setForeground(theme.success)
            self.list.addItem(entry)
        unread = center.unread_count()
        self.set_subtitle(f"{unread} non lue(s)" if unread else "")
        self.set_state(STATE_READY if items else STATE_EMPTY)
        if not items:
            self.list.addItem(QListWidgetItem("Aucune notification pour l'instant."))

    def _read_all(self) -> None:
        self.context.hub.notifications.mark_all_read()
        self.refresh()

    def _clear(self) -> None:
        if not self.context.confirm("notifications.clear", "Vider les notifications du Dashboard ?", ""):
            return
        count = self.context.hub.notifications.clear()
        self.context.toast(f"{count} notification(s) retirée(s).", "success")
        self.refresh()


class DiagnosticsWidget(DashboardWidget):
    """Santé des composants, preuves, rapport copiable (sans secret)."""

    def __init__(self, context, settings=None) -> None:
        super().__init__(context, settings)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        layout.addWidget(self.tree, 1)
        self.perf = label("", "dim", wrap=True)
        layout.addWidget(self.perf)
        refresh = button("Actualiser")
        refresh.clicked.connect(self.refresh)
        integrity = button("Intégrité mémoire")
        integrity.clicked.connect(self._integrity)
        copy = button("Copier le rapport")
        copy.clicked.connect(self._copy_report)
        save = button("Enregistrer…")
        save.clicked.connect(self._save_report)
        update = button("Mises à jour")
        update.setToolTip("Vérifie la dernière version publiée sur GitHub (mécanisme existant du launcher).")
        update.clicked.connect(self._check_update)
        layout.addLayout(hrow(refresh, integrity, copy, save, update, None))
        self._checks = []
        self._report = ""

    def on_activate(self) -> None:
        self.listen(self.context.hub.engine_changed, lambda _s: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        hub = self.context.hub
        store = hub.store
        snapshot = hub.engine()
        tasks_available = hub.tasks_available()

        def _collect():
            from UI import menu_state

            counts = hub.tasks.counts() if tasks_available else None
            checks = collect_checks(
                snapshot,
                memory_stats=hub.memory.stats,
                memory_status=hub.memory.status,
                tasks=(tasks_available, hub.tasks.unavailable_reason(), counts),
                config=(store.load_status, store.load_notes, store.path, store.last_save_ok),
                mic_enabled=menu_state.LIVE.get_mic_enabled(),
            )
            return checks

        self.run_job(_collect, on_done=self._on_checks, on_error=lambda e: self.set_state("error", str(e)))

    def _on_checks(self, checks) -> None:
        self._checks = checks
        self.tree.clear()
        theme = self.context.theme
        worst = "ok"
        order = {"ok": 0, "unknown": 1, "warning": 2, "error": 3}
        for check in checks:
            item = QTreeWidgetItem([f"{STATUS_LABELS.get(check.status, check.status)} — {check.label} : {check.detail}"])
            item.setForeground(0, theme.status_color(check.status))
            for evidence in check.evidence:
                child = QTreeWidgetItem([evidence])
                child.setForeground(0, theme.text_dim)
                item.addChild(child)
            self.tree.addTopLevelItem(item)
            if order.get(check.status, 1) > order.get(worst, 0):
                worst = check.status
        self.tree.expandToDepth(0 if self.settings.get("expanded", False) else -1)
        controller = self.context.controller
        perf = controller.performance_snapshot() if controller is not None and hasattr(controller, "performance_snapshot") else {}
        if perf:
            self.perf.setText(
                f"Dashboard : {perf.get('active_widgets', 0)} widget(s) actif(s) / {perf.get('widgets', 0)} · "
                f"minuteurs actifs {perf.get('active_timers', 0)} · travaux en attente {perf.get('pending_jobs', 0)} · "
                f"mesures ressources {'actives' if perf.get('resource_thread') else 'arrêtées'} · "
                f"processus {human_bytes(perf.get('rss', 0))}"
            )
        self._report = build_report(checks, self.context.hub.engine(), {"Dashboard": self.perf.text()})
        self.set_subtitle({"ok": "tout est OK", "warning": "attention", "error": "erreur", "unknown": "incomplet"}[worst])
        self.set_state(STATE_READY)

    def _integrity(self) -> None:
        def _done(result: dict) -> None:
            if result.get("success") and result.get("ok"):
                self.context.toast("Base mémoire intègre (PRAGMA quick_check = ok).", "success")
            elif result.get("success"):
                self.context.toast("Anomalies détectées : " + "; ".join(result.get("messages", [])[:3]), "error")
            else:
                self.context.toast(result.get("error") or "Vérification impossible.", "error")

        self.run_job(self.context.hub.memory.integrity, on_done=_done, on_error=lambda e: self.context.toast(str(e), "error"))

    def _copy_report(self) -> None:
        QGuiApplication.clipboard().setText(self._report)
        self.context.toast("Rapport copié (aucune clé ni secret).", "success")

    def _save_report(self) -> None:
        name = f"jarvis-diagnostic-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        path, _ = QFileDialog.getSaveFileName(self, "Enregistrer le rapport", name, "Texte (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(self._report)
        except OSError as exc:
            self.context.toast(f"Enregistrement impossible : {exc}", "error")
            return
        self.context.toast("Rapport enregistré.", "success")

    def _check_update(self) -> None:
        controller = self.context.controller
        if controller is None or not hasattr(controller, "check_for_update"):
            self.context.toast("Vérification indisponible.", "warning")
            return
        if not self.context.confirm("update.check", "Vérifier les mises à jour ?",
                                    "Interroge l'API GitHub publique (aucune donnée personnelle envoyée)."):
            return
        controller.check_for_update(manual=True)

    def actions(self) -> list[WidgetAction]:
        return [WidgetAction("copy", "Copier le rapport", self._copy_report)]
