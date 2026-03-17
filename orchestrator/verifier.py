"""Deterministic diff-vs-plan verification engine.

Compares actual git diff output against a plan's expected file scope,
objectives, and acceptance criteria. Produces categorized review findings
(CRITICAL / MAJOR / MINOR / INFO) without requiring LLM calls.

This module handles two verification surfaces:
    1. Spec-level   — ``SpecVerifier`` checks a ``GeneratedSpec`` from the
       LLM spec generator against the working-tree diff.
    2. Phase-level   — ``PlanVerifier`` checks a ``YoloPlan`` / ``YoloPhase``
       against the diff produced during phase execution. This is what the
       ``PhaseExecutor`` calls during the YOLO loop.
"""

from __future__ import annotations

import subprocess
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from orchestrator.logging_setup import get_logger

logger = get_logger("verifier")


# ---------------------------------------------------------------------------
# Review comment model
# ---------------------------------------------------------------------------

class Severity(StrEnum):
    critical = "CRITICAL"
    major = "MAJOR"
    minor = "MINOR"
    info = "INFO"


class ReviewComment(BaseModel):
    """A single categorized finding from verification."""
    severity: Severity
    category: str
    message: str
    file: str = ""
    suggestion: str = ""

    def one_line(self) -> str:
        loc = f" ({self.file})" if self.file else ""
        return f"[{self.severity.value}] {self.category}{loc}: {self.message}"


class VerificationReport(BaseModel):
    """Aggregate verification result."""
    spec_id: str = ""
    plan_id: str = ""
    phase_index: int = -1
    passed: bool = True
    score: float = 1.0
    comments: list[ReviewComment] = Field(default_factory=list)
    changed_files: list[str] = Field(default_factory=list)
    expected_files: list[str] = Field(default_factory=list)
    diff_summary: str = ""
    verification_time_seconds: float = 0.0

    @property
    def critical_count(self) -> int:
        return sum(1 for c in self.comments if c.severity == Severity.critical)

    @property
    def major_count(self) -> int:
        return sum(1 for c in self.comments if c.severity == Severity.major)

    def to_markdown(self) -> str:
        ident = self.spec_id or self.plan_id or "unknown"
        verdict = "PASSED" if self.passed else "FAILED"
        lines = [
            f"# Verification Report: {ident}",
            "",
            f"**Verdict:** {verdict}  ",
            f"**Score:** {self.score:.0%}  ",
            f"**Changed files:** {len(self.changed_files)}  ",
            f"**Expected files:** {len(self.expected_files)}  ",
            "",
        ]

        if self.comments:
            lines.append("## Findings")
            lines.append("")
            for c in self.comments:
                loc = f" `{c.file}`" if c.file else ""
                lines.append(f"- **{c.severity.value}** [{c.category}]{loc}: {c.message}")
                if c.suggestion:
                    lines.append(f"  - Suggestion: {c.suggestion}")
            lines.append("")

        if self.diff_summary:
            lines.append("## Diff Summary")
            lines.append(self.diff_summary)

        return "\n".join(lines)

    def to_findings_list(self) -> list[dict]:
        """Return findings as plain dicts for status.json persistence."""
        return [c.model_dump() for c in self.comments]


# ---------------------------------------------------------------------------
# Git diff helpers
# ---------------------------------------------------------------------------

def _git_changed_files(repo_path: str | Path, ref: str = "HEAD") -> list[str]:
    """Get list of files changed relative to ``ref`` (default: last commit)."""
    root = Path(repo_path).resolve()
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", ref],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            staged = subprocess.run(
                ["git", "diff", "--name-only", "--cached"],
                cwd=str(root),
                capture_output=True,
                text=True,
                timeout=30,
            )
            files = staged.stdout.strip().splitlines() if staged.returncode == 0 else []
        else:
            files = result.stdout.strip().splitlines()

        untracked = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if untracked.returncode == 0:
            files.extend(untracked.stdout.strip().splitlines())

        return sorted(set(f for f in files if f))
    except Exception as exc:
        logger.warning("git diff failed: %s", exc)
        return []


