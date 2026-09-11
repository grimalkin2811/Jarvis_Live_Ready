"""Interface graphique du launcher Jarvis (PySide6).

Fenêtre principale du ``JarvisLauncher.exe`` : affiche la version installée,
l'état de l'installation, vérifie et installe les mises à jour GitHub, puis
lance Jarvis. Toute la logique métier vit dans ``launcher/core.py`` (sans Qt)
et ``src/updater.py`` ; ce module ne fait que l'affichage et l'orchestration
des tâches de fond (``QThread``) pour ne jamais bloquer l'interface.

Utilisation :
    python -m launcher.main            # GUI par défaut (sans arguments)
    python -m launcher.main --gui      # GUI forcée
"""

from __future__ import annotations

import contextlib
import os
import sys
import traceback
from pathlib import Path
from typing import Any

# Racine du dépôt sur sys.path (lancement direct `python launcher/gui.py`).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from launcher import core  # noqa: E402


# ---------------------------------------------------------------------------
# Thème (identité visuelle Jarvis : fond sombre, accent cyan)
# ---------------------------------------------------------------------------

ACCENT = "#35e0ff"
BACKGROUND = "#0d1117"
PANEL = "#161b22"
TEXT = "#e6edf3"
MUTED = "#8b949e"
SUCCESS = "#3fb950"
WARNING = "#d29922"
ERROR = "#f85149"

STYLESHEET = f"""
QWidget {{
    background-color: {BACKGROUND};
    color: {TEXT};
    font-family: "Segoe UI", "Inter", sans-serif;
    font-size: 13px;
}}
QLabel#Title {{
    font-size: 30px;
    font-weight: 800;
    letter-spacing: 8px;
    color: {ACCENT};
}}
QLabel#Subtitle {{
    font-size: 12px;
    color: {MUTED};
    letter-spacing: 2px;
}}
QLabel#VersionBadge {{
    font-size: 12px;
    font-weight: 600;
    color: {BACKGROUND};
    background-color: {ACCENT};
    border-radius: 9px;
    padding: 3px 12px;
}}
QLabel#StatusDot {{
    font-size: 16px;
    font-weight: bold;
}}
QLabel#StatusText {{
    font-size: 13px;
}}
QLabel#FieldName {{
    color: {MUTED};
    font-size: 12px;
}}
QLabel#FieldValue {{
    font-size: 12px;
}}
QPushButton {{
    background-color: {PANEL};
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 9px 14px;
    font-weight: 600;
}}
QPushButton:hover:!disabled {{
    border-color: {ACCENT};
}}
QPushButton:disabled {{
    color: #484f58;
    border-color: #21262d;
}}
QPushButton#Primary {{
    background-color: {ACCENT};
    color: #06232b;
    border: none;
    font-size: 14px;
    padding: 11px 14px;
}}
QPushButton#Primary:hover:!disabled {{
    background-color: #6ceaff;
}}
QPushButton#Primary:disabled {{
    background-color: #1b3a42;
    color: #6e7681;
}}
QComboBox {{
    background-color: {PANEL};
    border: 1px solid #30363d;
    border-radius: 8px;
    padding: 7px 10px;
}}
QComboBox QAbstractItemView {{
    background-color: {PANEL};
    selection-background-color: #1f6feb;
}}
QProgressBar {{
    border: 1px solid #30363d;
    border-radius: 6px;
    background-color: {PANEL};
    text-align: center;
    height: 16px;
}}
QProgressBar::chunk {{
    background-color: {ACCENT};
    border-radius: 5px;
}}
QPlainTextEdit {{
    background-color: #010409;
    border: 1px solid #30363d;
    border-radius: 8px;
    font-family: "Cascadia Mono", Consolas, monospace;
    font-size: 11px;
    color: #c9d1d9;
}}
"""


# ---------------------------------------------------------------------------
# Tâches de fond
# ---------------------------------------------------------------------------

class _SignalWriter(QObject):
    """File texte redirigeant ``print()`` vers un signal Qt (thread-safe)."""

    line_received = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._buffer = ""

    def write(self, text: str) -> int:
        self._buffer += str(text)
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self.line_received.emit(line.strip())
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self.line_received.emit(self._buffer.strip())
            self._buffer = ""


