@echo off
:: Usage: run_orchestrator.bat --task "your task description" --repo-path "path\to\repo" [--dry-run]
:: All arguments are forwarded directly to: python -m orchestrator run-task
setlocal

set "REPO_ROOT=%~dp0..\.."

if exist "%REPO_ROOT%\.venv\Scripts\activate.bat" (
    call "%REPO_ROOT%\.venv\Scripts\activate.bat"
) else if exist "%REPO_ROOT%\venv\Scripts\activate.bat" (
    call "%REPO_ROOT%\venv\Scripts\activate.bat"
) else (
    echo ERROR: No virtual environment found.
    echo Please run scripts\windows\setup.bat first.
    exit /b 1
)

set "PYTHONPATH=%REPO_ROOT%"

python -m orchestrator run-task %*
exit /b %ERRORLEVEL%
