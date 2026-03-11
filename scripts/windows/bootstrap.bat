@echo off
setlocal

:: ClawSmith — Bootstrap (CI / re-run variant)
:: Verifies Python, creates venv if needed, installs deps.

set "REPO_ROOT=%~dp0..\.."

python --version >nul 2>&1 || (echo ERROR: Python not found. & exit /b 1)

if not exist "%REPO_ROOT%\venv\Scripts\activate.bat" (
    python -m venv "%REPO_ROOT%\venv" || exit /b 1
)

call "%REPO_ROOT%\venv\Scripts\activate.bat"
pushd "%REPO_ROOT%"

python -m pip install --upgrade pip --quiet
pip install -e .[dev] || exit /b 1

echo [ClawSmith] Bootstrap complete.
popd
endlocal
