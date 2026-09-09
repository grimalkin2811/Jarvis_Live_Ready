"""Notifications locales, sans réseau ni dépendance Qt côté backend.

L'UI s'abonne au bus et relaie les messages dans le thread graphique. En mode
console Windows, une file de bulles natives prend le relais (PowerShell livré
avec Windows). La console et le bip restent disponibles en dernier recours.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import subprocess
import threading

_LOCK = threading.RLock()
_HOOKS: list = []
_WINDOWS_QUEUE: queue.Queue = queue.Queue(maxsize=32)
_WINDOWS_WORKER: threading.Thread | None = None


def add_hook(hook) -> None:
    """``hook(title, message)`` doit être rapide (par exemple un signal Qt)."""
    with _LOCK:
        if hook not in _HOOKS:
            _HOOKS.append(hook)


def remove_hook(hook) -> None:
    with _LOCK:
        if hook in _HOOKS:
            _HOOKS.remove(hook)


def _beep() -> None:
    try:
        import winsound

        winsound.MessageBeep()
    except Exception:
        pass


def _windows_balloon(title: str, message: str) -> None:
    # Le texte n'est jamais interpolé comme du code PowerShell. Même des
    # guillemets/apostrophes dans un rappel restent de simples données JSON.
    data = base64.b64encode(
        json.dumps(
            {"title": title[:63], "message": message[:255]}, ensure_ascii=False
        ).encode("utf-8")
    ).decode("ascii")
    script = (
        f"$p = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{data}')) | ConvertFrom-Json; "
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "try { $n.Icon = [System.Drawing.SystemIcons]::Information; $n.Visible = $true; "
        "$n.ShowBalloonTip(8000, $p.title, $p.message, [System.Windows.Forms.ToolTipIcon]::Info); "
        "[System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Seconds 8; "
        "} finally { $n.Visible = $false; $n.Dispose() }"
    )
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Sta",
            "-EncodedCommand",
            encoded,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=12,
        check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _windows_worker() -> None:
    while True:
        title, message = _WINDOWS_QUEUE.get()
        try:
            _windows_balloon(title, message)
        except Exception as exc:
            print(
                f"[Notifications] Bulle indisponible (message conservé en console) : {exc}"
            )
        finally:
            _WINDOWS_QUEUE.task_done()


def publish(title: str, message: str) -> str:
    """Publie une notification ; renvoie le canal choisi, pas une preuve de lecture."""
    global _WINDOWS_WORKER
    title = str(title or "Jarvis").strip()[:63]
    message = str(message).strip()
    # ASCII volontairement : les runners Windows peuvent utiliser CP1252 et
    # l'emoji de notification faisait échouer les tests avec UnicodeEncodeError.
    print(f"\n[Jarvis] [Notification] {title} : {message}")
    with _LOCK:
        hooks = list(_HOOKS)
    delivered = False
    for hook in hooks:
        try:
            hook(title, message)
            delivered = True
        except Exception:
            pass
    if delivered:
        return "interface"

    _beep()
    if os.name == "nt":
        with _LOCK:
            if _WINDOWS_WORKER is None or not _WINDOWS_WORKER.is_alive():
                _WINDOWS_WORKER = threading.Thread(
                    target=_windows_worker,
                    name="jarvis-notifications",
                    daemon=True,
                )
                _WINDOWS_WORKER.start()
            try:
                _WINDOWS_QUEUE.put_nowait((title, message))
                return "windows"
            except queue.Full:
                pass
    return "console"
