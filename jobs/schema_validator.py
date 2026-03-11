"""Validation layer for JobSpec objects before execution."""

from __future__ import annotations

from pathlib import Path

from jobs.allowlist import validate_command
from orchestrator.schemas import JobSpec, TaskType


class ValidationError(ValueError):
    """Raised when a JobSpec fails pre-execution validation."""


class JobSpecValidator:
    """Validates a :class:`JobSpec` against safety and correctness rules."""

    def validate(
        self,
        job: JobSpec,
        workspace_root: Path | None = None,
        dry_run: bool = False,
    ) -> JobSpec:
        """Run all checks and return *job* unchanged on success.

        *dry_run* (or ``job.dry_run``) skips directory-existence checks while
        keeping all safety checks active.

        Raises :class:`ValidationError` with an actionable message on failure.
        """
        effective_dry_run = dry_run or job.dry_run
        self._check_task_type(job)
        self._check_timeout(job)
        self._check_working_directory(job, workspace_root, effective_dry_run)
        self._check_commands(job)
        return job

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_task_type(job: JobSpec) -> None:
        if job.task_type not in TaskType:
            raise ValidationError(
                f"Invalid task_type '{job.task_type}'. "
                f"Must be one of: {[t.value for t in TaskType]}"
            )

    @staticmethod
    def _check_timeout(job: JobSpec) -> None:
        if not (10 <= job.timeout_seconds <= 3600):
            raise ValidationError(
                f"timeout_seconds={job.timeout_seconds} is out of range. "
                "Must be between 10 and 3600 seconds."
            )

    @staticmethod
    def _check_working_directory(
        job: JobSpec,
        workspace_root: Path | None,
        dry_run: bool = False,
    ) -> None:
        wd = Path(job.working_directory)

        if ".." in wd.parts:
            raise ValidationError(
                f"working_directory '{job.working_directory}' contains '..' segments, "
                "which is not allowed for safety reasons."
            )

        if wd.is_absolute():
            if workspace_root is None:
                raise ValidationError(
                    f"working_directory '{job.working_directory}' is an absolute path "
                    "but no workspace root was provided for safety validation."
                )
            try:
                wd.resolve().relative_to(workspace_root.resolve())
            except ValueError:
                raise ValidationError(
                    f"working_directory '{job.working_directory}' is outside the "
                    f"workspace root '{workspace_root}'."
                )

        if not dry_run and not wd.exists():
            raise ValidationError(
                f"working_directory '{job.working_directory}' does not exist."
            )

    @staticmethod
    def _check_commands(job: JobSpec) -> None:
        for cmd in job.build_commands:
            if not validate_command(cmd):
                raise ValidationError(
                    f"Build command not allowed: '{cmd}'. "
                    "Its executable is not in the allowlist."
                )

        for cmd in job.test_commands:
            if not validate_command(cmd):
                raise ValidationError(
                    f"Test command not allowed: '{cmd}'. "
                    "Its executable is not in the allowlist."
                )
