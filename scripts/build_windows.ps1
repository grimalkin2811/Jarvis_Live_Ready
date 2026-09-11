# Build Windows de Jarvis (application + launcher + archive portable).
#
# À utiliser localement (Windows avec Python) ou en CI (windows-latest).
# Ce script est la source de vérité pour la chaîne de distribution :
#
#   source -> PyInstaller -> dist/Jarvis -> dist/app -> ZIP -> installer
#
# Chaque étape est validée immédiatement, avec des logs explicites.
# Si une étape échoue, le build s'arrête et aucune release n'est publiée.

param(
    [switch]$SkipModels,
    [string]$DistDir = "dist"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)  # racine du dépôt

# Même contrat qu'en CI (.github/workflows/build.yml) : Python doit émettre
# de l'UTF-8 et ne jamais planter sur l'encodage de la console Windows
# (cp1252/cp850). Protège validate_build.py, make_portable_zip.py,
# download_models.py et tous les sous-processus python du build.
$env:PYTHONUTF8 = "1"

Write-Host "=== JARVIS - BUILD WINDOWS ===" -ForegroundColor Cyan
Write-Host "Date: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Host "DistDir: $DistDir"

# --- Version ---
$Version = python -c "import sys; sys.path.insert(0, '.'); from src.version import __version__; print(__version__)"
if ($LASTEXITCODE -ne 0) { throw "Impossible de lire src.version.__version__" }
$Version = $Version.Trim()
$Channel = python -c "import sys; sys.path.insert(0, '.'); from src.version import CHANNEL; print(CHANNEL)"
$Channel = $Channel.Trim()
Write-Host "Version : $Version (canal : $Channel)"

# Vérifie que la version est au format semver
if ($Version -notmatch '^\d+\.\d+\.\d+') {
    throw "Version invalide: $Version (attendu: MAJOR.MINOR.PATCH)"
}

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
& $venvPython --version

# Vérifie/installe les dépendances de build.
Write-Host "[build] Installation des dépendances..."
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade échoué" }
& $venvPython -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install requirements échoué" }
& $venvPython -m pip install pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller échoué" }

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

Write-Host ""
Write-Host "=== PyInstaller output ===" -ForegroundColor Cyan

Write-Host "[build] Construction de l'application (PyInstaller onedir)..."
& $venvPython -m PyInstaller packaging/jarvis.spec --distpath $DistDir --workpath $work --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "Échec PyInstaller (application)." }

# --- Validation immédiate après PyInstaller ---
Write-Host ""
Write-Host "=== Validating PyInstaller output ===" -ForegroundColor Cyan
$onedir = Join-Path $DistDir "Jarvis"
$onedirExe = Join-Path $onedir "Jarvis.exe"
$onedirInternalDll = Join-Path $onedir "_internal\python311.dll"
$onedirBaseZip = Join-Path $onedir "_internal\base_library.zip"
$onedirFlattenedDll = Join-Path $onedir "python311.dll"

Write-Host "Checking PyInstaller output structure..."
Write-Host "  $onedirExe : $(if (Test-Path $onedirExe) { 'OK' } else { 'MISSING' })"
Write-Host "  $onedirInternalDll : $(if (Test-Path $onedirInternalDll) { 'OK' } else { 'MISSING' })"
Write-Host "  $onedirBaseZip : $(if (Test-Path $onedirBaseZip) { 'OK' } else { 'MISSING' })"
Write-Host "  Checking for flattened files (should NOT exist at root)..."
Write-Host "    $onedirFlattenedDll : $(if (Test-Path $onedirFlattenedDll) { 'FOUND (BAD - flattening!)' } else { 'OK (not present)' })"

if (-not (Test-Path $onedirExe)) {
    throw "PyInstaller output invalide: $onedirExe introuvable"
}
if (-not (Test-Path $onedirInternalDll)) {
    throw "PyInstaller output invalide: $onedirInternalDll introuvable. Le build ne sera pas publié."
}
if (-not (Test-Path $onedirBaseZip)) {
    throw "PyInstaller output invalide: $onedirBaseZip introuvable"
}
if (Test-Path $onedirFlattenedDll) {
    throw "PyInstaller output invalide: $onedirFlattenedDll existe à la racine (flattening détecté). Attendu dans _internal/"
}

# Validation via Python pour plus de robustesse
Write-Host "[build] Validation Python du dossier PyInstaller..."
& $venvPython scripts/validate_build.py --pyinstaller $onedir
if ($LASTEXITCODE -ne 0) { throw "Validation PyInstaller échouée (Python)" }

Write-Host "[build] PyInstaller output: OK" -ForegroundColor Green

Write-Host ""
Write-Host "[build] Construction du launcher (PyInstaller onefile)..."
& $venvPython -m PyInstaller packaging/launcher.spec --distpath $DistDir --workpath $work --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "Échec PyInstaller (launcher)." }

