@echo off
cd /d "%~dp0"
if not exist ".env" (
 echo Lance setup.bat d'abord.
 exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
 echo Lance setup.bat d'abord.
 exit /b 1
)
.venv\Scripts\python.exe -m src.main
exit /b %errorlevel%
