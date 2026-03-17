"""Tests for the deterministic diff-vs-plan verifier."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from orchestrator.verifier import (
    PlanVerifier,
    ReviewComment,
    Severity,
    SpecVerifier,
    VerificationReport,
)


@pytest.fixture()
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with an initial commit."""
    subprocess.run(
        ["git", "init"], cwd=str(tmp_path),
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(tmp_path), capture_output=True, check=True,
    )
    (tmp_path / "README.md").write_text("# Test", encoding="utf-8")
    subprocess.run(
        ["git", "add", "."], cwd=str(tmp_path),
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=str(tmp_path),
        capture_output=True, check=True,
    )
    return tmp_path


class TestReviewComment:
    def test_one_line_with_file(self) -> None:
        c = ReviewComment(
            severity=Severity.major,
            category="scope_miss",
            message="Expected file was not modified",
            file="src/auth.py",
        )
        line = c.one_line()
        assert "MAJOR" in line
        assert "src/auth.py" in line
        assert "scope_miss" in line

    def test_one_line_without_file(self) -> None:
        c = ReviewComment(
            severity=Severity.critical,
            category="no_changes",
            message="No files changed",
        )
        assert "CRITICAL" in c.one_line()
        assert "no_changes" in c.one_line()


class TestVerificationReport:
    def test_defaults_to_passed(self) -> None:
        report = VerificationReport()
        assert report.passed is True
        assert report.score == 1.0
        assert report.critical_count == 0
        assert report.major_count == 0

    def test_counts_by_severity(self) -> None:
        report = VerificationReport(
            comments=[
                ReviewComment(severity=Severity.critical, category="a", message="x"),
                ReviewComment(severity=Severity.major, category="b", message="y"),
                ReviewComment(severity=Severity.major, category="c", message="z"),
                ReviewComment(severity=Severity.minor, category="d", message="w"),
            ],
        )
        assert report.critical_count == 1
        assert report.major_count == 2

    def test_to_markdown(self) -> None:
        report = VerificationReport(
            spec_id="abc123",
            passed=False,
            score=0.5,
            comments=[
                ReviewComment(
                    severity=Severity.major,
                    category="scope_miss",
                    message="Missing file",
                    file="src/auth.py",
                ),
            ],
        )
        md = report.to_markdown()
        assert "FAILED" in md
        assert "abc123" in md
        assert "scope_miss" in md
        assert "src/auth.py" in md

    def test_to_findings_list(self) -> None:
        report = VerificationReport(
            comments=[
                ReviewComment(
                    severity=Severity.minor,
                    category="extra",
                    message="Unplanned file",
                    file="extra.py",
                ),
            ],
        )
        findings = report.to_findings_list()
        assert len(findings) == 1
        assert findings[0]["severity"] == "MINOR"
        assert findings[0]["file"] == "extra.py"


class TestPlanVerifier:
    def test_no_changes_is_critical(self, git_repo: Path) -> None:
        verifier = PlanVerifier()
        report = verifier.verify_phase(
            phase_index=0,
            phase_title="Implement",
            plan_id="plan1",
            expected_files=["src/auth.py"],
            acceptance_criteria=["Auth works"],
            repo_path=git_repo,
        )
        assert report.critical_count >= 1
        assert not report.passed

    def test_matching_changes_pass(self, git_repo: Path) -> None:
        (git_repo / "src").mkdir(exist_ok=True)
        (git_repo / "src" / "auth.py").write_text("class Auth: pass", encoding="utf-8")

        verifier = PlanVerifier()
        report = verifier.verify_phase(
            phase_index=0,
            phase_title="Implement",
            plan_id="plan1",
            expected_files=["src/auth.py"],
            acceptance_criteria=["Auth class exists"],
            repo_path=git_repo,
        )
        assert report.passed
        assert "src/auth.py" in report.changed_files

    def test_extra_files_are_minor(self, git_repo: Path) -> None:
        (git_repo / "src").mkdir(exist_ok=True)
        (git_repo / "src" / "auth.py").write_text("auth", encoding="utf-8")
        (git_repo / "src" / "extra.py").write_text("extra", encoding="utf-8")

        verifier = PlanVerifier()
        report = verifier.verify_phase(
            phase_index=0,
            phase_title="Implement",
            plan_id="plan1",
            expected_files=["src/auth.py"],
            acceptance_criteria=[],
            repo_path=git_repo,
        )
        minor_extras = [
            c for c in report.comments
            if c.severity == Severity.minor and c.category == "scope_extra"
        ]
        assert len(minor_extras) >= 1

    def test_missing_expected_files_are_major(self, git_repo: Path) -> None:
        (git_repo / "unrelated.py").write_text("x", encoding="utf-8")

        verifier = PlanVerifier()
        report = verifier.verify_phase(
            phase_index=0,
            phase_title="Implement",
            plan_id="plan1",
            expected_files=["src/missing.py"],
            acceptance_criteria=[],
            repo_path=git_repo,
        )
        major_misses = [
            c for c in report.comments
            if c.severity == Severity.major and c.category == "scope_miss"
        ]
        assert len(major_misses) >= 1

    def test_test_criterion_flags_missing_tests(self, git_repo: Path) -> None:
        (git_repo / "src").mkdir(exist_ok=True)
        (git_repo / "src" / "auth.py").write_text("auth", encoding="utf-8")

        verifier = PlanVerifier()
        report = verifier.verify_phase(
            phase_index=0,
            phase_title="Implement",
            plan_id="plan1",
            expected_files=["src/auth.py"],
            acceptance_criteria=["Tests pass"],
            repo_path=git_repo,
        )
        criteria_findings = [
            c for c in report.comments if c.category == "criteria_unmet"
        ]
        assert len(criteria_findings) >= 1

    def test_score_decreases_with_findings(self, git_repo: Path) -> None:
        verifier = PlanVerifier()
        report = verifier.verify_phase(
            phase_index=0,
            phase_title="Implement",
            plan_id="plan1",
            expected_files=["a.py", "b.py", "c.py"],
            acceptance_criteria=[],
            repo_path=git_repo,
        )
        assert report.score < 1.0


class TestSpecVerifier:
    @pytest.mark.asyncio
    async def test_verify_no_changes(self, git_repo: Path) -> None:
        class FakeSpec:
            id = "spec1"
            file_changes = []
            phases = []

        verifier = SpecVerifier()
        report = await verifier.verify(FakeSpec(), str(git_repo))
        assert report.critical_count >= 1
        assert not report.passed

    @pytest.mark.asyncio
    async def test_verify_and_save(self, git_repo: Path) -> None:
        (git_repo / "new_file.py").write_text("x", encoding="utf-8")

        class FakeSpec:
            id = "spec2"
            file_changes = []
            phases = []

        verifier = SpecVerifier()
        report, path = await verifier.verify_and_save(FakeSpec(), str(git_repo))
        assert path.exists()
        assert "spec2" in path.name
