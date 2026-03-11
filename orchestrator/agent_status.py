"""Structured agent lifecycle tracking.

Provides a Traycer-style status model so callers (CLI, TUI, MCP) always
know where the pipeline is:

    deployed -> planning -> executing -> verifying -> complete | failed

Each phase can emit granular sub-step events, and an optional callback
is invoked on every transition so UIs can render live progress.
"""

from __future__ import annotations

import time
from enum import StrEnum
from typing import Any, Callable, Protocol
from uuid import uuid4

from pydantic import BaseModel, Field


# -- lifecycle phases -------------------------------------------------------

class AgentPhase(StrEnum):
    """Top-level lifecycle phases, ordered by execution flow."""

    pending = "pending"
    deployed = "deployed"
    decomposing = "decomposing"
    planning = "planning"
    queued = "queued"
    executing = "executing"
    verifying = "verifying"
    retrying = "retrying"
    complete = "complete"
    failed = "failed"


PHASE_ORDER: list[AgentPhase] = [
    AgentPhase.pending,
    AgentPhase.deployed,
    AgentPhase.decomposing,
    AgentPhase.planning,
    AgentPhase.queued,
    AgentPhase.executing,
    AgentPhase.verifying,
    AgentPhase.complete,
]


class VerifyStage(StrEnum):
    """Sub-stages within the *verifying* phase."""

    build = "build"
    compile_check = "compile_check"
    fix_errors = "fix_errors"
    compare_main = "compare_main"
    fix_conflicts = "fix_conflicts"
    done = "done"


# -- event model ------------------------------------------------------------

class StatusEvent(BaseModel):
    """A single status transition or sub-step update."""

    phase: AgentPhase
    step: str
    detail: str = ""
    verify_stage: VerifyStage | None = None
    timestamp: float = Field(default_factory=time.time)
    elapsed_seconds: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


# -- callback protocol ------------------------------------------------------

class StatusCallback(Protocol):
    """Anything callable with ``(StatusEvent) -> None`` satisfies this."""

    def __call__(self, event: StatusEvent) -> None: ...


# -- tracker ----------------------------------------------------------------

class StatusTracker:
    """Accumulates lifecycle events and notifies subscribers.

    Usage::

        tracker = StatusTracker()
        tracker.on_status(my_ui_callback)

        tracker.transition(AgentPhase.deployed, "Pipeline started")
        tracker.transition(AgentPhase.planning, "Auditing repository")
        tracker.step("Mapping repository structure")
        ...
        tracker.transition(AgentPhase.complete, "Pipeline finished")
    """

    def __init__(self, run_id: str | None = None) -> None:
        self.run_id: str = run_id or uuid4().hex[:12]
        self.phase: AgentPhase = AgentPhase.pending
        self.verify_stage: VerifyStage | None = None
        self.events: list[StatusEvent] = []
        self._callbacks: list[Callable[[StatusEvent], None]] = []
        self._start: float = time.monotonic()
        self._yolo_meta: dict[str, Any] = {}

    # -- YOLO helpers -------------------------------------------------------

    def set_yolo_progress(
        self,
        current_phase: int,
        total_phases: int,
        phase_title: str = "",
        attempt: int = 1,
    ) -> None:
        """Attach YOLO queue progress so summary() includes it."""
        self._yolo_meta = {
            "yolo_current_phase": current_phase,
            "yolo_total_phases": total_phases,
            "yolo_phase_title": phase_title,
            "yolo_attempt": attempt,
        }

    # -- subscription -------------------------------------------------------

    def on_status(self, callback: Callable[[StatusEvent], None]) -> None:
        """Register a callback invoked on every status event."""
        self._callbacks.append(callback)

    # -- transitions --------------------------------------------------------

    def transition(
        self,
        phase: AgentPhase,
        step: str,
        detail: str = "",
        **metadata: Any,
    ) -> StatusEvent:
        """Move to a new lifecycle phase and emit an event."""
        self.phase = phase
        self.verify_stage = None
        return self._emit(phase, step, detail, metadata=metadata)

    def step(self, step: str, detail: str = "", **metadata: Any) -> StatusEvent:
        """Emit a sub-step event within the current phase."""
        return self._emit(self.phase, step, detail, metadata=metadata)

    def verify(
        self,
        stage: VerifyStage,
        step: str,
        detail: str = "",
        **metadata: Any,
    ) -> StatusEvent:
        """Emit a verification sub-stage event."""
        self.verify_stage = stage
        return self._emit(
            AgentPhase.verifying, step, detail,
            verify_stage=stage, metadata=metadata,
        )

    def fail(self, step: str, detail: str = "", **metadata: Any) -> StatusEvent:
        """Transition to the failed phase."""
        self.phase = AgentPhase.failed
        return self._emit(AgentPhase.failed, step, detail, metadata=metadata)

    # -- queries ------------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return time.monotonic() - self._start

    @property
    def is_terminal(self) -> bool:
        return self.phase in (AgentPhase.complete, AgentPhase.failed)

    @property
    def phase_index(self) -> int:
        """0-based index of the current phase in the lifecycle."""
        try:
            return PHASE_ORDER.index(self.phase)
        except ValueError:
            return len(PHASE_ORDER)

    @property
    def progress_fraction(self) -> float:
        """Rough 0.0-1.0 progress through the lifecycle."""
        total = len(PHASE_ORDER) - 1
        return min(self.phase_index / total, 1.0) if total else 1.0

    def summary(self) -> dict[str, Any]:
        """Snapshot suitable for JSON serialisation or display."""
        base: dict[str, Any] = {
            "run_id": self.run_id,
            "phase": self.phase.value,
            "verify_stage": self.verify_stage.value if self.verify_stage else None,
            "elapsed_seconds": round(self.elapsed, 2),
            "step_count": len(self.events),
            "latest_step": self.events[-1].step if self.events else "",
            "is_terminal": self.is_terminal,
        }
        if self._yolo_meta:
            base.update(self._yolo_meta)
        return base

    # -- internals ----------------------------------------------------------

    def _emit(
        self,
        phase: AgentPhase,
        step: str,
        detail: str,
        *,
        verify_stage: VerifyStage | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StatusEvent:
        event = StatusEvent(
            phase=phase,
            step=step,
            detail=detail,
            verify_stage=verify_stage,
            elapsed_seconds=round(self.elapsed, 2),
            metadata=metadata or {},
        )
        self.events.append(event)
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception:
                pass
        return event
