"""Tests for the YOLO task decomposition planner."""

from __future__ import annotations

import pytest

from orchestrator.planner import TaskPlanner
from orchestrator.schemas import (
    ComplexityBucket,
    ContextPacket,
    TaskClassification,
    TaskType,
    YoloPhaseStatus,
)


@pytest.fixture()
def planner() -> TaskPlanner:
    return TaskPlanner()


@pytest.fixture()
def simple_classification() -> TaskClassification:
    return TaskClassification(
        task_type=TaskType.bugfix,
        complexity_score=0.1,
        files_likely_touched=1,
        ambiguity_score=0.0,
        architectural_impact=0.0,
        failure_severity=0.0,
        estimated_tokens=500,
    )


@pytest.fixture()
def complex_classification() -> TaskClassification:
    return TaskClassification(
        task_type=TaskType.implementation,
        complexity_score=0.65,
        files_likely_touched=12,
        ambiguity_score=0.3,
        architectural_impact=0.7,
        failure_severity=0.2,
        estimated_tokens=5000,
    )


class TestComplexityAnalysis:
    def test_trivial_task(self, planner: TaskPlanner, simple_classification: TaskClassification) -> None:
        analysis = planner.analyze_complexity("Fix typo", classification=simple_classification)
        assert analysis.bucket == ComplexityBucket.trivial
        assert analysis.recommended_phases == 1

    def test_complex_task(self, planner: TaskPlanner, complex_classification: TaskClassification) -> None:
        analysis = planner.analyze_complexity(
            "Redesign the auth module and then add JWT support",
            classification=complex_classification,
        )
        assert analysis.bucket in (ComplexityBucket.high, ComplexityBucket.epic)
        assert analysis.recommended_phases >= 3

    def test_medium_task(self, planner: TaskPlanner) -> None:
        medium = TaskClassification(
            task_type=TaskType.implementation,
            complexity_score=0.4,
            files_likely_touched=5,
            ambiguity_score=0.1,
            architectural_impact=0.2,
            failure_severity=0.1,
            estimated_tokens=2000,
        )
        analysis = planner.analyze_complexity("Add logging to the API", classification=medium)
        assert analysis.bucket == ComplexityBucket.medium
        assert 2 <= analysis.recommended_phases <= 4

    def test_multi_concern_detection(self, planner: TaskPlanner) -> None:
        goal = "First fix the auth bug, then add rate limiting, also update the docs, and finally write tests"
        analysis = planner.analyze_complexity(goal)
        assert analysis.recommended_phases >= 3

    def test_raw_score_bounds(self, planner: TaskPlanner) -> None:
        analysis = planner.analyze_complexity("x")
        assert 0.0 <= analysis.raw_score <= 1.0


class TestDecomposition:
    def test_single_phase_for_trivial(
        self, planner: TaskPlanner, simple_classification: TaskClassification,
    ) -> None:
        plan = planner.decompose("Fix typo", "/repo", classification=simple_classification)
        assert len(plan.phases) == 1
        assert plan.phases[0].title == "Execute"
        assert plan.phases[0].status == YoloPhaseStatus.pending

    def test_multi_phase_for_complex(
        self, planner: TaskPlanner, complex_classification: TaskClassification,
    ) -> None:
        plan = planner.decompose(
            "Redesign the auth module and add JWT support",
            "/repo",
            classification=complex_classification,
        )
        assert len(plan.phases) >= 2
        assert all(p.objective for p in plan.phases)
        assert all(p.acceptance_criteria for p in plan.phases)

    def test_phases_have_sequential_indices(self, planner: TaskPlanner) -> None:
        plan = planner.decompose(
            "Refactor database layer and then add caching and also write tests",
            "/repo",
        )
        for i, phase in enumerate(plan.phases):
            assert phase.index == i

    def test_phases_have_dependencies(self, planner: TaskPlanner) -> None:
        plan = planner.decompose(
            "First design the API, then implement it, and finally test it",
            "/repo",
        )
        if len(plan.phases) > 1:
            for i in range(1, len(plan.phases)):
                assert plan.phases[i].depends_on, f"Phase {i} should have dependencies"

    def test_plan_metadata(
        self, planner: TaskPlanner, simple_classification: TaskClassification,
    ) -> None:
        plan = planner.decompose("Fix bug", "/repo", classification=simple_classification)
        assert plan.goal == "Fix bug"
        assert plan.repo_path == "/repo"
        assert plan.id
        assert plan.complexity.bucket is not None
        assert plan.created_at > 0

    def test_design_phase_for_high_arch_impact(self, planner: TaskPlanner) -> None:
        classification = TaskClassification(
            task_type=TaskType.implementation,
            complexity_score=0.7,
            files_likely_touched=15,
            ambiguity_score=0.2,
            architectural_impact=0.8,
            failure_severity=0.1,
            estimated_tokens=4000,
        )
        plan = planner.decompose(
            "Architect a new plugin system for the app",
            "/repo",
            classification=classification,
        )
        design_phases = [p for p in plan.phases if "Design" in p.title or "Planning" in p.title]
        assert len(design_phases) >= 1

    def test_test_phase_when_tests_mentioned(self, planner: TaskPlanner) -> None:
        classification = TaskClassification(
            task_type=TaskType.implementation,
            complexity_score=0.5,
            files_likely_touched=8,
            ambiguity_score=0.1,
            architectural_impact=0.3,
            failure_severity=0.1,
            estimated_tokens=3000,
        )
        plan = planner.decompose(
            "Add user registration and write tests for it",
            "/repo",
            classification=classification,
        )
        test_phases = [p for p in plan.phases if p.task_type == TaskType.testing]
        assert len(test_phases) >= 1

    def test_context_scoping(self, planner: TaskPlanner) -> None:
        context = ContextPacket(
            task_summary="Update auth module",
            relevant_files={"src/auth.py": "...", "src/models.py": "..."},
            architecture_summary="Python app",
            build_test_commands=["pytest"],
        )
        plan = planner.decompose(
            "Fix auth bug and then update models",
            "/repo",
            context=context,
        )
        assert plan.phases
