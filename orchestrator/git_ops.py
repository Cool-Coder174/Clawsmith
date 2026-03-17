"""Git operations for forge — branch, commit, PR creation.

Provides automated git workflows for spec-driven development:
- Create feature branches from spec IDs
- Stage and commit changes with spec-linked messages
- Create GitHub PRs via `gh` CLI with spec details in the body
- Link PRs back to specs and verification reports
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from orchestrator.logging_setup import get_logger
from orchestrator.spec_generator import GeneratedSpec
from orchestrator.verifier import VerificationResult

logger = get_logger("git_ops")

_SAFE_BRANCH = re.compile(r"[^a-zA-Z0-9_/.\-]")


def _run_git(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    result = subprocess.run(
        ["git"] + args,
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )
    if check and result.returncode != 0:
        logger.error("git %s failed: %s", " ".join(args), result.stderr.strip())
    return result


def _run_gh(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """Run a gh CLI command and return the result."""
    return subprocess.run(
        ["gh"] + args,
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


def slugify_goal(goal: str, max_len: int = 50) -> str:
    """Convert a goal string into a branch-safe slug."""
    slug = goal.lower().strip()
    slug = _SAFE_BRANCH.sub("-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug[:max_len]


class GitOps:
    """Git operations manager for spec-driven development."""

    def __init__(self, repo_path: str | Path) -> None:
        self._root = Path(repo_path).resolve()

    def current_branch(self) -> str:
        """Get the current branch name."""
        result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], self._root)
        return result.stdout.strip()

    def is_clean(self) -> bool:
        """Check if the working directory is clean."""
        result = _run_git(["status", "--porcelain"], self._root)
        return not result.stdout.strip()

    def has_changes(self) -> bool:
        """Check if there are staged or unstaged changes."""
        return not self.is_clean()

    def create_branch(self, spec: GeneratedSpec, prefix: str = "forge") -> str:
        """Create and checkout a feature branch for a spec.

        Returns the branch name.
        """
        slug = slugify_goal(spec.goal)
        branch = f"{prefix}/{spec.id[:8]}-{slug}"

        result = _run_git(["checkout", "-b", branch], self._root)
        if result.returncode != 0:
            # Branch might already exist
            _run_git(["checkout", branch], self._root)

        logger.info("Created branch: %s", branch)
        return branch

    def stage_all(self) -> None:
        """Stage all changes."""
        _run_git(["add", "-A"], self._root)

    def commit(
        self,
        spec: GeneratedSpec,
        message: str | None = None,
        phase_index: int | None = None,
    ) -> str | None:
        """Commit staged changes with a spec-linked message.

        Returns the commit hash, or None if nothing to commit.
        """
        if not message:
            if phase_index is not None and spec.phases:
                phase = spec.phases[phase_index] if phase_index < len(spec.phases) else None
                phase_title = phase.title if phase else f"Phase {phase_index + 1}"
                message = f"forge({spec.id[:8]}): {phase_title}\n\n{spec.goal[:200]}"
            else:
                message = f"forge({spec.id[:8]}): {spec.goal[:200]}"

        self.stage_all()

        # Check if there's anything to commit
        status = _run_git(["status", "--porcelain"], self._root)
        if not status.stdout.strip():
            logger.info("Nothing to commit")
            return None

        result = _run_git(["commit", "-m", message], self._root)
        if result.returncode != 0:
            logger.error("Commit failed: %s", result.stderr)
            return None

        # Get the commit hash
        hash_result = _run_git(["rev-parse", "HEAD"], self._root)
        commit_hash = hash_result.stdout.strip()[:12]
        logger.info("Committed: %s", commit_hash)
        return commit_hash

    def push(self, branch: str | None = None, set_upstream: bool = True) -> bool:
        """Push the current branch to origin."""
        args = ["push"]
        if set_upstream:
            target = branch or self.current_branch()
            args += ["--set-upstream", "origin", target]

        result = _run_git(args, self._root, check=False)
        if result.returncode != 0:
            logger.error("Push failed: %s", result.stderr)
            return False
        return True

    def create_pr(
        self,
        spec: GeneratedSpec,
        verification: VerificationResult | None = None,
        draft: bool = True,
        labels: list[str] | None = None,
    ) -> dict:
        """Create a GitHub PR via `gh` CLI with spec details in the body.

        Returns a dict with pr_url, pr_number, or error.
        """
        title = f"[forge] {spec.goal[:80]}"
        body = self._build_pr_body(spec, verification)

        args = [
            "pr", "create",
            "--title", title,
            "--body", body,
        ]

        if draft:
            args.append("--draft")

        if labels:
            for label in labels:
                args.extend(["--label", label])

        result = _run_gh(args, self._root)

        if result.returncode != 0:
            error = result.stderr.strip()
            logger.error("PR creation failed: %s", error)
            return {"error": error}

        pr_url = result.stdout.strip()
        logger.info("PR created: %s", pr_url)

        # Extract PR number
        pr_number = None
        match = re.search(r"/pull/(\d+)", pr_url)
        if match:
            pr_number = int(match.group(1))

        return {
            "pr_url": pr_url,
            "pr_number": pr_number,
            "title": title,
            "draft": draft,
        }

    def _build_pr_body(
        self,
        spec: GeneratedSpec,
        verification: VerificationResult | None,
    ) -> str:
        """Build a detailed PR body from spec and verification data."""
        lines = [
            "## Summary",
            spec.summary or spec.goal,
            "",
            "## Spec Details",
            f"- **Spec ID:** `{spec.id}`",
            f"- **Tier:** {spec.tier.value}",
            f"- **Model:** {spec.model_used}",
            f"- **Generated in:** {spec.generation_time_seconds:.1f}s",
            "",
        ]

        if spec.file_changes:
            lines.append("## File Changes")
            for fc in spec.file_changes:
                lines.append(f"- `{fc.path}` ({fc.action}): {fc.description}")
            lines.append("")

        if spec.phases:
            lines.append("## Phases")
            for phase in spec.phases:
                lines.append(f"### Phase {phase.index + 1}: {phase.title}")
                lines.append(phase.objective)
                if phase.acceptance_criteria:
                    for ac in phase.acceptance_criteria:
                        lines.append(f"- [x] {ac}")
                lines.append("")

        if verification:
            lines.append("## Verification")
            icon = ":white_check_mark:" if verification.passed else ":x:"
            lines.append(f"- **Verdict:** {icon} {'PASSED' if verification.passed else 'FAILED'}")
            lines.append(f"- **Score:** {verification.score:.0%}")
            lines.append(f"- **Critical:** {verification.critical_count}")
            lines.append(f"- **Major:** {verification.major_count}")

            if verification.comments:
                lines.append("")
                lines.append("### Review Comments")
                for c in verification.comments[:10]:
                    severity_icon = {
                        "CRITICAL": ":red_circle:",
                        "MAJOR": ":orange_circle:",
                        "MINOR": ":yellow_circle:",
                        "INFO": ":blue_circle:",
                    }.get(c.severity.value, "")
                    file_ref = f" `{c.file_path}`" if c.file_path else ""
                    lines.append(f"- {severity_icon} **{c.severity.value}**{file_ref}: {c.message}")
            lines.append("")

        if spec.risks:
            lines.append("## Risks")
            for r in spec.risks:
                lines.append(f"- {r}")
            lines.append("")

        lines.append("---")
        lines.append("*Generated by [ClawSmith](https://github.com/Cool-Coder174/ClawSmith) forge*")

        return "\n".join(lines)

    def checkout(self, branch: str) -> bool:
        """Checkout an existing branch."""
        result = _run_git(["checkout", branch], self._root, check=False)
        return result.returncode == 0

    def diff_stat(self) -> str:
        """Get a diffstat of current changes."""
        result = _run_git(["diff", "--stat"], self._root)
        return result.stdout.strip()

    def log_oneline(self, count: int = 5) -> str:
        """Get recent commit log."""
        result = _run_git(["log", "--oneline", f"-{count}"], self._root)
        return result.stdout.strip()
