@echo off
REM ===========================================================================
REM  DarkMatterBot - one-click Windows build.
REM
REM  Double-click this file. It installs the build requirements if needed,
REM  builds DarkMatterBot.exe into dist\ and a zip into releases\, and then
REM  PAUSES so the window never flashes and closes before you can read it.
REM ===========================================================================
setlocal
cd /d "%~dp0"

echo ============================================
echo   DarkMatterBot build
echo ============================================
echo.

REM --- pick a Python: prefer the project venv, else whatever is on PATH -------
set "PY=python"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
    echo Using the project virtualenv: .venv
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python was not found on this machine.
        echo         Install Python 3.10+ from https://www.python.org/downloads/
        echo         and tick "Add python.exe to PATH" during setup.
        echo.
        pause
        exit /b 1
    )
    echo Using the Python on PATH.
)

REM --- the build needs the full project, not just the zip of changed files ----
if not exist "requirements.txt" (
    echo [ERROR] requirements.txt is not in this folder:
    echo         %CD%
    echo         Put build.bat in the project folder ^(the one with main.py,
    echo         requirements.txt and the ui\ and core\ folders^) and run it again.
    echo.
    pause
    exit /b 1
)

REM --- install the runtime + build requirements ------------------------------
echo.
echo Installing requirements (first run only, this can take a few minutes)...
"%PY%" -m pip install --upgrade pip >nul 2>nul
"%PY%" -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Installing requirements failed. Check your internet connection.
    echo.
    pause
    exit /b 1
)
"%PY%" -m pip install --upgrade pyinstaller

REM --- build -----------------------------------------------------------------
echo.
echo Building DarkMatterBot.exe ...
"%PY%" build.py > build.log 2>&1
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (
    echo ============================================
    echo   BUILD OK
    echo ============================================
    echo   Executable : dist\DarkMatterBot.exe
    echo   Release zip: releases\
    echo   Full log   : build.log
    echo.
    if exist "dist\DarkMatterBot.exe" (
        echo Opening the dist folder...
        start "" "dist"
    )
) else (
    echo ============================================
    echo   BUILD FAILED  ^(exit code %RC%^)
    echo ============================================
    echo   Last lines of build.log:
    echo.
    powershell -NoProfile -Command "if (Test-Path build.log) { Get-Content build.log -Tail 25 }"
    echo.
    echo   Send build.log if you need help.
)
echo.
pause
endlocal
