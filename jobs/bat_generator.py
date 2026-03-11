"""Generates Windows .bat scripts from a validated JobSpec."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from config.config_loader import get_config
from orchestrator.schemas import JobSpec

_REPO_ROOT = Path(__file__).parent.parent
_GENERATED_DIR = _REPO_ROOT / "jobs" / "generated"


class BatGenerator:
    """Produces a runnable ``.bat`` file and metadata for a given job."""

    def generate(self, job: JobSpec) -> Path:
        """Create the ``.bat`` script and write metadata. Returns the bat path."""
        _GENERATED_DIR.mkdir(parents=True, exist_ok=True)

        config = get_config()
        artifact_dir = _REPO_ROOT / config.execution.artifacts_dir / job.id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).isoformat()

        build_block = self._commands_block(job.build_commands, "BUILD")
        test_block = self._commands_block(job.test_commands, "TEST")

        content = (
            "@echo off\n"
            "setlocal enabledelayedexpansion\n"
            f":: ClawSmith Job: {job.id}\n"
            f":: Task Type: {job.task_type.value}\n"
            f":: Objective: {job.objective}\n"
            f":: Generated: {timestamp}\n"
            f":: Timeout: {job.timeout_seconds} seconds\n"
            "\n"
            f"set JOB_ID={job.id}\n"
            f"set ARTIFACT_DIR={artifact_dir}\n"
            f'set STDOUT_LOG={artifact_dir}\\stdout.log\n'
            f'set STDERR_LOG={artifact_dir}\\stderr.log\n'
            f"set TIMEOUT_SECONDS={job.timeout_seconds}\n"
            "set EXIT_CODE=0\n"
            "\n"
            ":: Record start time as seconds since midnight\n"
            'for /f "tokens=1-3 delims=:." %%a in ("%TIME: =0%") do set /a _START_S=%%a*3600+%%b*60+%%c\n'
            "\n"
            f'cd /d "{job.working_directory}"\n'
            "if errorlevel 1 (\n"
            f"    echo [ERROR] Cannot cd to working directory: {job.working_directory}\n"
            "    exit /b 1\n"
            ")\n"
            "\n"
            'echo [ClawSmith] Starting job %JOB_ID% > "%STDOUT_LOG%"\n'
            f'echo [ClawSmith] Objective: {job.objective} >> "%STDOUT_LOG%"\n'
            f'echo [ClawSmith] Timeout: {job.timeout_seconds}s >> "%STDOUT_LOG%"\n'
            "\n"
            ":: --- Build Commands ---\n"
            f"{build_block}\n"
            "call :CHECK_TIMEOUT\n"
            "if !EXIT_CODE! equ -2 goto :TIMEOUT_EXIT\n"
            "\n"
            ":: --- Test Commands ---\n"
            f"{test_block}\n"
            "call :CHECK_TIMEOUT\n"
            "if !EXIT_CODE! equ -2 goto :TIMEOUT_EXIT\n"
            "\n"
            "goto :FINISH\n"
            "\n"
            ":CHECK_TIMEOUT\n"
            'for /f "tokens=1-3 delims=:." %%a in ("%TIME: =0%") do set /a _NOW_S=%%a*3600+%%b*60+%%c\n'
            "set /a _ELAPSED=_NOW_S-_START_S\n"
            "if !_ELAPSED! lss 0 set /a _ELAPSED=_ELAPSED+86400\n"
            "if !_ELAPSED! geq %TIMEOUT_SECONDS% (\n"
            "    set EXIT_CODE=-2\n"
            '    echo [TIMEOUT] Job exceeded %TIMEOUT_SECONDS%s limit after !_ELAPSED!s >> "%STDOUT_LOG%"\n'
            '    echo [TIMEOUT] exceeded >> "%STDERR_LOG%"\n'
            '    echo -2 > "%ARTIFACT_DIR%\\timeout.txt"\n'
            ")\n"
            "goto :eof\n"
            "\n"
            ":TIMEOUT_EXIT\n"
            "exit /b -2\n"
            "\n"
            ":FINISH\n"
            'echo [ClawSmith] Job %JOB_ID% completed with exit code !EXIT_CODE! >> "%STDOUT_LOG%"\n'
            "exit /b !EXIT_CODE!\n"
        )

        bat_path = _GENERATED_DIR / f"{job.id}.bat"
        bat_path.write_text(content, encoding="utf-8")

        metadata = {
            "job": job.model_dump(mode="json"),
            "generated_at": timestamp,
            "bat_path": str(bat_path),
        }
        (artifact_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2), encoding="utf-8"
        )

        return bat_path

    # ------------------------------------------------------------------

    @staticmethod
    def _commands_block(commands: list[str], label: str) -> str:
        if not commands:
            return ""
        lines: list[str] = []
        for cmd in commands:
            lines.append(f'echo [{label}] Running: {cmd} >> "%STDOUT_LOG%"')
            lines.append(f'{cmd} >> "%STDOUT_LOG%" 2>> "%STDERR_LOG%"')
            lines.append("if errorlevel 1 set EXIT_CODE=1")
        return "\n".join(lines) + "\n"