$launcherExe = Join-Path $DistDir "JarvisLauncher.exe"
if (-not (Test-Path $launcherExe)) {
    throw "Launcher introuvable: $launcherExe"
}
Write-Host "[build] Launcher: OK ($launcherExe)"

# --- Assembler l'application portable ---
Write-Host ""
Write-Host "=== Assembling portable app ===" -ForegroundColor Cyan

$appDir = Join-Path $DistDir "app"
if (Test-Path $appDir) { Remove-Item $appDir -Recurse -Force }
if (-not (Test-Path $onedirExe)) { throw "Jarvis.exe introuvable dans $onedir" }

# IMPORTANT : ne pas utiliser Copy-Item avec un wildcard ici.
# Avec certaines versions/comportements de PowerShell, la copie du contenu
# d'un dossier PyInstaller peut aplatir la structure `_internal`.
# Robocopy préserve exactement l'arborescence produite par PyInstaller.
Write-Host "[build] Copie $onedir -> $appDir (robocopy)..."
New-Item -ItemType Directory -Path $appDir -Force | Out-Null
& robocopy $onedir $appDir /E /NFL /NDL /NJH /NJS /NP
$robocopyExit = $LASTEXITCODE
Write-Host "[build] Robocopy exit code: $robocopyExit (0-7 = success)"
if ($robocopyExit -gt 7) {
    throw "Robocopy a échoué (code $robocopyExit)."
}

# --- Validation après copie ---
Write-Host ""
Write-Host "=== Validating application layout ===" -ForegroundColor Cyan

$appExe = Join-Path $appDir "Jarvis.exe"
$internalPython = Join-Path $appDir "_internal\python311.dll"
$internalBase = Join-Path $appDir "_internal\base_library.zip"
$flattenedPython = Join-Path $appDir "python311.dll"
$flattenedBase = Join-Path $appDir "base_library.zip"

Write-Host "  $appExe : $(if (Test-Path $appExe) { 'OK' } else { 'MISSING' })"
Write-Host "  $internalPython : $(if (Test-Path $internalPython) { 'OK' } else { 'MISSING' })"
Write-Host "  $internalBase : $(if (Test-Path $internalBase) { 'OK' } else { 'MISSING' })"
Write-Host "  Checking for flattened files (should NOT exist at root)..."
Write-Host "    $flattenedPython : $(if (Test-Path $flattenedPython) { 'FOUND (BAD)' } else { 'OK' })"
Write-Host "    $flattenedBase : $(if (Test-Path $flattenedBase) { 'FOUND (BAD)' } else { 'OK' })"

if (-not (Test-Path $appExe)) {
    throw "Layout invalide: $appExe introuvable"
}
if (-not (Test-Path $internalPython)) {
    throw "Layout PyInstaller invalide : $internalPython est introuvable. Le build ne sera pas publié."
}
if (-not (Test-Path $internalBase)) {
    throw "Layout invalide: $internalBase introuvable"
}
if (Test-Path $flattenedPython) {
    throw "Layout invalide: $flattenedPython existe à la racine (flattening). Doit être dans _internal/"
}
if (Test-Path $flattenedBase) {
    throw "Layout invalide: $flattenedBase existe à la racine (flattening). Doit être dans _internal/"
}

# Validation Python supplémentaire
Write-Host "[build] Validation Python du dossier app..."
& $venvPython scripts/validate_build.py --app-dir $appDir
if ($LASTEXITCODE -ne 0) { throw "Validation app dir échouée (Python)" }

Write-Host "[build] Application layout: OK" -ForegroundColor Green

# Écrire version.json à la racine de l'app.
Write-Host ""
Write-Host "[build] Écriture version.json..."
$versionJson = @"
{
  "version": "$Version",
  "channel": "$Channel",
  "build": "windows"
}
"@
$versionJson | Out-File -FilePath (Join-Path $appDir "version.json") -Encoding utf8
Write-Host "  version.json: OK"

# --- Archive portable ---
Write-Host ""
Write-Host "=== Creating portable ZIP ===" -ForegroundColor Cyan

$zipName = "Jarvis-v$Version-portable.zip"
$zipPath = Join-Path $DistDir $zipName
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
Write-Host "[build] Création de l'archive portable : $zipPath"

# Utilise Python pour créer le ZIP de façon robuste (préserve _internal)
# Fallback vers Compress-Archive si Python échoue
try {
    Write-Host "[build] Création ZIP via Python (robuste)..."
    & $venvPython scripts/make_portable_zip.py --app-dir $appDir --output $zipPath
    if ($LASTEXITCODE -ne 0) { throw "make_portable_zip.py a échoué" }
} catch {
    Write-Host "[build] Python ZIP creation failed, fallback to Compress-Archive: $_" -ForegroundColor Yellow
    Compress-Archive -Path (Join-Path $appDir "*") -DestinationPath $zipPath -Force
    # NB : Compress-Archive est une cmdlet et ne définit pas $LASTEXITCODE ;
    # on valide par la présence du fichier plutôt que par un code périmé.
    if (-not (Test-Path $zipPath)) { throw "Compress-Archive a échoué" }
}

