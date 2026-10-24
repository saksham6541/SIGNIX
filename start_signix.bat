
@echo off
title SIGNIX Debug Launcher

echo.
echo ===== SIGNIX LAUNCHER STARTED =====
echo.

cd /d "%~dp0"

echo Current folder:
cd
echo.

echo Checking Python environment...

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: Python not found in .venv
    pause
    exit /b 1
)

echo Python environment found!
echo.
echo Checking Flask...

".venv\Scripts\python.exe" -c "import flask; print('Flask is installed')"

if errorlevel 1 (
    echo.
    echo Flask is missing. Installing requirements...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

echo.
echo Starting SIGNIX server...
echo.

".venv\Scripts\python.exe" run.py

echo.
echo ===== SERVER STOPPED OR FAILED =====
pause
