"""Semantic verification engine — checks implementation against spec.

Unlike the existing pipeline verifier (exit code + build errors), this
compares actual git diffs against the generated spec to catch:
- Missing file changes (spec says create X, but X doesn't exist)
- Drift (implementation diverges from spec intent)
- Incomplete work (partial implementation)
- Unplanned changes (files modified that aren't in the spec)

Uses local Ollama models for zero-cost verification.

Produces categorized review comments:
    CRITICAL — blocks merge, must fix
    MAJOR    — significant issue, should fix
    MINOR    — style/improvement suggestion
    INFO     — observation, no action needed
"""

from __future__ import annotations

import subprocess
import time
from enum import StrEnum
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from orchestrator.logging_setup import get_logger
from orchestrator.spec_generator import GeneratedSpec, FileChange

logger = get_logger("verifier")

OLLAMA_BASE = "http://localhost:11434"
DEFAULT_LOCAL_MODEL = "gpt-oss:20b"
VERIFY_TIMEOUT = 90


class Severity(StrEnum):
    critical = "CRITICAL"
    major = "MAJOR"
    minor = "MINOR"
    info = "INFO"


class ReviewComment(BaseModel):
    """A single review comment produced by verification."""
    severity: Severity
    file_path: str = ""
    message: str
    suggestion: str = ""
    line_range: str = ""


class VerificationResult(BaseModel):
    """Aggregate result of verifying an implementation against its spec."""
    spec_id: str
    goal: str
    passed: bool
    score: float = Field(ge=0.0, le=1.0, description="0.0 = total failure, 1.0 = perfect")
    comments: list[ReviewComment] = Field(default_factory=list)
    files_expected: int = 0
    files_found: int = 0
    files_missing: int = 0
    files_unplanned: int = 0
    summary: str = ""
    raw_llm_output: str = ""
    model_used: str = ""
    verification_time_seconds: float = 0.0

    @property
    def critical_count(self) -> int:
        return sum(1 for c in self.comments if c.severity == Severity.critical)

    @property
    def major_count(self) -> int:
        return sum(1 for c in self.comments if c.severity == Severity.major)

    def to_markdown(self) -> str:
        lines = [
            f"# Verification Report",
            f"**Spec:** {self.spec_id}  ",
            f"**Goal:** {self.goal}  ",
            f"**Score:** {self.score:.0%}  ",
            f"**Verdict:** {'✅ PASSED' if self.passed else '❌ FAILED'}  ",
            f"**Model:** {self.model_used}  ",
            "",
            "## File Coverage",
            f"- Expected: {self.files_expected}",
            f"- Found: {self.files_found}",
            f"- Missing: {self.files_missing}",
            f"- Unplanned: {self.files_unplanned}",
            "",
        ]

        if self.summary:
            lines += ["## Summary", self.summary, ""]

        if self.comments:
            lines.append("## Comments")
            for c in self.comments:
                icon = {"CRITICAL": "🔴", "MAJOR": "🟠", "MINOR": "🟡", "INFO": "🔵"}
                prefix = icon.get(c.severity.value, "")
                file_ref = f" `{c.file_path}`" if c.file_path else ""
                lines.append(f"### {prefix} {c.severity.value}{file_ref}")
                lines.append(c.message)
                if c.suggestion:
                    lines.append(f"**Suggestion:** {c.suggestion}")
                lines.append("")

        return "\n".join(lines)


_VERIFY_PROMPT = """You are a senior code reviewer verifying an implementation against its specification.

## Original Spec
Goal: {goal}

### Expected File Changes:
{expected_changes}

## Actual Git Diff
```diff
{diff}
```

## Files Analysis
- Expected files: {expected_files}
- Files with changes in diff: {actual_files}
- Missing files (in spec but not in diff): {missing_files}
- Unplanned files (in diff but not in spec): {unplanned_files}

## Instructions
Review the diff against the spec and produce a JSON verification report:
{{
  "score": 0.85,
  "summary": "Brief assessment of implementation quality vs spec",
  "comments": [
    {{
      "severity": "CRITICAL|MAJOR|MINOR|INFO",
      "file_path": "path/to/file.py",
      "message": "What the issue is",
      "suggestion": "How to fix it"
    }}
  ]
}}

Scoring guide:
- 1.0: Perfect implementation of spec
- 0.8+: Good, minor issues only
- 0.6-0.8: Acceptable, some significant gaps
- 0.4-0.6: Partial implementation, major gaps
- <0.4: Failed, fundamental issues

Severity guide:
- CRITICAL: Breaks functionality, missing core requirement, security issue
- MAJOR: Significant gap from spec, likely bug, missing important piece
- MINOR: Style issue, could be improved, non-blocking
- INFO: Observation, context, or praise for good implementation

Only output valid JSON."""


