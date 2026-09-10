# Build Windows de Jarvis (application + launcher + archive portable).
#
# À utiliser localement (Windows avec Python) ou en CI (windows-latest).

param(
    [switch]$SkipModels,
    [string]$DistDir = "dist"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)  # racine du dépôt

Write-Host "=== JARVIS - BUILD WINDOWS ===" -ForegroundColor Cyan

# --- Version ---
$Version = python -c "import sys; sys.path.insert(0, '.'); from src.version import __version__; print(__version__)"
if ($LASTEXITCODE -ne 0) { throw "Impossible de lire src.version.__version__" }
$Version = $Version.Trim()
$Channel = python -c "import sys; sys.path.insert(0, '.'); from src.version import CHANNEL; print(CHANNEL)"
$Channel = $Channel.Trim()
Write-Host "Version : $Version (canal : $Channel)"

# --- Python de build ---
$venvPython = Join-Path $PSScriptRoot "..\.venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $venvPython = (Join-Path (Get-Location) ".venv\Scripts\python.exe")
}
if (-not (Test-Path $venvPython)) {
    Write-Host "[build] Aucun .venv trouvé : on utilise le python actif."
    $venvPython = "python"
}
Write-Host "Python build : $venvPython"

# Vérifie/installe les dépendances de build.
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt
& $venvPython -m pip install pyinstaller

# --- Modèles OpenWakeWord ---
if (-not $SkipModels) {
    Write-Host "[build] Téléchargement des modèles OpenWakeWord..."
    & $venvPython scripts/download_models.py
    if ($LASTEXITCODE -ne 0) { throw "Téléchargement des modèles OpenWakeWord échoué." }
} else {
    Write-Host "[build] Téléchargement des modèles ignoré (-SkipModels)."
}

# --- Construction ---
$work = Join-Path $DistDir ".build"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }

Write-Host "[build] Construction de l'application (PyInstaller onedir)..."
& $venvPython -m PyInstaller packaging/jarvis.spec --distpath $DistDir --workpath $work --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "Échec PyInstaller (application)." }

Write-Host "[build] Construction du launcher (PyInstaller onefile)..."
& $venvPython -m PyInstaller packaging/launcher.spec --distpath $DistDir --workpath $work --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "Échec PyInstaller (launcher)." }

# --- Assembler l'application portable ---
$appDir = Join-Path $DistDir "app"
if (Test-Path $appDir) { Remove-Item $appDir -Recurse -Force }
$onedir = Join-Path $DistDir "Jarvis"
if (-not (Test-Path (Join-Path $onedir "Jarvis.exe"))) { throw "Jarvis.exe introuvable dans $onedir" }

# IMPORTANT : ne pas utiliser Copy-Item avec un wildcard ici.
# Avec certaines versions/comportements de PowerShell, la copie du contenu
# d'un dossier PyInstaller peut aplatir la structure `_internal`.
# Robocopy préserve exactement l'arborescence produite par PyInstaller.
New-Item -ItemType Directory -Path $appDir -Force | Out-Null
& robocopy $onedir $appDir /E /NFL /NDL /NJH /NJS /NP
$robocopyExit = $LASTEXITCODE
if ($robocopyExit -gt 7) {
    throw "Robocopy a échoué (code $robocopyExit)."
}

# Garde-fou : un build moderne de PyInstaller doit contenir `_internal`.
$internalPython = Join-Path $appDir "_internal\python311.dll"
if (-not (Test-Path $internalPython)) {
    throw "Layout PyInstaller invalide : $internalPython est introuvable. Le build ne sera pas publié."
}

# Écrire version.json à la racine de l'app.
$versionJson = @"
{
  "version": "$Version",
  "channel": "$Channel",
  "build": "windows"
}
"@
$versionJson | Out-File -FilePath (Join-Path $appDir "version.json") -Encoding utf8

# --- Archive portable ---
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

# Copier aussi un version.json de référence à la racine.
Copy-Item -Force (Join-Path $appDir "version.json") $DistDir

Write-Host "=== BUILD TERMINÉ ===" -ForegroundColor Green
Get-ChildItem $DistDir | Select-Object Name, Length, LastWriteTime
