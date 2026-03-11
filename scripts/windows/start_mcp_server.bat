@echo off
setlocal

:: Navigate to repo root (two levels up from scripts\windows\)
set "REPO_ROOT=%~dp0..\.."

:: Activate virtual environment
if exist "%REPO_ROOT%\.venv\Scripts\activate.bat" (
    call "%REPO_ROOT%\.venv\Scripts\activate.bat"
) else if exist "%REPO_ROOT%\venv\Scripts\activate.bat" (
    call "%REPO_ROOT%\venv\Scripts\activate.bat"
) else (
    echo ERROR: No virtual environment found.
    echo Please run scripts\windows\setup.bat first to create one.
    exit /b 1
)

:: Ensure repo root is on PYTHONPATH so all packages resolve
set "PYTHONPATH=%REPO_ROOT%"

echo Starting ClawSmith MCP server...
python -m mcp_server

endlocal
