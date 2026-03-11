@echo off
setlocal

:: ClawSmith setup — creates venv, installs deps, copies .env.example

set "REPO_ROOT=%~dp0..\.."

echo [ClawSmith] Checking Python installation...
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not on PATH.
    echo Please install Python 3.11+ and try again.
    exit /b 1
)

echo [ClawSmith] Python found.

if not exist "%REPO_ROOT%\venv\Scripts\activate.bat" (
    echo [ClawSmith] Creating virtual environment...
    python -m venv "%REPO_ROOT%\venv"
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment.
        exit /b 1
    )
) else (
    echo [ClawSmith] Virtual environment already exists.
)

echo [ClawSmith] Activating virtual environment...
call "%REPO_ROOT%\venv\Scripts\activate.bat"

pushd "%REPO_ROOT%"

echo [ClawSmith] Installing dependencies...
pip install -e .[dev]
if errorlevel 1 (
    echo ERROR: pip install failed.
    exit /b 1
)

if not exist "%REPO_ROOT%\.env" (
    if exist "%REPO_ROOT%\.env.example" (
        echo [ClawSmith] Copying .env.example to .env...
        copy "%REPO_ROOT%\.env.example" "%REPO_ROOT%\.env" >nul
        echo REMINDER: Edit .env and fill in your API keys.
    )
)

if not exist "%REPO_ROOT%\logs" mkdir "%REPO_ROOT%\logs"
if not exist "%REPO_ROOT%\artifacts" mkdir "%REPO_ROOT%\artifacts"
if not exist "%REPO_ROOT%\jobs\generated" mkdir "%REPO_ROOT%\jobs\generated"

echo.
echo [ClawSmith] Setup complete!
echo.
echo Next steps:
echo   1. Edit .env with your API keys
echo   2. Run: scripts\windows\doctor.bat
echo   3. Run: scripts\windows\start_mcp_server.bat

popd
endlocal
