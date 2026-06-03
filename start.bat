@echo off
REM PFR Sentinel Startup Script

echo Starting PFR Sentinel...
echo.

REM Allow local scripts to run (needed for venv activation on fresh Windows)
powershell -Command "Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser -Force" >nul 2>&1

REM Check if virtual environment exists
if not exist "venv\Scripts\activate.bat" (
    echo Virtual environment not found. Creating...
    python -m venv venv
    call venv\Scripts\activate.bat
    echo Installing dependencies...
    python -m pip install --upgrade pip
    python -m pip install -r requirements.txt
    python -m pip install -r ml\requirements.txt
) else (
    call venv\Scripts\activate.bat
)

REM Use system malloc instead of Python's pymalloc — returns freed memory to the OS
REM more aggressively, which reduces long-session RAM bloat from large numpy array churn.
set PYTHONMALLOC=malloc

REM Run the application
python main.py

pause