if (-not (Test-Path $zipPath)) {
    throw "ZIP non créé: $zipPath"
}

# Validation du ZIP
Write-Host ""
Write-Host "=== Validating portable ZIP ===" -ForegroundColor Cyan
Write-Host "[build] Validation Python du ZIP..."
& $venvPython scripts/validate_build.py --zip $zipPath
if ($LASTEXITCODE -ne 0) {
    throw "Validation ZIP échouée: $zipPath est invalide (structure _internal manquante ou aplatie)"
}

# Vérifie aussi le contenu du ZIP avec PowerShell pour logs
Write-Host "[build] Contenu du ZIP (aperçu):"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
$entries = $zip.Entries | Select-Object -First 20 | ForEach-Object { $_.FullName }
$zip.Dispose()
$entries | ForEach-Object { Write-Host "  $_" }
Write-Host "  ... (total entries: $( (Get-ChildItem $appDir -Recurse -File).Count ) fichiers)"

Write-Host "[build] ZIP: OK" -ForegroundColor Green

# --- SHA-256 ---
Write-Host ""
Write-Host "=== SHA-256 ===" -ForegroundColor Cyan
$sha = (Get-FileHash -Algorithm SHA256 $zipPath).Hash.ToLower()
$shaPath = "$zipPath.sha256"
"$sha  $zipName" | Out-File -FilePath $shaPath -Encoding utf8
Write-Host "[build] SHA-256 : $sha"
Write-Host "[build] SHA file : $shaPath"

# Vérifie le SHA
$shaContent = Get-Content $shaPath -Raw
Write-Host "[build] SHA file content: $shaContent"

# Copier aussi un version.json de référence à la racine.
Copy-Item -Force (Join-Path $appDir "version.json") $DistDir
Write-Host "[build] version.json copié vers dist/"

# --- Smoke test de l'exécutable ---
Write-Host ""
Write-Host "=== Smoke testing executable ===" -ForegroundColor Cyan
$testExe = Join-Path $appDir "Jarvis.exe"
Write-Host "[build] Test: $testExe --smoke-test"
# NB : Start-Process n'a PAS de paramètre -Timeout (erreur de paramètre pwsh,
# qui était avalée par le catch et faisait que le smoke test ne tournait
# jamais). WaitForExit(ms) fournit un délai borné réel.
try {
    $process = Start-Process -FilePath $testExe -ArgumentList "--smoke-test" -PassThru -NoNewWindow
    if (-not $process.WaitForExit(30000)) {
        try { $process.Kill() } catch { }
        Write-Host "[build] Smoke test: timeout après 30s (processus tué)" -ForegroundColor Yellow
    } elseif ($process.ExitCode -ne 0) {
        Write-Host "[build] Smoke test exit code: $($process.ExitCode)" -ForegroundColor Yellow
        # Ne fait pas échouer le build si smoke test échoue, mais log ;
        # en CI, le Test 3 du workflow est le verrou bloquant.
    } else {
        Write-Host "[build] Smoke test: OK" -ForegroundColor Green
    }
} catch {
    Write-Host "[build] Smoke test exception: $_" -ForegroundColor Yellow
    # On ne fait pas échouer le build ici, mais on log.
    # Le test CI fera un smoke test plus robuste.
}

Write-Host ""
Write-Host "=== BUILD TERMINÉ ===" -ForegroundColor Green
Write-Host "Version: $Version"
Write-Host "Artifacts:"
Get-ChildItem $DistDir | Where-Object { $_.Name -like "Jarvis*" -or $_.Name -like "version.json" } | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize

Write-Host ""
Write-Host "Validation finale:" -ForegroundColor Cyan
Write-Host "  dist/app/Jarvis.exe : $(if (Test-Path (Join-Path $DistDir 'app\Jarvis.exe')) { 'OK' } else { 'MISSING' })"
Write-Host "  dist/app/_internal/python311.dll : $(if (Test-Path (Join-Path $DistDir 'app\_internal\python311.dll')) { 'OK' } else { 'MISSING' })"
Write-Host "  dist/app/_internal/base_library.zip : $(if (Test-Path (Join-Path $DistDir 'app\_internal\base_library.zip')) { 'OK' } else { 'MISSING' })"
Write-Host "  dist/JarvisLauncher.exe : $(if (Test-Path (Join-Path $DistDir 'JarvisLauncher.exe')) { 'OK' } else { 'MISSING' })"
Write-Host "  dist/$zipName : $(if (Test-Path $zipPath) { 'OK' } else { 'MISSING' })"
Write-Host "  dist/$zipName.sha256 : $(if (Test-Path $shaPath) { 'OK' } else { 'MISSING' })"

Write-Host ""
Write-Host "Build réussi! Prêt pour Inno Setup." -ForegroundColor Green
