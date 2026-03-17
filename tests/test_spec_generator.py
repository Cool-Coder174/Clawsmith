"""Tests for the spec generator module."""

from __future__ import annotations

import json
import pytest

from orchestrator.spec_generator import (
    FileChange,
    GeneratedSpec,
    SpecGenerator,
    SpecPhase,
    SpecTier,
)
from orchestrator.schemas import (
    ContextPacket,
    TaskClassification,
    TaskType,
)


class TestFileChange:
    def test_basic_creation(self):
        fc = FileChange(path="src/auth.py", action="modify", description="Add JWT validation")
        assert fc.path == "src/auth.py"
        assert fc.action == "modify"
        assert fc.key_changes == []

    def test_with_details(self):
        fc = FileChange(
            path="src/auth.py",
            action="create",
            description="New auth module",
            key_changes=["Add login function", "Add token refresh"],
            dependencies=["src/config.py"],
        )
        assert len(fc.key_changes) == 2
        assert "src/config.py" in fc.dependencies


class TestGeneratedSpec:
    def test_to_markdown_minimal(self):
        spec = GeneratedSpec(goal="Add tests", tier=SpecTier.quick, summary="Add unit tests")
        md = spec.to_markdown()
        assert "# Spec: Add tests" in md
        assert "Add unit tests" in md

    def test_to_markdown_with_files(self):
        spec = GeneratedSpec(
            goal="Add auth",
            tier=SpecTier.full,
            file_changes=[
                FileChange(path="auth.py", action="create", description="Auth module"),
            ],
        )
        md = spec.to_markdown()
        assert "`auth.py`" in md
        assert "create" in md

    def test_to_markdown_with_phases(self):
        spec = GeneratedSpec(
            goal="Big feature",
            tier=SpecTier.epic,
            phases=[
                SpecPhase(
                    index=0,
                    title="Foundation",
                    objective="Set up base",
                    acceptance_criteria=["Base works"],
                ),
                SpecPhase(
                    index=1,
                    title="Implementation",
                    objective="Build the thing",
                ),
            ],
        )
        md = spec.to_markdown()
        assert "Phase 1: Foundation" in md
        assert "Phase 2: Implementation" in md

    def test_to_yolo_plan_single_phase(self):
        spec = GeneratedSpec(
            goal="Simple fix",
            tier=SpecTier.quick,
            file_changes=[
                FileChange(path="fix.py", action="modify", description="Fix bug"),
            ],
        )
        plan = spec.to_yolo_plan("/repo")
        assert plan.goal == "Simple fix"
        assert plan.repo_path == "/repo"
        assert len(plan.phases) == 1
        assert "fix.py" in plan.phases[0].files_in_scope

    def test_to_yolo_plan_multi_phase(self):
        spec = GeneratedSpec(
            goal="Complex feature",
            tier=SpecTier.epic,
            phases=[
                SpecPhase(
                    index=0, title="Phase 1", objective="Setup",
                    file_changes=[FileChange(path="a.py", action="create", description="Create A")],
                ),
                SpecPhase(
                    index=1, title="Phase 2", objective="Build",
                    file_changes=[FileChange(path="b.py", action="create", description="Create B")],
                ),
            ],
        )
        plan = spec.to_yolo_plan("/repo")
        assert len(plan.phases) == 2
        assert plan.phases[0].title == "Phase 1"
        assert plan.phases[1].depends_on == [plan.phases[0].id]


class TestSpecGenerator:
    def test_auto_tier_quick(self):
        gen = SpecGenerator()
        cls = TaskClassification(
            task_type=TaskType.bugfix,
            complexity_score=0.15,
            files_likely_touched=1,
            ambiguity_score=0.1,
            architectural_impact=0.0,
            failure_severity=0.3,
            estimated_tokens=500,
        )
        assert gen._auto_tier(cls) == SpecTier.quick

    def test_auto_tier_full(self):
        gen = SpecGenerator()
        cls = TaskClassification(
            task_type=TaskType.implementation,
            complexity_score=0.5,
            files_likely_touched=5,
            ambiguity_score=0.3,
            architectural_impact=0.3,
            failure_severity=0.5,
            estimated_tokens=2000,
        )
        assert gen._auto_tier(cls) == SpecTier.full

    def test_auto_tier_epic(self):
        gen = SpecGenerator()
        cls = TaskClassification(
            task_type=TaskType.implementation,
            complexity_score=0.85,
            files_likely_touched=15,
            ambiguity_score=0.6,
            architectural_impact=0.8,
            failure_severity=0.7,
            estimated_tokens=5000,
        )
        assert gen._auto_tier(cls) == SpecTier.epic

    def test_auto_tier_none(self):
        gen = SpecGenerator()
        assert gen._auto_tier(None) == SpecTier.full

    def test_extract_json_direct(self):
        data = '{"summary": "test", "file_changes": []}'
        result = SpecGenerator._extract_json(data)
        assert result is not None
        assert result["summary"] == "test"

    def test_extract_json_fenced(self):
        data = 'Here is the spec:\n```json\n{"summary": "test"}\n```\nDone.'
        result = SpecGenerator._extract_json(data)
        assert result is not None
        assert result["summary"] == "test"

    def test_extract_json_invalid(self):
        result = SpecGenerator._extract_json("this is not json at all")
        assert result is None

    def test_parse_response_valid(self):
        gen = SpecGenerator()
        raw = json.dumps({
            "summary": "Add auth module",
            "file_changes": [
                {"path": "auth.py", "action": "create", "description": "Auth"},
            ],
            "risks": ["Token expiry edge case"],
            "open_questions": [],
        })
        spec = gen._parse_response(raw, "Add auth", SpecTier.quick)
        assert spec.summary == "Add auth module"
        assert len(spec.file_changes) == 1
        assert spec.file_changes[0].path == "auth.py"

    def test_parse_response_malformed(self):
        gen = SpecGenerator()
        spec = gen._parse_response("not json", "Fix bug", SpecTier.quick)
        assert spec.goal == "Fix bug"
        assert spec.raw_llm_output == "not json"
