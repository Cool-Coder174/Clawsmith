"""Cursor CLI integration for ClawSmith jobs."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from string import Template

from config.config_loader import get_config
from jobs.schema_validator import JobSpecValidator, ValidationError
from orchestrator.logging_setup import get_logger
from orchestrator.schemas import ExecutionResult, JobSpec

_REPO_ROOT = Path(__file__).parent.parent
_GENERATED_DIR = _REPO_ROOT / "jobs" / "generated"
_TEMPLATE_PATH = _REPO_ROOT / "jobs" / "templates" / "cursor_task.bat.template"

logger = get_logger("cursor_runner")


def detect_cursor_cli() -> Path | None:
    """Locate the Cursor CLI executable.

    Checks ``CURSOR_CLI_PATH`` env var first, then searches ``PATH``.
    """
    env_path = os.environ.get("CURSOR_CLI_PATH")
    if env_path:
        p = Path(env_path)
        if p.exists():
            return p

    which_result = shutil.which("cursor")
    if which_result:
        return Path(which_result)

    return None


class CursorRunner:
    """Generates and executes Cursor CLI tasks via bat scripts."""

    @staticmethod
    def generate_cursor_bat(
        job: JobSpec,
        cursor_path: Path,
        artifact_dir: Path,
    ) -> Path:
        """Read the cursor template, substitute variables, and write a .bat file."""
        _GENERATED_DIR.mkdir(parents=True, exist_ok=True)

        template_text = _TEMPLATE_PATH.read_text(encoding="utf-8")
        tmpl = Template(template_text)

        escaped_prompt = job.prompt.replace('"', "'")

        from jobs.bat_generator import BatGenerator

        gen = BatGenerator()
        build_block = gen._commands_block(job.build_commands, "BUILD")
        test_block = gen._commands_block(job.test_commands, "TEST")

        content = tmpl.safe_substitute(
            JOB_ID=job.id,
            WORKING_DIR=job.working_directory,
            ARTIFACT_DIR=str(artifact_dir),
            BUILD_COMMANDS=build_block,
            TEST_COMMANDS=test_block,
            OBJECTIVE=job.objective,
            CURSOR_CLI_PATH=str(cursor_path),
            CURSOR_PROMPT=escaped_prompt,
            TIMEOUT_SECONDS=str(job.timeout_seconds),
        )

        bat_path = _GENERATED_DIR / f"{job.id}_cursor.bat"
        bat_path.write_text(content, encoding="utf-8")
        return bat_path

    async def run(self, job: JobSpec, dry_run: bool = False) -> ExecutionResult:
        """Execute a Cursor CLI task for the given job."""
        try:
            JobSpecValidator().validate(job, workspace_root=_REPO_ROOT, dry_run=dry_run)
        except ValidationError as exc:
            logger.error("Validation failed for cursor job %s: %s", job.id, exc)
            return ExecutionResult(
                job_id=job.id,
                exit_code=-1,
                stdout="",
                stderr="",
                duration_seconds=0.0,
                success=False,
                error_message=str(exc),
            )

        cursor_path = detect_cursor_cli()

        if cursor_path is None and not dry_run:
            logger.error("Cursor CLI not found")
            return ExecutionResult(
                job_id=job.id,
                exit_code=-1,
                stdout="",
                stderr="",
                duration_seconds=0.0,
                success=False,
                error_message=(
                    "Cursor CLI not found. Set CURSOR_CLI_PATH env var or ensure "
                    "`cursor` is on PATH. See docs/cursor_setup.md."
                ),
            )

        if dry_run:
            logger.info("[dry-run] Would execute Cursor task for job %s", job.id)
            return ExecutionResult(
                job_id=job.id,
                exit_code=0,
                stdout=f"[dry-run] Cursor task for job {job.id}",
                stderr="",
                duration_seconds=0.0,
                success=True,
            )

        config = get_config()
        artifact_dir = _REPO_ROOT / config.execution.artifacts_dir / job.id
        artifact_dir.mkdir(parents=True, exist_ok=True)

        bat_path = self.generate_cursor_bat(job, cursor_path, artifact_dir)  # type: ignore[arg-type]

        from jobs.executor import JobExecutor

        executor = JobExecutor()
        return await executor.run_bat(job, bat_path)
