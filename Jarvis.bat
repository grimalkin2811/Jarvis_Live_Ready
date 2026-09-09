@echo off
cd /d "%~dp0"
echo === JARVIS LIVE (DEVELOPPEMENT) ===
echo L'utilisateur final utilise JarvisLauncher.exe (qui met a jour
echo automatiquement). Ce .bat n'est destine qu'au developpement.
echo.
if not exist ".env" (
 echo Lance setup.bat d'abord.
 exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
 echo Lance setup.bat d'abord.
 exit /b 1
)
.venv\Scripts\python.exe -m src.main %*
exit /b %errorlevel%