class Worker(QThread):
    """Exécute une fonction bloquante (réseau, installation) en tâche de fond."""

    finished = Signal(object)
    failed = Signal(str)
    progressed = Signal(int, object)
    logged = Signal(str)

    def __init__(self, fn, *args: Any, parent: QObject | None = None, **kwargs: Any) -> None:
        super().__init__(parent)
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self._writer = _SignalWriter()
        self._writer.line_received.connect(self.logged.emit)

    def set_kwarg(self, key: str, value: Any) -> None:
        """Ajoute un argument nommé avant ``start()`` (ex. rappel de progression)."""
        self._kwargs[key] = value

    def progress_callback(self, received: int, total: int | None) -> None:
        self.progressed.emit(int(received), total)

    def run(self) -> None:  # noqa: D102 (override Qt)
        try:
            with contextlib.redirect_stdout(self._writer), contextlib.redirect_stderr(self._writer):
                result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:
            traceback.print_exc()
            self.failed.emit(f"{exc}")
        else:
            self.finished.emit(result)


# ---------------------------------------------------------------------------
# Fenêtre principale
# ---------------------------------------------------------------------------

LAUNCH_MODES = (
    ("Orbe (recommandé)", "ui"),
    ("Overlay bureau", "desktop"),
    ("Console (diagnostic)", "console"),
)


def _find_icon() -> Path | None:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False) and getattr(sys, "_MEIPASS", None):
        candidates.append(Path(str(sys._MEIPASS)) / "jarvis.ico")  # type: ignore[attr-defined]
        candidates.append(core.install_dir() / "jarvis.ico")
    candidates.append(Path(__file__).resolve().parents[1] / "assets" / "jarvis.ico")
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


