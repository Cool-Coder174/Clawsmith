from __future__ import annotations

from pathlib import Path

import pytest

from jobs.schema_validator import JobSpecValidator, ValidationError
from orchestrator.schemas import JobSpec, TaskType

validator = JobSpecValidator()


def test_valid_spec_passes(sample_job_spec, tmp_repo):
    validator.validate(sample_job_spec, workspace_root=tmp_repo.parent, dry_run=True)


def test_disallowed_build_command_rejected(tmp_repo):
    job = JobSpec(
        task_type=TaskType.bugfix,
        objective="test",
        working_directory=str(tmp_repo),
        build_commands=["rm -rf /"],
        test_commands=[],
        prompt="test",
        dry_run=True,
    )
    with pytest.raises(ValidationError):
        validator.validate(job, workspace_root=tmp_repo.parent, dry_run=True)


def test_disallowed_test_command_rejected(tmp_repo):
    job = JobSpec(
        task_type=TaskType.bugfix,
        objective="test",
        working_directory=str(tmp_repo),
        build_commands=[],
        test_commands=["powershell -Command evil"],
        prompt="test",
        dry_run=True,
    )
    with pytest.raises(ValidationError):
        validator.validate(job, workspace_root=tmp_repo.parent, dry_run=True)


def test_dotdot_in_working_directory_rejected():
    job = JobSpec(
        task_type=TaskType.bugfix,
        objective="test",
        working_directory="../outside",
        build_commands=[],
        test_commands=[],
        prompt="test",
        dry_run=True,
    )
    with pytest.raises(ValidationError):
        validator.validate(job, dry_run=True)


def test_timeout_too_low_rejected(tmp_repo):
    job = JobSpec(
        task_type=TaskType.bugfix,
        objective="test",
        working_directory=str(tmp_repo),
        build_commands=[],
        test_commands=[],
        prompt="test",
        timeout_seconds=10,
        dry_run=True,
    )
    validator.validate(job, workspace_root=tmp_repo.parent, dry_run=True)

    with pytest.raises(Exception):
        JobSpec(
            task_type=TaskType.bugfix,
            objective="test",
            working_directory=str(tmp_repo),
            build_commands=[],
            test_commands=[],
            prompt="test",
            timeout_seconds=5,
            dry_run=True,
        )


def test_timeout_too_high_rejected(tmp_repo):
    with pytest.raises(Exception):
        JobSpec(
            task_type=TaskType.bugfix,
            objective="test",
            working_directory=str(tmp_repo),
            build_commands=[],
            test_commands=[],
            prompt="test",
            timeout_seconds=9999,
            dry_run=True,
        )


def test_nonexistent_working_directory_rejected():
    job = JobSpec(
        task_type=TaskType.bugfix,
        objective="test",
        working_directory="C:/does/not/exist",
        build_commands=[],
        test_commands=[],
        prompt="test",
        dry_run=False,
    )
    with pytest.raises(ValidationError):
        validator.validate(job, workspace_root=Path("C:/does"), dry_run=False)


def test_dry_run_skips_directory_existence_check():
    job = JobSpec(
        task_type=TaskType.bugfix,
        objective="test",
        working_directory="C:/does/not/exist",
        build_commands=[],
        test_commands=[],
        prompt="test",
        dry_run=True,
    )
    validator.validate(job, workspace_root=Path("C:/does"), dry_run=True)


def test_shell_metacharacter_rejected(tmp_repo):
    job = JobSpec(
        task_type=TaskType.bugfix,
        objective="test",
        working_directory=str(tmp_repo),
        build_commands=["python && evil"],
        test_commands=[],
        prompt="test",
        dry_run=True,
    )
    with pytest.raises(ValidationError):
        validator.validate(job, workspace_root=tmp_repo.parent, dry_run=True)
