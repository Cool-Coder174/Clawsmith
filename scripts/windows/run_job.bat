@echo off
:: ClawSmith - Run a job from a JSON job spec file
:: Usage: run_job.bat <path-to-job-spec.json>

if "%~1"=="" (
    echo Usage: run_job.bat ^<path-to-job-spec.json^>
    exit /b 1
)

call "%~dp0..\..\venv\Scripts\activate.bat" 2>nul
if errorlevel 1 (
    call "%~dp0..\..\.venv\Scripts\activate.bat" 2>nul
)

python -m orchestrator.cli run-job --job-file "%~1"
