@echo off
REM ============================================================
REM  Ultron Translate - Local Launch Script
REM  Uses the correct Python 3.10 interpreter where all packages
REM  (pydirectinput, pywinauto, playwright, etc.) are installed.
REM ============================================================

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

if exist "%PROJECT_DIR%venv\Scripts\python.exe" (
    set "PYTHON_EXE=%PROJECT_DIR%venv\Scripts\python.exe"
) else (
    where python >nul 2>&1
    if %errorlevel% equ 0 (
        set "PYTHON_EXE=python"
    ) else (
        echo [ERROR] Python not found in virtual environment or PATH!
        echo Please ensure Python is installed and added to PATH, or run: python -m venv venv
        pause
        exit /b 1
    )
)

echo ============================================================
echo   ALton - Starting Local Server
echo   Python: %PYTHON_EXE%
echo   URL:    http://127.0.0.1:8080
echo ============================================================

"%PYTHON_EXE%" -m uvicorn backend.main:app --host 127.0.0.1 --port 8080 --reload
pause

