@echo off
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

pytest tests\ -v
exit /b %ERRORLEVEL%