class SpecVerifier:
    """Verifies git diffs against generated specs using LLM analysis."""

    def __init__(
        self,
        *,
        model: str = DEFAULT_LOCAL_MODEL,
        ollama_base: str = OLLAMA_BASE,
        timeout: int = VERIFY_TIMEOUT,
    ) -> None:
        self._model = model
        self._ollama_base = ollama_base
        self._timeout = timeout

    async def verify(
        self,
        spec: GeneratedSpec,
        repo_path: str,
        diff: str | None = None,
    ) -> VerificationResult:
        """Verify the current repo state against a spec.

        If ``diff`` is not provided, generates it from git.
        """
        root = Path(repo_path).resolve()
        start = time.monotonic()

        if diff is None:
            diff = self._get_git_diff(root)

        if not diff.strip():
            return VerificationResult(
                spec_id=spec.id,
                goal=spec.goal,
                passed=False,
                score=0.0,
                summary="No changes detected in the repository.",
                files_expected=len(self._all_spec_files(spec)),
                model_used=self._model,
                verification_time_seconds=time.monotonic() - start,
            )

        # Structural checks first (no LLM needed)
        expected_files = set(self._all_spec_files(spec))
        actual_files = set(self._extract_diff_files(diff))
        missing = expected_files - actual_files
        unplanned = actual_files - expected_files

        # Build prompt and call LLM
        prompt = self._build_prompt(spec, diff, expected_files, actual_files, missing, unplanned)
        raw_output = await self._call_ollama(prompt)
        verify_time = time.monotonic() - start

        result = self._parse_response(raw_output, spec, expected_files, actual_files, missing, unplanned)
        result.model_used = self._model
        result.verification_time_seconds = round(verify_time, 2)
        result.raw_llm_output = raw_output

        # Add structural comments for missing/unplanned files
        if missing:
            result.comments.insert(0, ReviewComment(
                severity=Severity.critical if len(missing) > len(expected_files) / 2 else Severity.major,
                message=f"Missing expected file changes: {', '.join(sorted(missing))}",
                suggestion="These files were in the spec but have no changes in the diff.",
            ))

        if unplanned:
            result.comments.append(ReviewComment(
                severity=Severity.info,
                message=f"Unplanned file changes: {', '.join(sorted(unplanned))}",
                suggestion="These files were changed but not listed in the spec. Verify they're intentional.",
            ))

        logger.info(
            "Verification complete for spec %s: score=%.0f%% passed=%s "
            "(%d critical, %d major, %d comments total)",
            spec.id, result.score * 100, result.passed,
            result.critical_count, result.major_count, len(result.comments),
        )
        return result

    async def verify_and_save(
        self,
        spec: GeneratedSpec,
        repo_path: str,
        diff: str | None = None,
    ) -> tuple[VerificationResult, Path]:
        """Verify and save the report to .clawsmith/verifications/."""
        result = await self.verify(spec, repo_path, diff)

        verify_dir = Path(repo_path) / ".clawsmith" / "verifications"
        verify_dir.mkdir(parents=True, exist_ok=True)

        report_path = verify_dir / f"{spec.id}_verify.md"
        report_path.write_text(result.to_markdown(), encoding="utf-8")

        json_path = verify_dir / f"{spec.id}_verify.json"
        json_path.write_text(result.model_dump_json(indent=2), encoding="utf-8")

        return result, report_path

    def _build_prompt(
        self,
        spec: GeneratedSpec,
        diff: str,
        expected: set[str],
        actual: set[str],
        missing: set[str],
        unplanned: set[str],
    ) -> str:
        expected_changes = self._format_expected_changes(spec)

        # Truncate diff if it's massive
        max_diff = 8000
        if len(diff) > max_diff:
            diff = diff[:max_diff] + "\n\n... (diff truncated, showing first 8000 chars)"

        return _VERIFY_PROMPT.format(
            goal=spec.goal,
            expected_changes=expected_changes,
            diff=diff,
            expected_files=", ".join(sorted(expected)) or "(none)",
            actual_files=", ".join(sorted(actual)) or "(none)",
            missing_files=", ".join(sorted(missing)) or "(none)",
            unplanned_files=", ".join(sorted(unplanned)) or "(none)",
        )

    @staticmethod
    def _format_expected_changes(spec: GeneratedSpec) -> str:
        changes = spec.file_changes
        if spec.phases:
            for phase in spec.phases:
                changes.extend(phase.file_changes)

        if not changes:
            return "(no specific file changes in spec)"

        lines = []
        for fc in changes:
            lines.append(f"- `{fc.path}` ({fc.action}): {fc.description}")
            for kc in fc.key_changes:
                lines.append(f"  - {kc}")
        return "\n".join(lines)

    @staticmethod
    def _all_spec_files(spec: GeneratedSpec) -> list[str]:
        files = [fc.path for fc in spec.file_changes]
        for phase in spec.phases:
            files.extend(fc.path for fc in phase.file_changes)
        return list(dict.fromkeys(files))  # dedupe preserving order

    @staticmethod
    def _extract_diff_files(diff: str) -> list[str]:
        files = []
        for line in diff.splitlines():
            if line.startswith("+++ b/"):
                path = line[6:]
                if path != "/dev/null":
                    files.append(path)
            elif line.startswith("--- a/"):
                path = line[6:]
                if path != "/dev/null":
                    files.append(path)
        return list(dict.fromkeys(files))

    @staticmethod
    def _get_git_diff(root: Path) -> str:
        """Get the combined staged + unstaged diff."""
        try:
            # Staged changes
            staged = subprocess.run(
                ["git", "diff", "--cached"],
                capture_output=True, text=True, cwd=root,
            )
            # Unstaged changes
            unstaged = subprocess.run(
                ["git", "diff"],
                capture_output=True, text=True, cwd=root,
            )
            # Untracked files (show content)
            untracked = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard"],
                capture_output=True, text=True, cwd=root,
            )

            parts = []
            if staged.stdout.strip():
                parts.append(staged.stdout)
            if unstaged.stdout.strip():
                parts.append(unstaged.stdout)

            # For untracked files, create synthetic diff entries
            for f in untracked.stdout.strip().splitlines():
                f = f.strip()
                if f:
                    fpath = root / f
                    if fpath.is_file() and fpath.stat().st_size < 50_000:
                        try:
                            content = fpath.read_text(encoding="utf-8", errors="replace")
                            parts.append(
                                f"diff --git a/{f} b/{f}\n"
                                f"new file mode 100644\n"
                                f"--- /dev/null\n"
                                f"+++ b/{f}\n"
                                f"@@ -0,0 +1,{len(content.splitlines())} @@\n"
                                + "\n".join(f"+{line}" for line in content.splitlines())
                            )
                        except Exception:
                            pass

            return "\n".join(parts)
        except FileNotFoundError:
            logger.warning("git not found; cannot generate diff")
            return ""

    async def _call_ollama(self, prompt: str) -> str:
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.2,
                "num_predict": 2048,
            },
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._ollama_base}/api/generate",
                json=payload,
            )
            resp.raise_for_status()
            return resp.json().get("response", "")

    def _parse_response(
        self,
        raw: str,
        spec: GeneratedSpec,
        expected: set[str],
        actual: set[str],
        missing: set[str],
        unplanned: set[str],
    ) -> VerificationResult:
        import json
        import re

        data = None
        # Try JSON extraction
        for pattern in [r"```json\s*\n(.*?)\n\s*```", r"```\s*\n(.*?)\n\s*```", r"\{.*\}"]:
            match = re.search(pattern, raw, re.DOTALL)
            if match:
                candidate = match.group(1) if match.lastindex else match.group(0)
                try:
                    data = json.loads(candidate)
                    break
                except json.JSONDecodeError:
                    continue

        if not data:
            try:
                data = json.loads(raw.strip())
            except json.JSONDecodeError:
                pass

        if not data:
            # Fallback: structural analysis only
            coverage = len(actual & expected) / max(len(expected), 1)
            return VerificationResult(
                spec_id=spec.id,
                goal=spec.goal,
                passed=coverage > 0.7 and not missing,
                score=round(coverage, 2),
                files_expected=len(expected),
                files_found=len(actual & expected),
                files_missing=len(missing),
                files_unplanned=len(unplanned),
                summary="LLM verification output could not be parsed. Score based on file coverage only.",
            )

        # Parse comments
        comments = []
        for c_data in data.get("comments", []):
            try:
                comments.append(ReviewComment(
                    severity=Severity(c_data.get("severity", "INFO")),
                    file_path=c_data.get("file_path", ""),
                    message=c_data.get("message", ""),
                    suggestion=c_data.get("suggestion", ""),
                ))
            except Exception:
                pass

        score = float(data.get("score", 0.5))
        score = max(0.0, min(1.0, score))

        has_critical = any(c.severity == Severity.critical for c in comments)

        return VerificationResult(
            spec_id=spec.id,
            goal=spec.goal,
            passed=score >= 0.6 and not has_critical,
            score=score,
            comments=comments,
            files_expected=len(expected),
            files_found=len(actual & expected),
            files_missing=len(missing),
            files_unplanned=len(unplanned),
            summary=data.get("summary", ""),
        )
