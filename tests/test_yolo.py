"""Tests for the YOLO execution engine."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from execution.backend import BackendConfig
from execution.models import PhaseExecStatus, PhaseExecutionResult
from orchestrator.agent_status import StatusTracker
from orchestrator.schemas import (
    PipelineResult,
    YoloConfig,
    YoloPhaseStatus,
)
from orchestrator.yolo import YoloEngine


def _ok_exec_result(**kwargs) -> PhaseExecutionResult:
    defaults = dict(
        phase_id="test_phase",
        phase_index=0,
        title="Test Phase",
        status=PhaseExecStatus.completed,
        exit_code=0,
        stdout="OK",
        stderr="",
        prompt_generated="test prompt",
        command_executed='agent chat "$env:CLAWSMITH_PROMPT"',
        backend_id="cli_agent",
        duration_seconds=1.0,
        start_time=0.0,
        end_time=1.0,
    )
    defaults.update(kwargs)
    return PhaseExecutionResult(**defaults)


def _fail_exec_result(msg: str = "boom", **kwargs) -> PhaseExecutionResult:
    defaults = dict(
        phase_id="test_phase",
        phase_index=0,
        title="Test Phase",
        status=PhaseExecStatus.failed,
        exit_code=1,
        stdout="",
        stderr=msg,
        error_message=msg,
        prompt_generated="test prompt",
        command_executed='agent chat "$env:CLAWSMITH_PROMPT"',
        backend_id="cli_agent",
        duration_seconds=1.0,
        start_time=0.0,
        end_time=1.0,
    )
    defaults.update(kwargs)
    return PhaseExecutionResult(**defaults)


def _ok_pipeline_result(**kwargs) -> PipelineResult:
    defaults = dict(
        task_description="test",
        repo_path="/repo",
        success=True,
        duration_seconds=1.0,
    )
    defaults.update(kwargs)
    return PipelineResult(**defaults)


@pytest.fixture()
def engine() -> YoloEngine:
    return YoloEngine()


class TestYoloBasic:
    @pytest.mark.asyncio
    async def test_invalid_repo_path(self, engine: YoloEngine) -> None:
        result = await engine.execute("Fix bug", "/nonexistent/path/xxx")
        assert not result.success
        assert "does not exist" in (result.error_message or "")

    @pytest.mark.asyncio
    async def test_successful_simple_run(self, engine: YoloEngine, tmp_repo) -> None:
        mock_exec = AsyncMock(return_value=_ok_exec_result())

        with patch(
            "execution.cli_agent.CliAgentBackend.execute_phase", mock_exec,
        ), patch(
            "execution.cli_agent.CliAgentBackend.health_check",
            AsyncMock(return_value=True),
        ):
            result = await engine.execute(
                "Fix a typo",
                str(tmp_repo),
                config=YoloConfig(dry_run=True),
            )

        assert result.success
        assert result.total_phases >= 1
        assert result.completed_phases >= 1
        assert result.failed_phases == 0
        assert result.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_phase_failure_with_retry(self, engine: YoloEngine, tmp_repo) -> None:
        call_count = 0

        async def alternating_result(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _fail_exec_result("first attempt failed")
            return _ok_exec_result()

        with patch(
            "execution.cli_agent.CliAgentBackend.execute_phase",
            side_effect=alternating_result,
        ):
            result = await engine.execute(
                "Fix bug",
                str(tmp_repo),
                config=YoloConfig(max_retries=2),
            )

        assert result.success
        assert result.completed_phases >= 1

    @pytest.mark.asyncio
    async def test_phase_exhausts_retries(self, engine: YoloEngine, tmp_repo) -> None:
        mock_exec = AsyncMock(return_value=_fail_exec_result("always fails"))

        with patch(
            "execution.cli_agent.CliAgentBackend.execute_phase", mock_exec,
        ):
            result = await engine.execute(
                "Fix bug",
                str(tmp_repo),
                config=YoloConfig(max_retries=1, pause_on_failure=False),
            )

        assert not result.success
        assert result.failed_phases >= 1

    @pytest.mark.asyncio
    async def test_pause_on_failure(self, engine: YoloEngine, tmp_repo) -> None:
        mock_exec = AsyncMock(return_value=_fail_exec_result("oops"))

        with patch(
            "execution.cli_agent.CliAgentBackend.execute_phase", mock_exec,
        ):
            result = await engine.execute(
                "Fix bug",
                str(tmp_repo),
                config=YoloConfig(max_retries=0, pause_on_failure=True),
            )

        assert not result.success
        status = result.agent_status
        assert status

    @pytest.mark.asyncio
    async def test_status_tracker_integration(self, engine: YoloEngine, tmp_repo) -> None:
        tracker = StatusTracker()
        events_seen: list[str] = []
        tracker.on_status(lambda ev: events_seen.append(ev.step))

        mock_exec = AsyncMock(return_value=_ok_exec_result())
        with patch(
            "execution.cli_agent.CliAgentBackend.execute_phase", mock_exec,
        ):
            result = await engine.execute(
                "Fix typo",
                str(tmp_repo),
                status=tracker,
            )

        assert result.success
        assert len(events_seen) > 0
        assert any("YOLO" in e or "Decompos" in e or "Audit" in e for e in events_seen)


class TestYoloConfig:
    @pytest.mark.asyncio
    async def test_skip_planning_flag(self, engine: YoloEngine, tmp_repo) -> None:
        mock_exec = AsyncMock(return_value=_ok_exec_result())

        with patch(
            "execution.cli_agent.CliAgentBackend.execute_phase", mock_exec,
        ):
            result = await engine.execute(
                "Fix typo",
                str(tmp_repo),
                config=YoloConfig(skip_planning=True),
            )

        assert result.success

    @pytest.mark.asyncio
    async def test_dry_run_config(self, engine: YoloEngine, tmp_repo) -> None:
        mock_exec = AsyncMock(return_value=_ok_exec_result())

        with patch(
            "execution.cli_agent.CliAgentBackend.execute_phase", mock_exec,
        ):
            result = await engine.execute(
                "Fix typo",
                str(tmp_repo),
                config=YoloConfig(dry_run=True),
            )

        assert result.success


class TestYoloResult:
    @pytest.mark.asyncio
    async def test_result_structure(self, engine: YoloEngine, tmp_repo) -> None:
        mock_exec = AsyncMock(return_value=_ok_exec_result())

        with patch(
            "execution.cli_agent.CliAgentBackend.execute_phase", mock_exec,
        ):
            result = await engine.execute("Fix typo", str(tmp_repo))

        assert result.plan_id
        assert result.goal == "Fix typo"
        assert result.repo_path == str(tmp_repo.resolve())
        assert result.phase_results
        assert result.agent_status

    @pytest.mark.asyncio
    async def test_phase_results_have_correct_fields(self, engine: YoloEngine, tmp_repo) -> None:
        mock_exec = AsyncMock(return_value=_ok_exec_result())

        with patch(
            "execution.cli_agent.CliAgentBackend.execute_phase", mock_exec,
        ):
            result = await engine.execute("Fix typo", str(tmp_repo))

        for pr in result.phase_results:
            assert pr.phase_id
            assert pr.title
            assert pr.status in YoloPhaseStatus
            assert pr.attempts >= 0
