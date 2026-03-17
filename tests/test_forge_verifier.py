"""Tests for the verifier and forge modules."""

from __future__ import annotations

import json
import pytest

from orchestrator.verifier import (
    ReviewComment,
    Severity,
    SpecVerifier,
    VerificationResult,
)
from orchestrator.spec_generator import (
    FileChange,
    GeneratedSpec,
    SpecTier,
)
from orchestrator.forge import ForgeEngine, ForgeMode, ForgeResult


class TestVerificationResult:
    def test_critical_count(self):
        result = VerificationResult(
            spec_id="abc",
            goal="test",
            passed=False,
            score=0.3,
            comments=[
                ReviewComment(severity=Severity.critical, message="Missing auth"),
                ReviewComment(severity=Severity.major, message="No tests"),
                ReviewComment(severity=Severity.critical, message="SQL injection"),
            ],
        )
        assert result.critical_count == 2
        assert result.major_count == 1

    def test_to_markdown(self):
        result = VerificationResult(
            spec_id="abc",
            goal="Add auth",
            passed=True,
            score=0.95,
            summary="Good implementation",
            comments=[
                ReviewComment(
                    severity=Severity.minor,
                    file_path="auth.py",
                    message="Could use type hints",
                ),
            ],
        )
        md = result.to_markdown()
        assert "PASSED" in md
        assert "auth.py" in md
        assert "Could use type hints" in md


class TestSpecVerifier:
    def test_extract_diff_files(self):
        diff = (
            "diff --git a/src/auth.py b/src/auth.py\n"
            "--- a/src/auth.py\n"
            "+++ b/src/auth.py\n"
            "@@ -1,3 +1,5 @@\n"
            "+import jwt\n"
            "diff --git a/src/new.py b/src/new.py\n"
            "--- /dev/null\n"
            "+++ b/src/new.py\n"
        )
        files = SpecVerifier._extract_diff_files(diff)
        assert "src/auth.py" in files
        assert "src/new.py" in files

    def test_all_spec_files_flat(self):
        spec = GeneratedSpec(
            goal="test",
            tier=SpecTier.quick,
            file_changes=[
                FileChange(path="a.py", action="create", description="A"),
                FileChange(path="b.py", action="modify", description="B"),
            ],
        )
        files = SpecVerifier._all_spec_files(spec)
        assert files == ["a.py", "b.py"]

    def test_all_spec_files_deduped(self):
        from orchestrator.spec_generator import SpecPhase
        spec = GeneratedSpec(
            goal="test",
            tier=SpecTier.epic,
            file_changes=[
                FileChange(path="shared.py", action="modify", description="Shared"),
            ],
            phases=[
                SpecPhase(
                    index=0, title="P1", objective="Phase 1",
                    file_changes=[
                        FileChange(path="shared.py", action="modify", description="Shared"),
                        FileChange(path="new.py", action="create", description="New"),
                    ],
                ),
            ],
        )
        files = SpecVerifier._all_spec_files(spec)
        assert files == ["shared.py", "new.py"]

    def test_parse_response_valid(self):
        verifier = SpecVerifier()
        spec = GeneratedSpec(
            goal="test", tier=SpecTier.quick,
            file_changes=[FileChange(path="a.py", action="create", description="A")],
        )
        raw = json.dumps({
            "score": 0.85,
            "summary": "Good implementation",
            "comments": [
                {"severity": "MINOR", "file_path": "a.py", "message": "Missing docstring"},
            ],
        })
        result = verifier._parse_response(
            raw, spec,
            expected={"a.py"},
            actual={"a.py"},
            missing=set(),
            unplanned=set(),
        )
        assert result.score == 0.85
        assert result.passed is True
        assert len(result.comments) == 1

    def test_parse_response_fails_on_critical(self):
        verifier = SpecVerifier()
        spec = GeneratedSpec(goal="test", tier=SpecTier.quick)
        raw = json.dumps({
            "score": 0.7,
            "summary": "Has issues",
            "comments": [
                {"severity": "CRITICAL", "message": "Security flaw"},
            ],
        })
        result = verifier._parse_response(
            raw, spec,
            expected=set(), actual=set(),
            missing=set(), unplanned=set(),
        )
        assert result.passed is False

    def test_parse_response_fallback(self):
        verifier = SpecVerifier()
        spec = GeneratedSpec(goal="test", tier=SpecTier.quick)
        result = verifier._parse_response(
            "not json",
            spec,
            expected={"a.py", "b.py"},
            actual={"a.py"},
            missing={"b.py"},
            unplanned=set(),
        )
        assert result.score == 0.5  # 1/2 files
        assert result.passed is False


class TestForgeResult:
    def test_summary(self):
        result = ForgeResult()
        result.goal = "Add auth"
        result.success = True
        result.duration_seconds = 45.2
        s = result.summary()
        assert s["goal"] == "Add auth"
        assert s["overall_success"] is True
        assert s["duration_seconds"] == 45.2

    def test_final_verification(self):
        result = ForgeResult()
        v1 = VerificationResult(spec_id="a", goal="test", passed=False, score=0.4)
        v2 = VerificationResult(spec_id="a", goal="test", passed=True, score=0.9)
        result.verification_results = [v1, v2]
        assert result.final_verification == v2
        assert result.final_verification.passed is True


class TestGitOps:
    def test_slugify(self):
        from orchestrator.git_ops import slugify_goal
        assert slugify_goal("Add JWT auth to the API") == "add-jwt-auth-to-the-api"
        assert slugify_goal("Fix bug #123!") == "fix-bug-123"
        assert len(slugify_goal("A" * 100)) <= 50
