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

echo [ClawSmith] Running ruff check...
ruff check . --fix
set RUFF_CHECK=%ERRORLEVEL%

echo [ClawSmith] Running ruff format...
ruff format .
set RUFF_FORMAT=%ERRORLEVEL%

echo [ClawSmith] Running mypy...
mypy orchestrator\ mcp_server\ --ignore-missing-imports
set MYPY_RESULT=%ERRORLEVEL%

echo.
echo === Lint Summary ===
if %RUFF_CHECK%==0 (echo   ruff check:  PASS) else (echo   ruff check:  FAIL)
if %RUFF_FORMAT%==0 (echo   ruff format: PASS) else (echo   ruff format: FAIL)
if %MYPY_RESULT%==0 (echo   mypy:        PASS) else (echo   mypy:        FAIL)

set /a FINAL_EXIT=0
if not %RUFF_CHECK%==0 set /a FINAL_EXIT=1
if not %RUFF_FORMAT%==0 set /a FINAL_EXIT=1
if not %MYPY_RESULT%==0 set /a FINAL_EXIT=1

endlocal & exit /b %FINAL_EXIT%
