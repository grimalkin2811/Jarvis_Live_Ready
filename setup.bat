@echo off
cd /d "%~dp0"
echo === JARVIS LIVE - SETUP ===
where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)
%PY% --version
if errorlevel 1 (
 echo Python 3 est requis. Installe Python puis relance.
 pause
 exit /b 1
)
set /p JARVIS_USER=Ton nom : 
set /p GEMINI_KEY=Cle Gemini API : 
if "%JARVIS_USER%"=="" exit /b 1
if "%GEMINI_KEY%"=="" exit /b 1
%PY% -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
pip install -r requirements.txt
if errorlevel 1 (
 echo Erreur installation dependances.
 pause
 exit /b 1
)
.venv\Scripts\python.exe -c "from openwakeword.utils import download_models; download_models(['hey_jarvis'])"
if errorlevel 1 (
 echo Attention : le modèle wake word n'a pas pu etre telecharge.
 echo Vous pouvez relancer Jarvis.bat, il essaiera automatiquement de le recuperer.
)
(
 echo JARVIS_USER=%JARVIS_USER%
 echo GEMINI_API_KEY=%GEMINI_KEY%
 echo GEMINI_MODEL=gemini-2.5-flash-native-audio-preview-12-2025
 echo JARVIS_MEMORY_ENABLED=1
 echo JARVIS_MEMORY_MAX_RESULTS=5
 echo JARVIS_MEMORY_MIN_IMPORTANCE=1
 echo JARVIS_ROUTINES_ENABLED=1
 echo JARVIS_REMINDERS_ENABLED=1
) > .env
echo.
echo Installation terminee. Lance Jarvis.bat
pause
