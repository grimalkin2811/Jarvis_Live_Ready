"""Relais des notifications backend vers le thread Qt, y compris en veille."""

from __future__ import annotations

from collections import deque

from PySide6.QtCore import QObject, Qt, QTimer, Signal, Slot
from PySide6.QtWidgets import QApplication, QLabel, QSystemTrayIcon

from src import notifications


class NotificationBridge(QObject):
    received = Signal(str, str)

    def __init__(self, tray=None, parent=None) -> None:
        super().__init__(parent)
        self.tray = tray
        self.pending = deque(maxlen=32)
        self.popup = None
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._show_next)
        self.received.connect(self._receive, Qt.QueuedConnection)
        self._hook = self.received.emit
        notifications.add_hook(self._hook)

    @Slot(str, str)
    def _receive(self, title: str, message: str) -> None:
        if self._closed:
            return
        self.pending.append((title, message))
        if not self._timer.isActive():
            self._show_next()

    @Slot()
    def _show_next(self) -> None:
        if self.popup is not None:
            self.popup.hide()
        if not self.pending or self._closed:
            return
        title, message = self.pending.popleft()
        if self.tray is not None and QSystemTrayIcon.supportsMessages():
            # Les bulles Windows sont limitées à 255 caractères. Le message
            # complet reste toujours visible dans la console de Jarvis.
            clipped = message if len(message) <= 255 else message[:254] + "…"
            self.tray.showMessage(title, clipped, QSystemTrayIcon.Information, 8000)
        else:
            # Sans zone de notification : une carte non modale, sans prendre
            # le focus ni interrompre la conversation ou l'application active.
            if self.popup is None:
                self.popup = QLabel()
                self.popup.setWindowFlags(
                    Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                )
                self.popup.setAttribute(Qt.WA_ShowWithoutActivating)
                self.popup.setTextFormat(Qt.PlainText)
                self.popup.setWordWrap(True)
                self.popup.setFixedWidth(360)
                self.popup.setMargin(18)
                self.popup.setStyleSheet(
                    "background: #142332; color: #e4f2ff; border: 1px solid #417086; "
                    "border-radius: 10px; font-size: 14px;"
                )
            self.popup.setText(f"{title}\n\n{message}")
            self.popup.adjustSize()
            screen = QApplication.primaryScreen()
            if screen is not None:
                area = screen.availableGeometry()
                self.popup.move(
                    area.right() - self.popup.width() - 20,
                    area.bottom() - self.popup.height() - 20,
                )
            self.popup.show()
            QApplication.beep()
        # Sérialiser : deux routines à la même heure ne s'écrasent pas.
        self._timer.start(8500)

    def close(self) -> None:
        self._closed = True
        notifications.remove_hook(self._hook)
        self._timer.stop()
        self.pending.clear()
        if self.popup is not None:
            self.popup.close()
            self.popup.deleteLater()
            self.popup = None
