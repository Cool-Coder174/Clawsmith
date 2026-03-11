"""Job executor: validates, generates, and runs .bat scripts for ClawSmith jobs."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential

from config.config_loader import get_config
from orchestrator.logging_setup import get_logger
from orchestrator.schemas import ExecutionResult, JobSpec

from jobs.bat_generator import BatGenerator
from jobs.schema_validator import JobSpecValidator, ValidationError

_REPO_ROOT = Path(__file__).parent.parent

logger = get_logger("executor")


class JobExecutor:
    """Validates a job, generates its .bat file, and runs it with retry logic."""

    async def execute(self, job: JobSpec, dry_run: bool = False) -> ExecutionResult:
        """End-to-end execution pipeline for a single job."""
        try:
            JobSpecValidator().validate(job, workspace_root=_REPO_ROOT, dry_run=dry_run)
        except ValidationError as exc:
            logger.error("Validation failed for job %s: %s", job.id, exc)
            return ExecutionResult(
                job_id=job.id,
                exit_code=-1,
                stdout="",
                stderr="",
                duration_seconds=0.0,
                success=False,
                error_message=str(exc),
            )

        if dry_run:
            cmds = job.build_commands + job.test_commands
            logger.info("[dry-run] Job %s would execute: %s", job.id, cmds)
            return ExecutionResult(
                job_id=job.id,
                exit_code=0,
                stdout=f"[dry-run] Would execute {len(cmds)} commands",
                stderr="",
                duration_seconds=0.0,
                success=True,
                artifacts=[],
            )

        bat_path = BatGenerator().generate(job)
        return await self.run_bat(job, bat_path)

    async def run_bat(self, job: JobSpec, bat_path: Path) -> ExecutionResult:
        """Run a pre-generated bat file with retry logic.

        The caller is responsible for validating the job beforehand.
        Retries are governed by ``job.retries``.
        """
        config = get_config()
        artifact_dir = _REPO_ROOT / config.execution.artifacts_dir / job.id

        logger.info("Starting job %s (timeout=%ds, retries=%d)", job.id, job.timeout_seconds, job.retries)

        result: ExecutionResult | None = None
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(job.retries + 1),
                wait=wait_exponential(multiplier=1, min=2, max=10),
                reraise=True,
            ):
                with attempt:
                    logger.debug(
                        "Job %s attempt %d/%d",
                        job.id,
                        attempt.retry_state.attempt_number,
                        job.retries + 1,
                    )
                    result = await self._run_bat(bat_path, job.timeout_seconds, artifact_dir, job.id)
                    if not result.success:
                        raise RuntimeError(result.error_message or f"Job {job.id} failed")
        except Exception:
            if result is not None:
                return result
            return ExecutionResult(
                job_id=job.id,
                exit_code=-1,
                stdout="",
                stderr="",
                duration_seconds=0.0,
                success=False,
                error_message=f"Job {job.id} failed after {job.retries + 1} attempts",
            )

        logger.info("Job %s completed successfully", job.id)
        return result

    async def _run_bat(
        self,
        bat_path: Path,
        timeout: int,
        artifact_dir: Path,
        job_id: str = "",
    ) -> ExecutionResult:
        """Execute a ``.bat`` file as an async subprocess."""
        start = time.monotonic()

        proc = await asyncio.create_subprocess_exec(
            "cmd.exe", "/c", str(bat_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            raw_stdout, raw_stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except asyncio.TimeoutError:
            proc.kill()
            elapsed = time.monotonic() - start
            timeout_marker = artifact_dir / "timeout.txt"
            timeout_marker.write_text(
                f"Job timed out after {timeout}s\n", encoding="utf-8"
            )
            logger.error("Job %s timed out after %ds", job_id, timeout)
            return ExecutionResult(
                job_id=job_id,
                exit_code=-2,
                stdout="",
                stderr="",
                duration_seconds=elapsed,
                success=False,
                error_message=f"Job timed out after {timeout}s",
            )

        elapsed = time.monotonic() - start

        stdout_text = raw_stdout.decode("utf-8", errors="replace") if raw_stdout else ""
        stderr_text = raw_stderr.decode("utf-8", errors="replace") if raw_stderr else ""

        artifact_dir.mkdir(parents=True, exist_ok=True)
        (artifact_dir / "subprocess_stdout.log").write_text(stdout_text, encoding="utf-8")
        (artifact_dir / "subprocess_stderr.log").write_text(stderr_text, encoding="utf-8")
        (artifact_dir / "exit_code.txt").write_text(
            str(proc.returncode), encoding="utf-8"
        )

        artifacts = [
            str(p.relative_to(artifact_dir)) for p in artifact_dir.iterdir() if p.is_file()
        ]

        return ExecutionResult(
            job_id=job_id,
            exit_code=proc.returncode or 0,
            stdout=stdout_text,
            stderr=stderr_text,
            artifacts=artifacts,
            duration_seconds=elapsed,
            success=proc.returncode == 0,
        )
