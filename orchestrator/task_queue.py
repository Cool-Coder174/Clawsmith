"""FIFO phase queue for YOLO mode execution.

Manages the ordered dispatch of decomposed phases, tracks per-phase state,
and supports pause/resume semantics so YOLO runs can survive failures,
rate limits, and session interruptions.
"""

from __future__ import annotations

import time
from collections import deque
from typing import Any

from orchestrator.logging_setup import get_logger
from orchestrator.schemas import (
    PipelineResult,
    YoloPhase,
    YoloPhaseResult,
    YoloPhaseStatus,
)

logger = get_logger("task_queue")


class QueueExhausted(Exception):
    """Raised when ``next()`` is called on an empty queue."""


class QueuePaused(Exception):
    """Raised when ``next()`` is called while the queue is paused."""


class TaskQueue:
    """FIFO queue that dispatches YOLO phases one at a time.

    Lifecycle of a phase inside the queue::

        pending → running → verifying → completed
                                      ↘ retrying → running  (loop)
                                      ↘ failed / paused
    """

    def __init__(self, phases: list[YoloPhase]) -> None:
        self._pending: deque[YoloPhase] = deque(phases)
        self._results: dict[str, YoloPhaseResult] = {}
        self._current: YoloPhase | None = None
        self._paused: bool = False
        self._total: int = len(phases)

        for phase in phases:
            self._results[phase.id] = YoloPhaseResult(
                phase_id=phase.id,
                phase_index=phase.index,
                title=phase.title,
                status=YoloPhaseStatus.pending,
            )

    # -- dispatch -----------------------------------------------------------

    def next(self) -> YoloPhase:
        """Pop the next pending phase. Raises on empty or paused."""
        if self._paused:
            raise QueuePaused("Queue is paused — call resume() first")
        if not self._pending:
            raise QueueExhausted("No more phases in the queue")

        phase = self._pending.popleft()
        phase.status = YoloPhaseStatus.running
        self._current = phase
        self._results[phase.id].status = YoloPhaseStatus.running
        logger.info("Dispatching phase %d/%d: %s", phase.index + 1, self._total, phase.title)
        return phase

    def peek(self) -> YoloPhase | None:
        """Look at the next phase without consuming it."""
        return self._pending[0] if self._pending else None

    # -- completion ---------------------------------------------------------

    def complete(
        self,
        phase: YoloPhase,
        pipeline_result: PipelineResult,
        duration: float = 0.0,
    ) -> YoloPhaseResult:
        """Mark a phase as successfully completed."""
        phase.status = YoloPhaseStatus.completed
        result = self._results[phase.id]
        result.status = YoloPhaseStatus.completed
        result.pipeline_result = pipeline_result
        result.attempts += 1
        result.duration_seconds = duration
        if self._current and self._current.id == phase.id:
            self._current = None
        logger.info("Phase %d/%d completed: %s", phase.index + 1, self._total, phase.title)
        return result

    def fail(
        self,
        phase: YoloPhase,
        error: str,
        *,
        can_retry: bool = False,
    ) -> YoloPhaseResult:
        """Record a phase failure. Optionally re-enqueues for retry."""
        result = self._results[phase.id]
        result.error_history.append(error)
        result.attempts += 1

        if can_retry:
            phase.status = YoloPhaseStatus.retrying
            result.status = YoloPhaseStatus.retrying
            self._pending.appendleft(phase)
            logger.info(
                "Phase %d/%d queued for retry (attempt %d): %s — %s",
                phase.index + 1, self._total, result.attempts, phase.title, error,
            )
        else:
            phase.status = YoloPhaseStatus.failed
            result.status = YoloPhaseStatus.failed
            logger.warning(
                "Phase %d/%d failed: %s — %s",
                phase.index + 1, self._total, phase.title, error,
            )

        if self._current and self._current.id == phase.id:
            self._current = None
        return result

    def skip(self, phase: YoloPhase, reason: str = "") -> YoloPhaseResult:
        """Mark a phase as skipped without executing it."""
        phase.status = YoloPhaseStatus.skipped
        result = self._results[phase.id]
        result.status = YoloPhaseStatus.skipped
        if reason:
            result.error_history.append(f"Skipped: {reason}")
        logger.info("Phase %d/%d skipped: %s", phase.index + 1, self._total, phase.title)
        return result

    # -- pause / resume -----------------------------------------------------

    def pause(self, reason: str = "") -> None:
        """Pause the queue. Current phase stays in-flight."""
        self._paused = True
        if self._current:
            self._current.status = YoloPhaseStatus.paused
            self._results[self._current.id].status = YoloPhaseStatus.paused
        logger.info("Queue paused: %s", reason or "user requested")

    def resume(self) -> None:
        """Resume a paused queue.

        If a phase was paused mid-flight, it is re-enqueued at the front.
        """
        self._paused = False
        if self._current and self._current.status == YoloPhaseStatus.paused:
            self._current.status = YoloPhaseStatus.pending
            self._results[self._current.id].status = YoloPhaseStatus.pending
            self._pending.appendleft(self._current)
            self._current = None
        logger.info("Queue resumed")

    # -- queries ------------------------------------------------------------

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def is_exhausted(self) -> bool:
        return len(self._pending) == 0 and self._current is None

    @property
    def current(self) -> YoloPhase | None:
        return self._current

    @property
    def remaining(self) -> int:
        return len(self._pending)

    @property
    def total(self) -> int:
        return self._total

    @property
    def completed_count(self) -> int:
        return sum(1 for r in self._results.values() if r.status == YoloPhaseStatus.completed)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self._results.values() if r.status == YoloPhaseStatus.failed)

    @property
    def skipped_count(self) -> int:
        return sum(1 for r in self._results.values() if r.status == YoloPhaseStatus.skipped)

    def progress(self) -> tuple[int, int]:
        """Return ``(completed_or_terminal, total)``."""
        terminal = sum(
            1 for r in self._results.values()
            if r.status in (YoloPhaseStatus.completed, YoloPhaseStatus.failed, YoloPhaseStatus.skipped)
        )
        return terminal, self._total

    def results(self) -> list[YoloPhaseResult]:
        """Return per-phase results in original index order."""
        return sorted(self._results.values(), key=lambda r: r.phase_index)

    def summary(self) -> dict[str, Any]:
        """JSON-serialisable snapshot of queue state."""
        done, total = self.progress()
        return {
            "total_phases": total,
            "completed": self.completed_count,
            "failed": self.failed_count,
            "skipped": self.skipped_count,
            "remaining": self.remaining,
            "paused": self._paused,
            "current_phase": self._current.title if self._current else None,
            "progress_fraction": done / total if total else 1.0,
        }
