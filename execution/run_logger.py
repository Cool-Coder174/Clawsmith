"""Structured per-run, per-phase logging for execution.

Logs are stored as::

    logs/runs/<run_id>/
        run_meta.json           # run-level metadata
        phase_01_design.json    # per-phase result + prompt + output
        phase_02_implement.json
        ...

Each phase log captures: phase index/name, generated prompt, command executed,
stdout, stderr, exit code, start/end timestamps, retry count, and verification
result.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from execution.models import PhaseExecutionResult, RunManifest
from orchestrator.logging_setup import get_logger

logger = get_logger("run_logger")


def _sanitize_filename(name: str) -> str:
    """Convert a phase title to a safe filename component."""
    safe = re.sub(r"[^\w\s-]", "", name.lower())
    safe = re.sub(r"[\s]+", "_", safe.strip())
    return safe[:50] or "unnamed"


class PhaseRunLogger:
    """Writes structured JSON logs for each phase in a run."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = base_dir or (Path.cwd() / "logs" / "runs")

    def init_run(
        self,
        run_id: str,
        goal: str,
        repo_path: str,
        total_phases: int,
        backend_id: str,
        extra_meta: dict[str, Any] | None = None,
    ) -> Path:
        """Create the run directory and write initial metadata."""
        run_dir = self._base / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        meta = {
            "run_id": run_id,
            "goal": goal,
            "repo_path": repo_path,
            "total_phases": total_phases,
            "backend_id": backend_id,
            "started_at": time.time(),
            "started_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        if extra_meta:
            meta.update(extra_meta)

        (run_dir / "run_meta.json").write_text(
            json.dumps(meta, indent=2), encoding="utf-8",
        )
        logger.info("Run log directory created: %s", run_dir)
        return run_dir

    def log_phase(
        self,
        run_id: str,
        result: PhaseExecutionResult,
    ) -> Path:
        """Write a single phase's execution log."""
        run_dir = self._base / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        safe_title = _sanitize_filename(result.title)
        filename = f"phase_{result.phase_index + 1:02d}_{safe_title}.json"

        entry = {
            "phase_id": result.phase_id,
            "phase_index": result.phase_index,
            "title": result.title,
            "status": result.status.value,
            "attempt": result.attempt,
            "retry_count": result.retry_count,
            "backend_id": result.backend_id,
            "command_executed": result.command_executed,
            "exit_code": result.exit_code,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "duration_seconds": round(result.duration_seconds, 3),
            "start_time_iso": (
                time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(result.start_time))
                if result.start_time else ""
            ),
            "end_time_iso": (
                time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(result.end_time))
                if result.end_time else ""
            ),
            "prompt_generated": result.prompt_generated,
            "prompt_file": result.prompt_file,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "error_message": result.error_message,
            "error_history": result.error_history,
            "verification_passed": result.verification_passed,
            "verification_detail": result.verification_detail,
            "metadata": result.metadata,
        }

        path = run_dir / filename
        path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
        logger.info("Phase log written: %s", path)
        return path

    def finalize_run(
        self,
        run_id: str,
        success: bool,
        duration_seconds: float,
        completed_phases: int,
        failed_phases: int,
        error_message: str | None = None,
    ) -> Path:
        """Update run metadata with final results."""
        run_dir = self._base / run_id
        meta_path = run_dir / "run_meta.json"

        meta: dict[str, Any] = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))

        meta.update({
            "finished_at": time.time(),
            "finished_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "success": success,
            "duration_seconds": round(duration_seconds, 3),
            "completed_phases": completed_phases,
            "failed_phases": failed_phases,
            "error_message": error_message,
        })

        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        logger.info("Run finalized: %s (success=%s)", run_id, success)
        return meta_path
