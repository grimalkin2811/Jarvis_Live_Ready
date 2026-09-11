# -*- mode: python ; coding: utf-8 -*-
# PyInstaller SPEC pour JarvisLauncher.exe.
#
# Depuis la 1.1.1, le launcher embarque une interface graphique PySide6
# (launcher/gui.py), la logique restant dans launcher/core.py (stdlib) et
# src.updater. Il n'embarque toujours NI l'audio, NI Gemini, NI openwakeword.
#
# L'exécutable est compilé en mode "windowed" (console=False) : double-cliqué,
# il n'ouvre que la fenêtre du launcher. Les modes console (--check,
# --validate, --no-gui, ...) rattachent automatiquement le terminal parent
# (voir launcher/main.py::_attach_windows_console).

import os

base_dir = os.path.dirname(os.path.abspath(SPEC))
root = os.path.dirname(base_dir)

icon = os.path.join(root, "assets", "jarvis.ico")
if not os.path.isfile(icon):
    icon = None

# Icône réutilisée DANS la fenêtre (QIcon) : copiée à la racine du bundle.
datas = []
icon_data = os.path.join(root, "assets", "jarvis.ico")
if os.path.isfile(icon_data):
    datas.append((icon_data, "."))

a = Analysis(
    ["../launcher/main.py"],
    pathex=[root],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "launcher.core",
        "launcher.gui",
        "src.updater",
        "src.version",
        "src.paths",
        "src.packaging_validation",
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "sounddevice",
        "openwakeword",
        "onnxruntime",
        "google",
        "numpy",
        "psutil",
        "pycaw",
        "comtypes",
        "tkinter",
        "matplotlib",
        "pandas",
        "IPython",
        "notebook",
        "scipy",
        "sklearn",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtNetwork",
        "PySide6.QtSql",
        "PySide6.QtXml",
        "PySide6.QtMultimedia",
        "PySide6.QtBluetooth",
        "PySide6.QtNfc",
        "PySide6.QtPositioning",
        "PySide6.QtSensors",
        "PySide6.QtSerialPort",
        "PySide6.QtTest",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="JarvisLauncher",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=icon,
)