def _git_diff_stat(repo_path: str | Path, ref: str = "HEAD") -> str:
    """Get ``git diff --stat`` output."""
    root = Path(repo_path).resolve()
    try:
        result = subprocess.run(
            ["git", "diff", "--stat", ref],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Phase-level verifier (used by PhaseExecutor in the YOLO loop)
# ---------------------------------------------------------------------------

class PlanVerifier:
    """Deterministic diff-vs-plan verification for a single phase.

    Called by ``PhaseExecutor._verify()`` to compare actual repo changes
    against the phase's ``files_in_scope``, ``objective``, and
    ``acceptance_criteria``.
    """

    def verify_phase(
        self,
        phase_index: int,
        phase_title: str,
        plan_id: str,
        expected_files: list[str],
        acceptance_criteria: list[str],
        repo_path: str | Path,
        *,
        git_ref: str = "HEAD",
    ) -> VerificationReport:
        start = time.monotonic()
        changed = _git_changed_files(repo_path, ref=git_ref)
        diff_stat = _git_diff_stat(repo_path, ref=git_ref)
        comments: list[ReviewComment] = []

        # -- Check 1: Were expected files touched? --
        if expected_files:
            expected_set = set(expected_files)
            changed_set = set(changed)
            missing = expected_set - changed_set
            extra = changed_set - expected_set

            for f in missing:
                comments.append(ReviewComment(
                    severity=Severity.major,
                    category="scope_miss",
                    message="Expected file was not modified",
                    file=f,
                    suggestion="Verify this file still needs changes per the plan",
                ))

            for f in extra:
                comments.append(ReviewComment(
                    severity=Severity.minor,
                    category="scope_extra",
                    message="File changed outside planned scope",
                    file=f,
                    suggestion="Confirm this change is intentional",
                ))

        # -- Check 2: Were any files changed at all? --
        if not changed:
            comments.append(ReviewComment(
                severity=Severity.critical,
                category="no_changes",
                message="No files were changed during this phase",
                suggestion="The agent may have failed silently",
            ))

        # -- Check 3: Acceptance criteria heuristics --
        for criterion in acceptance_criteria:
            cl = criterion.lower()
            if "test" in cl and not any("test" in f.lower() for f in changed):
                comments.append(ReviewComment(
                    severity=Severity.major,
                    category="criteria_unmet",
                    message=f"Criterion may be unmet: '{criterion}'",
                    suggestion="No test files were modified",
                ))
            if "build succeeds" in cl or "no new build errors" in cl:
                pass

        # -- Score --
        critical = sum(1 for c in comments if c.severity == Severity.critical)
        major = sum(1 for c in comments if c.severity == Severity.major)
        deductions = critical * 0.4 + major * 0.15
        score = max(0.0, 1.0 - deductions)
        passed = critical == 0 and score >= 0.5

        duration = time.monotonic() - start
        return VerificationReport(
            plan_id=plan_id,
            phase_index=phase_index,
            passed=passed,
            score=round(score, 2),
            comments=comments,
            changed_files=changed,
            expected_files=expected_files,
            diff_summary=diff_stat,
            verification_time_seconds=round(duration, 3),
        )


# ---------------------------------------------------------------------------
# Spec-level verifier (used by the ``clawsmith verify`` CLI command)
# ---------------------------------------------------------------------------

class SpecVerifier:
    """Verifies a ``GeneratedSpec`` against the current working tree diff.

    This is the implementation behind ``clawsmith verify --spec-id <id>``.
    """

    def __init__(self, **kwargs: Any) -> None:
        self._extra = kwargs

    async def verify(
        self,
        spec: Any,
        repo_path: str,
        *,
        git_ref: str = "HEAD",
    ) -> VerificationReport:
        start = time.monotonic()
        root = Path(repo_path).resolve()
        changed = _git_changed_files(root, ref=git_ref)
        diff_stat = _git_diff_stat(root, ref=git_ref)
        comments: list[ReviewComment] = []

        expected = self._extract_expected_files(spec)
        expected_set = set(expected)
        changed_set = set(changed)

        for f in expected_set - changed_set:
            comments.append(ReviewComment(
                severity=Severity.major,
                category="spec_miss",
                message="Spec expected changes to this file",
                file=f,
                suggestion="Implement the planned changes or update the spec",
            ))

        for f in changed_set - expected_set:
            comments.append(ReviewComment(
                severity=Severity.minor,
                category="spec_extra",
                message="File changed but not in spec",
                file=f,
                suggestion="Confirm this is intentional",
            ))

        if not changed:
            comments.append(ReviewComment(
                severity=Severity.critical,
                category="no_changes",
                message="No files changed relative to the spec",
            ))

        # Check spec phases if they exist
        phases = getattr(spec, "phases", []) or []
        for phase in phases:
            for criterion in getattr(phase, "acceptance_criteria", []):
                cl = criterion.lower()
                if "test" in cl and not any("test" in f.lower() for f in changed):
                    comments.append(ReviewComment(
                        severity=Severity.major,
                        category="criteria_unmet",
                        message=f"Phase '{getattr(phase, 'title', '?')}' criterion: '{criterion}'",
                        suggestion="No test files modified",
                    ))

        critical = sum(1 for c in comments if c.severity == Severity.critical)
        major = sum(1 for c in comments if c.severity == Severity.major)
        deductions = critical * 0.4 + major * 0.15
        score = max(0.0, 1.0 - deductions)
        passed = critical == 0 and score >= 0.5

        return VerificationReport(
            spec_id=getattr(spec, "id", ""),
            passed=passed,
            score=round(score, 2),
            comments=comments,
            changed_files=changed,
            expected_files=expected,
            diff_summary=diff_stat,
            verification_time_seconds=round(time.monotonic() - start, 3),
        )

    async def verify_and_save(
        self,
        spec: Any,
        repo_path: str,
        **kwargs: Any,
    ) -> tuple[VerificationReport, Path]:
        report = await self.verify(spec, repo_path, **kwargs)

        save_dir = Path(repo_path).resolve() / ".clawsmith" / "verifications"
        save_dir.mkdir(parents=True, exist_ok=True)

        ts = time.strftime("%Y%m%d_%H%M%S")
        report_path = save_dir / f"verify_{report.spec_id}_{ts}.md"
        report_path.write_text(report.to_markdown(), encoding="utf-8")

        json_path = save_dir / f"verify_{report.spec_id}_{ts}.json"
        json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")

        logger.info("Verification report saved to %s", report_path)
        return report, report_path

    @staticmethod
    def _extract_expected_files(spec: Any) -> list[str]:
        """Pull expected file paths from a GeneratedSpec."""
        files: list[str] = []
        for fc in getattr(spec, "file_changes", []):
            path = getattr(fc, "path", "")
            if path:
                files.append(path)
        for phase in getattr(spec, "phases", []) or []:
            for fc in getattr(phase, "file_changes", []):
                path = getattr(fc, "path", "")
                if path and path not in files:
                    files.append(path)
        return files
