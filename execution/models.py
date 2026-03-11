"""Data models for the phase execution engine."""

from __future__ import annotations

import time
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


class PhaseExecStatus(StrEnum):
    pending = "pending"
    generating = "generating"
    executing = "executing"
    verifying = "verifying"
    retrying = "retrying"
    completed = "completed"
    failed = "failed"
    paused = "paused"


class PhaseExecutionResult(BaseModel):
    """Outcome of executing a single phase through a backend."""

    model_config = ConfigDict(frozen=False)

    phase_id: str
    phase_index: int
    title: str
    status: PhaseExecStatus = PhaseExecStatus.pending
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    prompt_generated: str = ""
    prompt_file: str | None = None
    command_executed: str = ""
    backend_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_seconds: float = 0.0
    attempt: int = 0
    max_attempts: int = 1
    retry_count: int = 0
    verification_passed: bool | None = None
    verification_detail: str = ""
    error_message: str | None = None
    error_history: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == PhaseExecStatus.completed and self.exit_code == 0


class RunManifest(BaseModel):
    """Persistent state for a YOLO run, enabling resume from last success."""

    model_config = ConfigDict(frozen=False)

    run_id: str = Field(default_factory=lambda: uuid4().hex[:12])
    goal: str = ""
    repo_path: str = ""
    backend_id: str = "cli_agent"
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    total_phases: int = 0
    last_completed_index: int = -1
    phase_results: list[PhaseExecutionResult] = Field(default_factory=list)
    plan_snapshot: dict[str, Any] = Field(default_factory=dict)
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    is_complete: bool = False
    is_failed: bool = False
    is_paused: bool = False
    failure_reason: str | None = None

    def save(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"manifest_{self.run_id}.json"
        self.updated_at = time.time()
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")
        return path

    @classmethod
    def load(cls, path: Path) -> RunManifest:
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    @classmethod
    def find_latest(cls, directory: Path) -> RunManifest | None:
        if not directory.exists():
            return None
        manifests = sorted(
            directory.glob("manifest_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        return cls.load(manifests[0]) if manifests else None

    @classmethod
    def find_resumable(cls, directory: Path) -> RunManifest | None:
        """Find the most recent manifest that is paused or has a failed phase."""
        if not directory.exists():
            return None
        for path in sorted(
            directory.glob("manifest_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        ):
            manifest = cls.load(path)
            if not manifest.is_complete and (manifest.is_paused or manifest.is_failed):
                return manifest
        return None
