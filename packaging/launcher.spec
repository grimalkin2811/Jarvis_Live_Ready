# -*- mode: python ; coding: utf-8 -*-
# PyInstaller SPEC pour JarvisLauncher.exe.
#
# Le launcher est volontairement minimal : bibliothèque standard + src.updater
# / src.version / src.paths. Il ne doit surtout PAS embarquer PySide6, l'audio,
# Gemini ni openwakeword — sinon il deviendrait lourd et fragile.

import os

base_dir = os.path.dirname(os.path.abspath(SPEC))
root = os.path.dirname(base_dir)

icon = os.path.join(root, "assets", "jarvis.ico")
if not os.path.isfile(icon):
    icon = None

a = Analysis(
    ["../launcher/main.py"],
    pathex=[root],
    binaries=[],
    datas=[],
    hiddenimports=[
        "src.updater",
        "src.version",
        "src.paths",
        "src.packaging_validation",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "PySide6",
        "sounddevice",
        "openwakeword",
        "onnxruntime",
        "google",
        "numpy",
        "psutil",
        "pycaw",
        "comtypes",
        "tkinter",
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
    console=True,
    disable_windowed_traceback=False,
    icon=icon,
)