class LauncherWindow(QWidget):
    """Fenêtre principale du launcher."""

    def __init__(self, *, auto_check: bool = True, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._auto_check = auto_check
        self._worker: Worker | None = None
        self._closing = False
        self._pending_plan: dict | None = None
        self.setWindowTitle("Jarvis Launcher")
        self.setMinimumSize(460, 560)
        self.resize(500, 620)
        self.setStyleSheet(STYLESHEET)
        icon = _find_icon()
        if icon is not None:
            self.setWindowIcon(QIcon(str(icon)))
        self._build_ui()
        self._refresh_static_info()
        if self._auto_check:
            QTimer.singleShot(600, self.check_updates)
        # Crochet de test (sans effet en usage normal) : ferme la fenetre
        # apres N secondes pour valider le launcher package en CI/smoke test.
        try:
            auto_close = int(os.environ.get("JARVIS_LAUNCHER_AUTOCLOSE", "0").strip() or "0")
        except ValueError:
            auto_close = 0
        if auto_close > 0:
            QTimer.singleShot(auto_close * 1000, self.close)

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        # En-tête : titre + badge version.
        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("JARVIS")
        title.setObjectName("Title")
        subtitle = QLabel("L A U N C H E R")
        subtitle.setObjectName("Subtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box)
        header.addStretch(1)
        self._version_badge = QLabel("…")
        self._version_badge.setObjectName("VersionBadge")
        self._version_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header.addWidget(self._version_badge, alignment=Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        # Statut : pastille + texte.
        status_row = QHBoxLayout()
        self._status_dot = QLabel("●")
        self._status_dot.setObjectName("StatusDot")
        self._status_text = QLabel("Initialisation…")
        self._status_text.setObjectName("StatusText")
        self._status_text.setWordWrap(True)
        status_row.addWidget(self._status_dot)
        status_row.addWidget(self._status_text, 1)
        root.addLayout(status_row)

        # Informations (grille).
        grid = QGridLayout()
        grid.setColumnStretch(1, 1)
        self._latest_label = QLabel("—")
        self._latest_label.setObjectName("FieldValue")
        self._latest_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        path_label = QLabel("Dossier")
        path_label.setObjectName("FieldName")
        self._path_value = QLabel("—")
        self._path_value.setObjectName("FieldValue")
        self._path_value.setWordWrap(True)
        self._path_value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        latest_title = QLabel("Dernière version")
        latest_title.setObjectName("FieldName")
        grid.addWidget(latest_title, 0, 0)
        grid.addWidget(self._latest_label, 0, 1)
        grid.addWidget(path_label, 1, 0)
        grid.addWidget(self._path_value, 1, 1)
        root.addLayout(grid)

        # Mode de lancement.
        mode_row = QHBoxLayout()
        mode_label = QLabel("Mode :")
        mode_label.setObjectName("FieldName")
        self._mode_combo = QComboBox()
        for label, value in LAUNCH_MODES:
            self._mode_combo.addItem(label, value)
        self._mode_combo.setToolTip("Choisissez comment Jarvis démarre.")
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self._mode_combo, 1)
        root.addLayout(mode_row)

        # Boutons d'action.
        self._launch_button = QPushButton("Lancer Jarvis")
        self._launch_button.setObjectName("Primary")
        self._launch_button.clicked.connect(self.launch_jarvis)
        root.addWidget(self._launch_button)

        secondary = QHBoxLayout()
        self._check_button = QPushButton("Vérifier les mises à jour")
        self._check_button.clicked.connect(self.check_updates)
        self._update_button = QPushButton("Mettre à jour")
        self._update_button.setEnabled(False)
        self._update_button.clicked.connect(self.install_update)
        secondary.addWidget(self._check_button)
        secondary.addWidget(self._update_button)
        root.addLayout(secondary)

        quit_row = QHBoxLayout()
        quit_row.addStretch(1)
        self._quit_button = QPushButton("Quitter")
        self._quit_button.clicked.connect(self.close)
        quit_row.addWidget(self._quit_button)
        root.addLayout(quit_row)

        # Progression (cachée au repos).
        self._progress = QProgressBar()
        self._progress.setVisible(False)
        root.addWidget(self._progress)

        # Journal.
        log_title = QLabel("Journal")
        log_title.setObjectName("FieldName")
        root.addWidget(log_title)
        self._log_view = QPlainTextEdit()
        self._log_view.setReadOnly(True)
        self._log_view.setMaximumBlockCount(1000)
        root.addWidget(self._log_view, 1)

    # -- état initial ------------------------------------------------------

    def _refresh_static_info(self) -> None:
        status = core.get_status()
        self._version_badge.setText(f"v{status.local_version}")
        self._path_value.setText(status.app_path or status.install_path)
        if not status.install_ok:
            self._set_status("error", "Installation invalide — voir le journal.")
            self.append_log(status.install_message)
            self._launch_button.setEnabled(False)
            self._check_button.setEnabled(True)
        else:
            self._set_status("idle", f"Prêt — Jarvis {status.local_version}.")
            if status.jarvis_running:
                self.append_log("Remarque : Jarvis semble déjà en cours d'exécution.")
        self.append_log(f"Version installée : {status.local_version} (canal {status.channel})")
        self.append_log(f"Dossier : {status.install_path}")

    # -- helpers d'affichage ------------------------------------------------

    def _set_status(self, level: str, text: str) -> None:
        colors = {"idle": MUTED, "busy": ACCENT, "ok": SUCCESS, "warn": WARNING, "error": ERROR}
        color = colors.get(level, MUTED)
        self._status_dot.setStyleSheet(f"color: {color};")
        self._status_text.setText(text)

    def append_log(self, text: str) -> None:
        try:
            self._log_view.appendPlainText(str(text))
        except Exception:
            pass

    def _set_busy(self, busy: bool, *, indeterminate: bool = False) -> None:
        for button in (self._launch_button, self._check_button, self._quit_button):
            button.setEnabled(not busy)
        # Le bouton de mise à jour dépend aussi de la disponibilité d'un plan.
        self._update_button.setEnabled((not busy) and self._pending_plan is not None)
        self._mode_combo.setEnabled(not busy)
        if busy:
            self._progress.setVisible(True)
            if indeterminate:
                self._progress.setRange(0, 0)
            else:
                self._progress.setRange(0, 100)
                self._progress.setValue(0)
        else:
            self._progress.setVisible(False)

    def _start_worker(self, fn, *args: Any, **kwargs: Any) -> Worker:
        self._stop_worker()
        worker = Worker(fn, *args, parent=self, **kwargs)
        worker.logged.connect(self.append_log)
        self._worker = worker
        return worker

    def _stop_worker(self) -> None:
        worker, self._worker = self._worker, None
        if worker is not None:
            try:
                worker.logged.disconnect(self.append_log)
            except Exception:
                pass
            if worker.isRunning():
                worker.wait(100)

    @property
    def _busy(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    # -- actions ------------------------------------------------------------

    def check_updates(self) -> None:
        """Vérifie les mises à jour en tâche de fond."""
        if self._busy:
            return
        self._pending_plan = None
        self._update_button.setEnabled(False)
        self._update_button.setText("Mettre à jour")
        self._set_status("busy", "Vérification des mises à jour…")
        self._set_busy(True, indeterminate=True)
        self.append_log("Vérification des mises à jour…")
        worker = self._start_worker(core.check_update)
        worker.finished.connect(self._on_check_finished)
        worker.failed.connect(self._on_check_failed)
        worker.start()

    def _on_check_finished(self, result: Any) -> None:
        self._stop_worker()  # libère _busy avant de réactiver l'UI
        self._set_busy(False)
        if not isinstance(result, core.UpdateCheck):
            self._set_status("error", "Réponse inattendue du service de mise à jour.")
            return
        self.append_log(result.message)
        if not result.ok:
            self._set_status("warn", result.message)
            return
        if result.update_available:
            self._pending_plan = result.plan
            self._latest_label.setText(result.latest)
            self._update_button.setText(f"Mettre à jour vers {result.latest}")
            self._update_button.setEnabled(True)
            self._set_status("warn", f"Mise à jour disponible : {result.message}")
        else:
            self._latest_label.setText(result.latest or "—")
            self._set_status("ok", result.message)

    def _on_check_failed(self, message: str) -> None:
        self._stop_worker()  # libère _busy avant de réactiver l'UI
        self._set_busy(False)
        self._set_status("error", f"Vérification impossible : {message}")
        self.append_log(f"Échec de la vérification : {message}")

    def install_update(self) -> None:
        """Installe la mise à jour disponible (avec confirmation)."""
        if self._busy or self._pending_plan is None:
            return
        plan = self._pending_plan
        latest = str(plan.get("latest", "?"))
        answer = QMessageBox.question(
            self,
            "Mettre à jour Jarvis",
            f"Mettre à jour Jarvis vers {latest} ?\n\n"
            "L'application sera remplacée ; vos données sont préservées.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.append_log("Mise à jour annulée.")
            return
        self._set_status("busy", f"Téléchargement de la version {latest}…")
        self._set_busy(True)
        worker = self._start_worker(core.install_update, plan)
        # Le rappel de progression est injecté après création (le worker
        # expose un signal thread-safe).
        worker.set_kwarg("progress", worker.progress_callback)
        worker.progressed.connect(self._on_download_progress)
        worker.finished.connect(self._on_install_finished)
        worker.failed.connect(self._on_install_failed)
        worker.start()

    def _on_download_progress(self, received: int, total: Any) -> None:
        try:
            total_int = int(total) if total else 0
        except (TypeError, ValueError):
            total_int = 0
        if total_int > 0:
            self._progress.setRange(0, 100)
            self._progress.setValue(min(100, int(received * 100 / total_int)))
            mb_received = received / (1024 * 1024)
            mb_total = total_int / (1024 * 1024)
            self._set_status("busy", f"Téléchargement… {mb_received:.1f} / {mb_total:.1f} Mo")
        else:
            self._progress.setRange(0, 0)
            self._set_status("busy", f"Téléchargement… {received / (1024 * 1024):.1f} Mo")

    def _on_install_finished(self, result: Any) -> None:
        self._stop_worker()  # libère _busy avant de réactiver l'UI
        self._set_busy(False)
        if not isinstance(result, core.UpdateResult):
            self._set_status("error", "Réponse inattendue pendant l'installation.")
            return
        self.append_log(result.message)
        if not result.ok:
            self._set_status("error", "Échec de la mise à jour — voir le journal.")
            QMessageBox.critical(self, "Mise à jour impossible", result.message)
            return
        self._pending_plan = None
        self._update_button.setEnabled(False)
        self._update_button.setText("Mettre à jour")
        status = core.get_status()
        self._version_badge.setText(f"v{status.local_version}")
        self._latest_label.setText(status.local_version)
        self._set_status("ok", result.message)
        QMessageBox.information(self, "Mise à jour terminée", result.message)

    def _on_install_failed(self, message: str) -> None:
        self._stop_worker()  # libère _busy avant de réactiver l'UI
        self._set_busy(False)
        self._set_status("error", "Échec de la mise à jour — voir le journal.")
        self.append_log(f"Échec de l'installation : {message}")
        QMessageBox.critical(self, "Mise à jour impossible", message)

    def launch_jarvis(self) -> None:
        """Lance Jarvis puis ferme le launcher en cas de succès."""
        if self._busy:
            return
        mode = str(self._mode_combo.currentData() or "ui")
        if mode == "console":
            answer = QMessageBox.question(
                self,
                "Lancer Jarvis en console",
                "Le mode console bloque le launcher tant que Jarvis tourne.\nContinuer ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._set_status("busy", "Lancement de Jarvis…")
        self._set_busy(True, indeterminate=True)
        self.append_log(f"Lancement de Jarvis (mode {mode})…")
        wait = mode == "console"
        worker = self._start_worker(core.launch_jarvis, mode, wait=wait)
        worker.finished.connect(self._on_launch_finished)
        worker.failed.connect(self._on_launch_failed)
        worker.start()

    def _on_launch_finished(self, result: Any) -> None:
        self._stop_worker()  # libère _busy avant de réactiver l'UI
        self._set_busy(False)
        if not isinstance(result, core.LaunchResult):
            self._set_status("error", "Réponse inattendue au lancement.")
            return
        self.append_log(result.message)
        if not result.ok:
            self._set_status("error", "Lancement impossible — voir le journal.")
            QMessageBox.critical(self, "Lancement impossible", result.message)
            return
        self._set_status("ok", f"{result.message} Fermeture du launcher…")
        # Succès : Jarvis tourne dans son propre processus, le launcher a
        # terminé son rôle. On laisse le statut visible un instant.
        QTimer.singleShot(1500, self._quit_after_launch)

    def _on_launch_failed(self, message: str) -> None:
        self._stop_worker()  # libère _busy avant de réactiver l'UI
        self._set_busy(False)
        self._set_status("error", "Lancement impossible — voir le journal.")
        self.append_log(f"Échec du lancement : {message}")
        QMessageBox.critical(self, "Lancement impossible", message)

    def _quit_after_launch(self) -> None:
        if not self._busy:
            self._closing = True
            self.close()

    # -- fermeture ----------------------------------------------------------

    def closeEvent(self, event) -> None:  # noqa: D102 (override Qt)
        if self._busy and not self._closing:
            QMessageBox.information(
                self,
                "Opération en cours",
                "Veuillez attendre la fin de l'opération avant de quitter.",
            )
            event.ignore()
            return
        self._stop_worker()
        super().closeEvent(event)


# ---------------------------------------------------------------------------
# Point d'entrée GUI
# ---------------------------------------------------------------------------

def run_gui(*, auto_check: bool = True) -> int:
    """Ouvre la fenêtre du launcher et retourne le code de sortie Qt."""
    app = QApplication.instance() or QApplication(sys.argv)
    try:
        window = LauncherWindow(auto_check=auto_check)
    except Exception:
        traceback.print_exc()
        return 1
    window.show()
    print("[Launcher] Interface graphique prête.")
    try:
        return int(app.exec())
    except Exception:
        traceback.print_exc()
        return 1
    finally:
        # Nettoie proprement le flux de logs redirigé éventuel.
        try:
            sys.stdout.flush()
        except Exception:
            pass
        try:
            sys.stderr.flush()
        except Exception:
            pass


if __name__ == "__main__":  # pragma: no cover - lancement direct en dev
    raise SystemExit(run_gui())
