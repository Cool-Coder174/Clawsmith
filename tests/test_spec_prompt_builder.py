"""Tests for the spec-aware prompt builder."""

from __future__ import annotations

import pytest

from execution.spec_prompt_builder import SpecPromptBuilder
from orchestrator.spec_generator import (
    FileChange,
    GeneratedSpec,
    SpecPhase,
    SpecTier,
)
from orchestrator.schemas import (
    TaskType,
    YoloPhase,
    YoloPlan,
    ComplexityAnalysis,
    ComplexityBucket,
)


def _make_plan(goal: str = "Test goal", phases: int = 2) -> YoloPlan:
    return YoloPlan(
        goal=goal,
        repo_path="/test",
        complexity=ComplexityAnalysis(
            bucket=ComplexityBucket.medium,
            raw_score=0.5,
            recommended_phases=phases,
        ),
        phases=[
            YoloPhase(
                index=i,
                title=f"Phase {i + 1}",
                objective=f"Do phase {i + 1}",
                acceptance_criteria=[f"Phase {i + 1} works"],
            )
            for i in range(phases)
        ],
    )


class TestSpecPromptBuilder:
    def test_falls_back_without_spec(self):
        builder = SpecPromptBuilder(spec=None)
        plan = _make_plan()
        prompt = builder.build(plan.phases[0], plan)
        assert "Phase 1 of 2" in prompt
        assert "Implementation Details" not in prompt

    def test_includes_spec_file_changes(self):
        spec = GeneratedSpec(
            goal="Add auth",
            tier=SpecTier.full,
            phases=[
                SpecPhase(
                    index=0,
                    title="Foundation",
                    objective="Setup",
                    file_changes=[
                        FileChange(
                            path="auth.py",
                            action="create",
                            description="Auth module with JWT",
                            key_changes=["Add login endpoint", "Add token refresh"],
                        ),
                    ],
                ),
                SpecPhase(
                    index=1,
                    title="Tests",
                    objective="Add tests",
                    file_changes=[],
                ),
            ],
        )

        builder = SpecPromptBuilder(spec=spec)
        plan = _make_plan()
        prompt = builder.build(plan.phases[0], plan)

        assert "Implementation Details" in prompt
        assert "`auth.py`" in prompt
        assert "CREATE" in prompt
        assert "Add login endpoint" in prompt

    def test_includes_previous_phase_context(self):
        spec = GeneratedSpec(
            goal="Multi-step",
            tier=SpecTier.epic,
            phases=[
                SpecPhase(index=0, title="P1", objective="Setup"),
                SpecPhase(index=1, title="P2", objective="Build"),
            ],
        )

        builder = SpecPromptBuilder(spec=spec)
        builder.record_phase_result(0, "Created the base auth module with JWT support")

        plan = _make_plan()
        prompt = builder.build(plan.phases[1], plan)

        assert "Previous Phase Context" in prompt
        assert "Created the base auth module" in prompt
        assert "Do not redo" in prompt

    def test_includes_rollback_notes(self):
        spec = GeneratedSpec(
            goal="Risky change",
            tier=SpecTier.epic,
            phases=[
                SpecPhase(
                    index=0,
                    title="Migration",
                    objective="Migrate DB",
                    rollback_notes="Run rollback_migration.sql to revert",
                ),
            ],
        )

        builder = SpecPromptBuilder(spec=spec)
        plan = _make_plan(phases=1)
        prompt = builder.build(plan.phases[0], plan)

        assert "Rollback Notes" in prompt
        assert "rollback_migration.sql" in prompt

    def test_merges_acceptance_criteria(self):
        spec = GeneratedSpec(
            goal="Test",
            tier=SpecTier.full,
            phases=[
                SpecPhase(
                    index=0,
                    title="Build",
                    objective="Build it",
                    acceptance_criteria=["API returns 200", "Tests pass"],
                ),
            ],
        )

        builder = SpecPromptBuilder(spec=spec)
        plan = _make_plan(phases=1)
        plan.phases[0].acceptance_criteria = ["Phase 1 works"]

        prompt = builder.build(plan.phases[0], plan)

        assert "Phase 1 works" in prompt
        assert "API returns 200" in prompt
        assert "Tests pass" in prompt

    def test_file_dependencies(self):
        spec = GeneratedSpec(
            goal="Linked files",
            tier=SpecTier.full,
            phases=[
                SpecPhase(
                    index=0,
                    title="Build",
                    objective="Build",
                    file_changes=[
                        FileChange(
                            path="routes.py",
                            action="modify",
                            description="Add auth routes",
                            dependencies=["auth.py", "config.py"],
                        ),
                    ],
                ),
            ],
        )

        builder = SpecPromptBuilder(spec=spec)
        plan = _make_plan(phases=1)
        prompt = builder.build(plan.phases[0], plan)

        assert "File Dependencies" in prompt
        assert "`auth.py`" in prompt
        assert "`config.py`" in prompt
