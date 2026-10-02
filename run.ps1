# ============================================================
#  Ultron Translate - Local Launch Script (PowerShell)
#  Uses the correct Python 3.10 interpreter where all packages
#  (pydirectinput, pywinauto, playwright, etc.) are installed.
# ============================================================

Set-Location $PSScriptRoot

$VENV_PYTHON = Join-Path $PSScriptRoot "venv\Scripts\python.exe"
if (Test-Path $VENV_PYTHON) {
    $PYTHON_EXE = $VENV_PYTHON
} else {
    $SysPython = Get-Command python -ErrorAction SilentlyContinue
    if ($SysPython) {
        $PYTHON_EXE = $SysPython.Source
    } else {
        Write-Host "[ERROR] Python not found in virtual environment or PATH!" -ForegroundColor Red
        Write-Host "Please ensure Python is installed and added to PATH, or run: python -m venv venv"
        Read-Host "Press Enter to exit"
        exit 1
    }
}

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  ALton - Starting Local Server" -ForegroundColor Cyan
Write-Host "  Python: $PYTHON_EXE" -ForegroundColor Green
Write-Host "  URL:    http://127.0.0.1:8080" -ForegroundColor Green
Write-Host "============================================================" -ForegroundColor Cyan

& $PYTHON_EXE -m uvicorn backend.main:app --host 127.0.0.1 --port 8080 --reload

