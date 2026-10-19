@echo off
setlocal EnableExtensions
rem ======================================================================
rem  JARVIS - VALIDATION FINALE AUTOMATISEE DE LA PR (une seule commande)
rem
rem  Usage : double-clic sur ce fichier, ou dans une console :
rem      valider_pr.bat                    (fenetre d'autorisations)
rem      valider_pr.bat --non-interactif   (options par defaut, sans fenetre)
rem
rem  Teste la tete exacte de la PR dans une copie isolee, sans toucher a la
rem  copie de travail, a l'installation quotidienne ni aux donnees Jarvis.
rem  Ecrit TOUJOURS un rapport : docs\validation\rapports\ (depot) et
rem  %LOCALAPPDATA%\JarvisValidation\runs\ (copie locale + journaux).
rem  Codes de sortie : 0 prete, 1 bloquee, 2 validation insuffisante,
rem  3 erreur du protocole ou interruption. Detail : docs\validation\README.md
rem ======================================================================
cd /d "%~dp0"
chcp 65001 >nul 2>nul
title Validation de la PR Jarvis
set "PYTHONDONTWRITEBYTECODE=1"
set "PYTHONUTF8=1"
set "PYEXE="
set "SANSPAUSE="
for %%a in (%*) do if /i "%%~a"=="--non-interactif" set "SANSPAUSE=1"

rem 1) Python 3.11 : version de la CI et de l'executable distribue.
py -3.11 -c "import sys" >nul 2>nul && set "PYEXE=py -3.11"
rem 2) Python du .venv de developpement : sert UNIQUEMENT a lancer l'orchestrateur
rem    (bibliotheque standard) ; rien n'y est installe ni modifie.
if not defined PYEXE if exist ".venv\Scripts\python.exe" ".venv\Scripts\python.exe" -c "import sys" >nul 2>nul && set "PYEXE=.venv\Scripts\python.exe"
rem 3) Lanceur py (version la plus recente), puis python du PATH.
if not defined PYEXE py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYEXE=py -3"
if not defined PYEXE python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul && set "PYEXE=python"

if defined PYEXE echo Python utilise pour l'orchestrateur : %PYEXE%
if defined PYEXE %PYEXE% "scripts\validation\valider_pr.py" %*
set "CODE=%ERRORLEVEL%"
if defined PYEXE echo.
if defined PYEXE echo Code de sortie : %CODE%  [0 prete, 1 bloquee, 2 validation insuffisante, 3 erreur du protocole]

rem ---- Sans Python : rapport minimal, jamais d'ecrasement d'un rapport existant ----
if not defined PYEXE set "CODE=3"
set "TS=inconnu"
if not defined PYEXE for /f "usebackq delims=" %%i in (`powershell -NoProfile -NonInteractive -Command "Get-Date -Format yyyyMMdd-HHmmss"`) do set "TS=%%i"
set "RAPPORT="
if not defined PYEXE if not exist "docs\validation\rapports" mkdir "docs\validation\rapports"
if not defined PYEXE if not exist "docs\validation\rapports\validation_pr50_%TS%.md" set "RAPPORT=docs\validation\rapports\validation_pr50_%TS%.md"
if not defined PYEXE for %%n in (2 3 4 5 6 7 8 9) do if not defined RAPPORT if not exist "docs\validation\rapports\validation_pr50_%TS%_%%n.md" set "RAPPORT=docs\validation\rapports\validation_pr50_%TS%_%%n.md"
if defined RAPPORT >"%RAPPORT%" echo # Rapport de validation - PR #50 de Jarvis
if defined RAPPORT >>"%RAPPORT%" echo.
if defined RAPPORT >>"%RAPPORT%" echo *Genere par valider_pr.bat le %TS%.*
if defined RAPPORT >>"%RAPPORT%" echo.
if defined RAPPORT >>"%RAPPORT%" echo ## Verdict global
if defined RAPPORT >>"%RAPPORT%" echo.
if defined RAPPORT >>"%RAPPORT%" echo **VALIDATION INSUFFISANTE** - code de sortie 3 : Python est introuvable sur ce PC, aucun controle n'a pu etre execute.
if defined RAPPORT >>"%RAPPORT%" echo.
if defined RAPPORT >>"%RAPPORT%" echo Tous les controles A a F sont BLOQUE : l'orchestrateur scripts\validation\valider_pr.py demande Python 3.9 ou plus, 3.11 recommande comme la CI.
if defined RAPPORT >>"%RAPPORT%" echo.
if defined RAPPORT >>"%RAPPORT%" echo ## Action
if defined RAPPORT >>"%RAPPORT%" echo.
if defined RAPPORT >>"%RAPPORT%" echo Installer Python 3.11 depuis https://www.python.org/downloads/windows/ en cochant le lanceur py, puis relancer valider_pr.bat.
if defined RAPPORT >>"%RAPPORT%" echo Aucune donnee Jarvis, aucune installation et aucun fichier du depot n'ont ete modifies, hormis ce rapport.
if not defined PYEXE echo Python introuvable : installez Python 3.11 depuis python.org puis relancez valider_pr.bat.
if defined RAPPORT echo Rapport minimal ecrit : %RAPPORT%

if not defined SANSPAUSE pause
exit /b %CODE%
