"""Tests for the plan writer — plan artifact persistence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from orchestrator.plan_writer import (
    list_plans,
    load_plan,
    update_status,
    write_plan,
)
from orchestrator.planner import TaskPlanner
from orchestrator.schemas import (
    ComplexityAnalysis,
    ComplexityBucket,
    TaskType,
    YoloPhase,
    YoloPlan,
)


@pytest.fixture()
def sample_plan() -> YoloPlan:
    return YoloPlan(
        id="test123",
        goal="Add user authentication with JWT",
        repo_path="/tmp/repo",
        complexity=ComplexityAnalysis(
            bucket=ComplexityBucket.medium,
            raw_score=0.45,
            recommended_phases=3,
            reasoning="score=0.45; medium complexity",
        ),
        phases=[
            YoloPhase(
                index=0,
                title="Design & Planning",
                objective="Design the auth module structure",
                task_type=TaskType.planning,
                files_in_scope=["src/auth.py", "src/models.py"],
                acceptance_criteria=["Approach is documented", "Files identified"],
                estimated_complexity=0.2,
            ),
            YoloPhase(
                index=1,
                title="Core Implementation",
                objective="Implement JWT auth middleware",
                task_type=TaskType.implementation,
                files_in_scope=["src/auth.py", "src/middleware.py"],
                acceptance_criteria=["JWT tokens issued", "Middleware validates tokens"],
                estimated_complexity=0.6,
            ),
            YoloPhase(
                index=2,
                title="Testing",
                objective="Write tests for auth module",
                task_type=TaskType.testing,
                acceptance_criteria=["Tests pass", "Coverage > 80%"],
                estimated_complexity=0.3,
            ),
        ],
    )


class TestWritePlan:
    def test_creates_plan_directory(self, tmp_path: Path, sample_plan: YoloPlan) -> None:
        plan_dir = write_plan(sample_plan, tmp_path)
        assert plan_dir.exists()
        assert plan_dir.is_dir()

    def test_writes_plan_json(self, tmp_path: Path, sample_plan: YoloPlan) -> None:
        plan_dir = write_plan(sample_plan, tmp_path)
        json_path = plan_dir / "plan.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["goal"] == "Add user authentication with JWT"
        assert len(data["phases"]) == 3

    def test_writes_plan_markdown(self, tmp_path: Path, sample_plan: YoloPlan) -> None:
        plan_dir = write_plan(sample_plan, tmp_path)
        md_path = plan_dir / "plan.md"
        assert md_path.exists()
        content = md_path.read_text(encoding="utf-8")
        assert "# Plan:" in content
        assert "Add user authentication with JWT" in content
        assert "Phase 1:" in content
        assert "Phase 2:" in content
        assert "Phase 3:" in content

    def test_writes_status_json(self, tmp_path: Path, sample_plan: YoloPlan) -> None:
        plan_dir = write_plan(sample_plan, tmp_path)
        status_path = plan_dir / "status.json"
        assert status_path.exists()
        status = json.loads(status_path.read_text(encoding="utf-8"))
        assert status["plan_id"] == "test123"
        assert status["overall_status"] == "planned"
        assert len(status["phases"]) == 3
        assert all(p["status"] == "pending" for p in status["phases"])

    def test_markdown_contains_acceptance_criteria(
        self, tmp_path: Path, sample_plan: YoloPlan,
    ) -> None:
        plan_dir = write_plan(sample_plan, tmp_path)
        content = (plan_dir / "plan.md").read_text(encoding="utf-8")
        assert "JWT tokens issued" in content
        assert "Tests pass" in content

    def test_markdown_contains_files_in_scope(
        self, tmp_path: Path, sample_plan: YoloPlan,
    ) -> None:
        plan_dir = write_plan(sample_plan, tmp_path)
        content = (plan_dir / "plan.md").read_text(encoding="utf-8")
        assert "src/auth.py" in content
        assert "src/middleware.py" in content


class TestLoadPlan:
    def test_roundtrip(self, tmp_path: Path, sample_plan: YoloPlan) -> None:
        write_plan(sample_plan, tmp_path)
        loaded = load_plan("test123", tmp_path)
        assert loaded.id == sample_plan.id
        assert loaded.goal == sample_plan.goal
        assert len(loaded.phases) == len(sample_plan.phases)

    def test_not_found_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_plan("nonexistent", tmp_path)


class TestUpdateStatus:
    def test_update_phase_status(self, tmp_path: Path, sample_plan: YoloPlan) -> None:
        write_plan(sample_plan, tmp_path)
        updated = update_status(
            "test123", tmp_path,
            phase_index=0, phase_status="completed",
        )
        assert updated["phases"][0]["status"] == "completed"
        assert updated["phases"][1]["status"] == "pending"

    def test_add_findings(self, tmp_path: Path, sample_plan: YoloPlan) -> None:
        write_plan(sample_plan, tmp_path)
        findings = [{"severity": "MAJOR", "message": "Missing test file"}]
        updated = update_status("test123", tmp_path, findings=findings)
        assert len(updated["findings"]) == 1
        assert updated["findings"][0]["severity"] == "MAJOR"

    def test_set_run_id(self, tmp_path: Path, sample_plan: YoloPlan) -> None:
        write_plan(sample_plan, tmp_path)
        updated = update_status("test123", tmp_path, run_id="run_abc")
        assert updated["run_id"] == "run_abc"


class TestListPlans:
    def test_empty_when_no_plans(self, tmp_path: Path) -> None:
        assert list_plans(tmp_path) == []

    def test_lists_saved_plans(self, tmp_path: Path, sample_plan: YoloPlan) -> None:
        write_plan(sample_plan, tmp_path)
        plans = list_plans(tmp_path)
        assert len(plans) == 1
        assert plans[0]["id"] == "test123"
        assert plans[0]["goal"] == "Add user authentication with JWT"
        assert plans[0]["phases"] == 3

    def test_lists_multiple_plans(self, tmp_path: Path, sample_plan: YoloPlan) -> None:
        write_plan(sample_plan, tmp_path)
        plan2 = sample_plan.model_copy(update={"id": "test456", "goal": "Another task"})
        write_plan(plan2, tmp_path)
        plans = list_plans(tmp_path)
        assert len(plans) == 2


class TestPlannerIntegration:
    def test_decompose_and_write(self, tmp_path: Path) -> None:
        planner = TaskPlanner()
        plan = planner.decompose(
            "Add logging to the API and write tests",
            str(tmp_path),
        )
        write_plan(plan, tmp_path)
        loaded = load_plan(plan.id, tmp_path)
        assert loaded.goal == plan.goal
        assert len(loaded.phases) == len(plan.phases)
