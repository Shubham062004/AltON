@echo off
REM ============================================================
REM  Ultron Translate - Standalone Native Desktop Agent App
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
echo   ALton - Starting Desktop Assistant Application
echo   Python: %PYTHON_EXE%
echo   Mode:   Native Desktop Agent (PyWebView + Edge Chromium)
echo ============================================================

"%PYTHON_EXE%" desktop_app.py
pause

