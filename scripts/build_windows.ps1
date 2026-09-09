# Build Windows de Jarvis (application + launcher + archive portable).
#
# À utiliser localement (Windows avec Python) ou en CI (windows-latest).
#
# Étapes :
#   1. télécharger les ressources OpenWakeWord ;
#   2. construire l'application avec PyInstaller (onedir) ;
#   3. construire le launcher avec PyInstaller (onefile) ;
#   4. assembler l'archive portable `Jarvis-v<version>-portable.zip` ;
#   5. calculer l'empreinte SHA-256.
#
# Résultat dans `dist/` :
#   Jarvis/                      (dossier onedir de l'application)
#   JarvisLauncher.exe
#   Jarvis-v<version>-portable.zip
#   Jarvis-v<version>-portable.zip.sha256
#   version.json

param(
    [switch]$SkipModels,
    [string]$DistDir = "dist"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)  # racine du dépôt

Write-Host "=== JARVIS - BUILD WINDOWS ===" -ForegroundColor Cyan

# --- Version (source unique de vérité : src/version.py) ---
$Version = python -c "import sys; sys.path.insert(0, '.'); from src.version import __version__; print(__version__)"
if ($LASTEXITCODE -ne 0) { throw "Impossible de lire src.version.__version__" }
$Version = $Version.Trim()
$Channel = python -c "import sys; sys.path.insert(0, '.'); from src.version import CHANNEL; print(CHANNEL)"
$Channel = $Channel.Trim()
Write-Host "Version : $Version (canal : $Channel)"

if (-not (Test-Path ".venv")) {
    Write-Host "[build] Création de l'environnement virtuel..."
    python -m venv .venv
}
$venvPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) { $venvPython = (Join-Path (Get-Location) ".venv\Scripts\python.exe") }
Write-Host "Python venv : $venvPython"

# Installer les dépendances de build.
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt
& $venvPython -m pip install pyinstaller

# --- Modèles OpenWakeWord (sauf si -SkipModels) ---
if (-not $SkipModels) {
    Write-Host "[build] Téléchargement des modèles OpenWakeWord..."
    & $venvPython scripts/download_models.py
    if ($LASTEXITCODE -ne 0) { throw "Téléchargement des modèles OpenWakeWord échoué." }
} else {
    Write-Host "[build] Téléchargement des modèles ignoré (-SkipModels)."
}

# --- Construction (dossier temporaire pour éviter de polluer dist) ---
$work = Join-Path $DistDir ".build"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }

Write-Host "[build] Construction de l'application (PyInstaller onedir)..."
& $venvPython -m PyInstaller packaging/jarvis.spec --distpath $DistDir --workpath $work --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "Échec PyInstaller (application)." }

Write-Host "[build] Construction du launcher (PyInstaller onefile)..."
& $venvPython -m PyInstaller packaging/launcher.spec --distpath $DistDir --workpath $work --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "Échec PyInstaller (launcher)." }

# --- Assembler l'archive portable ---
$appDir = Join-Path $DistDir "app"
if (Test-Path $appDir) { Remove-Item $appDir -Recurse -Force }
# L'application onedir produit dist/Jarvis/contenu ; on copie ce contenu dans app/.
$onedir = Join-Path $DistDir "Jarvis"
if (-not (Test-Path (Join-Path $onedir "Jarvis.exe"))) { throw "Jarvis.exe introuvable dans $onedir" }

# Copier Jarvis.exe + _internal (et autres fichiers de l'onedir).
# Les ressources (modèles OpenWakeWord) sont déjà embarquées par le spec
# PyInstaller dans `_internal/resources/` — pas besoin de les dupliquer.
Copy-Item -Recurse -Force (Join-Path $onedir "*") $appDir

# Écrire version.json à la racine de l'app (le launcher le lit pour l'update).
$versionJson = @"
{
  "version": "$Version",
  "channel": "$Channel",
  "build": "windows"
}
"@
$versionJson | Out-File -FilePath (Join-Path $appDir "version.json") -Encoding utf8

# Archive portable (contenu de app/ directement, sans sous-dossier app).
$zipName = "Jarvis-v$Version-portable.zip"
$zipPath = Join-Path $DistDir $zipName
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Write-Host "[build] Création de l'archive portable : $zipPath"
Compress-Archive -Path (Join-Path $appDir "*") -DestinationPath $zipPath -Force

# --- SHA-256 ---
$sha = (Get-FileHash -Algorithm SHA256 $zipPath).Hash.ToLower()
$shaPath = "$zipPath.sha256"
"$sha  $zipName" | Out-File -FilePath $shaPath -Encoding utf8
Write-Host "[build] SHA-256 : $sha"

# Copier aussi le launcher et un version.json de référence à la racine.
Copy-Item -Force (Join-Path $DistDir "JarvisLauncher.exe") $DistDir
Copy-Item -Force (Join-Path $appDir "version.json") $DistDir

Write-Host "=== BUILD TERMINÉ ===" -ForegroundColor Green
Get-ChildItem $DistDir | Select-Object Name, Length, LastWriteTime
