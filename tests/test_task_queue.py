"""Tests for the YOLO FIFO task queue."""

from __future__ import annotations

import pytest

from orchestrator.schemas import (
    PipelineResult,
    YoloPhase,
    YoloPhaseStatus,
)
from orchestrator.task_queue import (
    QueueExhausted,
    QueuePaused,
    TaskQueue,
)


def _make_phases(n: int) -> list[YoloPhase]:
    return [
        YoloPhase(
            index=i,
            title=f"Phase {i + 1}",
            objective=f"Objective for phase {i + 1}",
        )
        for i in range(n)
    ]


def _ok_result() -> PipelineResult:
    return PipelineResult(
        task_description="test",
        repo_path="/repo",
        success=True,
        duration_seconds=1.0,
    )


def _fail_result(msg: str = "boom") -> PipelineResult:
    return PipelineResult(
        task_description="test",
        repo_path="/repo",
        success=False,
        error_message=msg,
        duration_seconds=1.0,
    )


class TestBasicDispatch:
    def test_dispatch_in_order(self) -> None:
        phases = _make_phases(3)
        queue = TaskQueue(phases)
        for i in range(3):
            phase = queue.next()
            assert phase.index == i

    def test_exhausted_raises(self) -> None:
        queue = TaskQueue(_make_phases(1))
        queue.next()
        queue.complete(queue.current, _ok_result())
        with pytest.raises(QueueExhausted):
            queue.next()

    def test_peek_does_not_consume(self) -> None:
        queue = TaskQueue(_make_phases(2))
        peeked = queue.peek()
        assert peeked is not None
        assert peeked.index == 0
        dispatched = queue.next()
        assert dispatched.index == 0


class TestCompletion:
    def test_complete_updates_status(self) -> None:
        phases = _make_phases(2)
        queue = TaskQueue(phases)
        p = queue.next()
        result = queue.complete(p, _ok_result(), duration=2.5)
        assert result.status == YoloPhaseStatus.completed
        assert result.duration_seconds == 2.5
        assert queue.completed_count == 1

    def test_complete_clears_current(self) -> None:
        queue = TaskQueue(_make_phases(1))
        p = queue.next()
        queue.complete(p, _ok_result())
        assert queue.current is None


class TestFailure:
    def test_fail_without_retry(self) -> None:
        queue = TaskQueue(_make_phases(2))
        p = queue.next()
        result = queue.fail(p, "error", can_retry=False)
        assert result.status == YoloPhaseStatus.failed
        assert queue.failed_count == 1

    def test_fail_with_retry_re_enqueues(self) -> None:
        queue = TaskQueue(_make_phases(2))
        p = queue.next()
        result = queue.fail(p, "transient error", can_retry=True)
        assert result.status == YoloPhaseStatus.retrying
        next_p = queue.next()
        assert next_p.id == p.id

    def test_error_history_accumulates(self) -> None:
        queue = TaskQueue(_make_phases(1))
        p = queue.next()
        queue.fail(p, "err1", can_retry=True)
        p2 = queue.next()
        queue.fail(p2, "err2", can_retry=False)
        results = queue.results()
        assert len(results[0].error_history) == 2


class TestSkip:
    def test_skip_marks_phase(self) -> None:
        queue = TaskQueue(_make_phases(2))
        p = queue.next()
        result = queue.skip(p, "not needed")
        assert result.status == YoloPhaseStatus.skipped
        assert queue.skipped_count == 1


class TestPauseResume:
    def test_pause_blocks_next(self) -> None:
        queue = TaskQueue(_make_phases(3))
        queue.next()
        queue.pause("test")
        with pytest.raises(QueuePaused):
            queue.next()

    def test_resume_re_enqueues_current(self) -> None:
        queue = TaskQueue(_make_phases(3))
        p = queue.next()
        queue.pause("test")
        assert queue.is_paused
        queue.resume()
        assert not queue.is_paused
        resumed = queue.next()
        assert resumed.id == p.id

    def test_pause_without_current(self) -> None:
        queue = TaskQueue(_make_phases(2))
        queue.pause("preemptive")
        assert queue.is_paused
        queue.resume()
        p = queue.next()
        assert p.index == 0


class TestProgress:
    def test_progress_tracking(self) -> None:
        phases = _make_phases(3)
        queue = TaskQueue(phases)

        assert queue.progress() == (0, 3)
        assert queue.total == 3
        assert queue.remaining == 3

        p1 = queue.next()
        queue.complete(p1, _ok_result())
        assert queue.progress() == (1, 3)

        p2 = queue.next()
        queue.fail(p2, "oops", can_retry=False)
        assert queue.progress() == (2, 3)

    def test_summary(self) -> None:
        queue = TaskQueue(_make_phases(2))
        p1 = queue.next()
        queue.complete(p1, _ok_result())
        summary = queue.summary()
        assert summary["total_phases"] == 2
        assert summary["completed"] == 1
        assert summary["remaining"] == 1
        assert not summary["paused"]


class TestResults:
    def test_results_in_index_order(self) -> None:
        phases = _make_phases(3)
        queue = TaskQueue(phases)
        for _ in range(3):
            p = queue.next()
            queue.complete(p, _ok_result())
        results = queue.results()
        assert [r.phase_index for r in results] == [0, 1, 2]
