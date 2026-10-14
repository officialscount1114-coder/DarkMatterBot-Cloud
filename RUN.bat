@echo off
REM ===========================================================================
REM  DarkMatterBot - run the app.
REM  Double-click this file. First run installs the requirements (needs
REM  internet for a few minutes); after that it just opens the app.
REM ===========================================================================
setlocal
cd /d "%~dp0"

echo ============================================
echo   DarkMatterBot
echo ============================================
echo.

set "PY=python"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
    echo Using the project virtualenv (.venv)
) else (
    where python >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] Python was not found.
        echo         Install Python 3.10+ from https://www.python.org/downloads/
        echo         and TICK "Add python.exe to PATH" during the install.
        echo.
        pause
        exit /b 1
    )
    echo Using the Python on PATH.
)

if not exist "requirements.txt" (
    echo [ERROR] requirements.txt is missing.
    echo         Run this file from the project folder - the one that contains
    echo         main.py and requirements.txt. Current folder:
    echo         %CD%
    echo.
    pause
    exit /b 1
)

echo.
echo Checking requirements (first run takes a few minutes)...
"%PY%" -m pip install --quiet --upgrade pip
"%PY%" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Could not install the requirements. Check your internet connection,
    echo         then run this file again.
    echo.
    pause
    exit /b 1
)

echo.
echo Starting DarkMatterBot...
"%PY%" main.py
echo.
echo DarkMatterBot has exited.
pause
endlocal
