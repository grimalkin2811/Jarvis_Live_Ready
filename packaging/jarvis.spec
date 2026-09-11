# -*- mode: python ; coding: utf-8 -*-
# PyInstaller SPEC pour l'application Jarvis.
#
# Produit un dossier onedir (`dist/Jarvis/Jarvis.exe` + `_internal/`) qui est
# ensuite assemblé en archive portable par `scripts/build_windows.ps1` et
# installé par `packaging/installer.iss`.

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

import os

# Ressources internes au bundle.
datas = []
binaries = []
hiddenimports = []

# openwakeword : modèles + binaires + hooks internes.
for pkg in ("openwakeword",):
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

# google.genai : sous-modules dynamiques (client, types, live) non détectés
# statiquement par modulegraph.
hiddenimports += collect_submodules("google.genai")
hiddenimports += collect_submodules("google.genai.live")

# Validation de packaging (utilisé par --smoke-test et updater)
hiddenimports += ["src.packaging_validation", "src.updater", "src.version", "src.paths", "src.wakeword"]

# openwakeword télécharge parfois ses modèles dans resources/models ;
# on les récupère s'ils existent déjà (CI les télécharge avant le build).
datas += collect_data_files("openwakeword", include_py_files=False)

# Ressources du dépôt (modèles téléchargés par scripts/download_models.py).
base_dir = os.path.dirname(os.path.abspath(SPEC))
root = os.path.dirname(base_dir)
resources_dir = os.path.join(root, "resources")
if os.path.isdir(resources_dir):
    for f in os.listdir(resources_dir):
        full = os.path.join(resources_dir, f)
        if os.path.isdir(full):
            datas.append((full, f"resources/{f}"))

# CORRECTIF 1.1.1 (bug OpenWakeWord du build distribué) : openWakeWord résout
# ses modèles de pré-traitement (melspectrogram.*, embedding_model.*)
# UNIQUEMENT depuis le dossier du paquet ``openwakeword/resources/models``,
# qui est vide dans l'environnement de build (les modèles sont téléchargés
# vers ``resources/openwakeword/``, pas dans site-packages). Sans cette copie,
# le bundle contient le wake word mais pas son pré-traitement, et le
# chargement échoue avec ``NO_SUCHFILE ... melspectrogram.onnx``.
# On copie donc chaque modèle téléchargé vers l'emplacement du paquet dans le
# bundle, en PLUS de ``resources/openwakeword`` (utilisé par la résolution
# explicite de src/wakeword.py). Ainsi, même la résolution par défaut
# d'openWakeWord fonctionne dans l'application packagée.
oww_resources = os.path.join(resources_dir, "openwakeword")
if os.path.isdir(oww_resources):
    for entry in sorted(os.listdir(oww_resources)):
        full = os.path.join(oww_resources, entry)
        if os.path.isfile(full):
            datas.append((full, "openwakeword/resources/models"))

# Icône de l'application.
icon = os.path.join(root, "assets", "jarvis.ico")
if not os.path.isfile(icon):
    icon = None

a = Analysis(
    ["../run_jarvis.py"],
    pathex=[root, os.path.join(root, "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "matplotlib",
        "pandas",
        "IPython",
        "notebook",
        "PySide6.QtWebEngineCore",
        "PySide6.QtWebEngineWidgets",
        "PySide6.QtQml",
        "PySide6.QtQuick",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Jarvis",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,  # console visible en mode headless ; l'UI reste utilisable.
    disable_windowed_traceback=False,
    icon=icon,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Jarvis",
)
