@echo off
setlocal

:: ClawSmith — Full Installation Script
:: Run this once after cloning the repository.

set "REPO_ROOT=%~dp0..\.."

:: 1. Check Python
python --version >nul 2>&1 || (echo ERROR: Python 3.11+ required. & exit /b 1)

:: 2. Create venv if missing
if not exist "%REPO_ROOT%\venv\Scripts\activate.bat" (
    echo [install] Creating virtual environment...
    python -m venv "%REPO_ROOT%\venv" || exit /b 1
)

:: 3. Activate
call "%REPO_ROOT%\venv\Scripts\activate.bat"
pushd "%REPO_ROOT%"

:: 4. Upgrade pip silently
python -m pip install --upgrade pip --quiet

:: 5. Install project with dev extras
echo [install] Installing ClawSmith and dependencies...
pip install -e .[dev] || exit /b 1

:: 6. Copy .env.example → .env if not present
if not exist "%REPO_ROOT%\.env" (
    copy "%REPO_ROOT%\.env.example" "%REPO_ROOT%\.env" >nul
    echo [install] Created .env from .env.example — edit it with your API keys.
)

:: 7. Create required runtime directories
if not exist "%REPO_ROOT%\logs"           mkdir "%REPO_ROOT%\logs"
if not exist "%REPO_ROOT%\artifacts"      mkdir "%REPO_ROOT%\artifacts"
if not exist "%REPO_ROOT%\jobs\generated" mkdir "%REPO_ROOT%\jobs\generated"

:: 8. Success
echo.
echo [ClawSmith] Installation complete!
echo.
echo   Next steps:
echo     1. Edit .env with your API keys
echo     2. scripts\windows\doctor.bat     — verify your setup
echo     3. scripts\windows\run_mcp.bat    — start the MCP server
echo.

popd
endlocal
