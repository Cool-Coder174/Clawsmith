"""Forge engine — the full spec-driven development loop.

    Goal → Spec → Execute (per phase) → Verify → Fix → Re-verify → Done

This is the Traycer-equivalent pipeline: LLM-generated specs drive
execution through coding agents, with semantic verification and
automatic fix loops. Everything runs on local models by default.

Modes:
    plan     — generate spec only, save for review
    execute  — spec → agent execution → verify
    forge    — full loop with auto-fix on verification failure
"""

from __future__ import annotations

import time
from enum import StrEnum
from pathlib import Path

from orchestrator.logging_setup import get_logger
from orchestrator.spec_generator import GeneratedSpec, SpecGenerator, SpecTier
from orchestrator.verifier import SpecVerifier, VerificationResult, Severity
from orchestrator.schemas import (
    ContextPacket,
    TaskClassification,
    YoloConfig,
    YoloResult,
)
from orchestrator.agent_status import AgentPhase, StatusTracker
from orchestrator.yolo import YoloEngine

logger = get_logger("forge")


class ForgeMode(StrEnum):
    plan = "plan"
    execute = "execute"
    forge = "forge"


class ForgeResult:
    """Aggregate result of a forge run."""

    def __init__(self) -> None:
        self.goal: str = ""
        self.spec: GeneratedSpec | None = None
        self.spec_path: Path | None = None
        self.execution_result: YoloResult | None = None
        self.verification_results: list[VerificationResult] = []
        self.fix_attempts: int = 0
        self.success: bool = False
        self.error: str | None = None
        self.duration_seconds: float = 0.0
        self.branch: str | None = None
        self.pr_url: str | None = None
        self.pr_number: int | None = None
        self.commits: list[str] = []

    @property
    def final_verification(self) -> VerificationResult | None:
        return self.verification_results[-1] if self.verification_results else None

    def summary(self) -> dict:
        v = self.final_verification
        return {
            "goal": self.goal,
            "spec_id": self.spec.id if self.spec else None,
            "spec_tier": self.spec.tier.value if self.spec else None,
            "phases": len(self.spec.phases) if self.spec else 0,
            "file_changes": len(self.spec.file_changes) if self.spec else 0,
            "execution_success": self.execution_result.success if self.execution_result else None,
            "verification_score": v.score if v else None,
            "verification_passed": v.passed if v else None,
            "critical_issues": v.critical_count if v else 0,
            "major_issues": v.major_count if v else 0,
            "fix_attempts": self.fix_attempts,
            "overall_success": self.success,
            "duration_seconds": round(self.duration_seconds, 1),
            "error": self.error,
            "branch": self.branch,
            "pr_url": self.pr_url,
            "pr_number": self.pr_number,
            "commits": self.commits,
        }


class ForgeEngine:
    """Full spec-driven development pipeline.

    Usage::

        engine = ForgeEngine()
        result = await engine.run(
            "Add JWT authentication to the API",
            repo_path=".",
        )
    """

    def __init__(
        self,
        *,
        spec_model: str = "qwen2.5-coder:14b",
        verify_model: str = "qwen2.5-coder:14b",
        ollama_base: str = "http://localhost:11434",
        max_fix_loops: int = 2,
        auto_branch: bool = False,
        auto_pr: bool = False,
        pr_draft: bool = True,
        pr_labels: list[str] | None = None,
    ) -> None:
        self._spec_gen = SpecGenerator(model=spec_model, ollama_base=ollama_base)
        self._verifier = SpecVerifier(model=verify_model, ollama_base=ollama_base)
        self._max_fix_loops = max_fix_loops
        self._auto_branch = auto_branch
        self._auto_pr = auto_pr
        self._pr_draft = pr_draft
        self._pr_labels = pr_labels or ["forge"]

    async def run(
        self,
        goal: str,
        repo_path: str,
        *,
        mode: ForgeMode = ForgeMode.forge,
        spec_tier: SpecTier | None = None,
        yolo_config: YoloConfig | None = None,
        status: StatusTracker | None = None,
        context: ContextPacket | None = None,
        classification: TaskClassification | None = None,
    ) -> ForgeResult:
        """Run the forge pipeline."""
        start = time.monotonic()
        tracker = status or StatusTracker()
        result = ForgeResult()
        result.goal = goal

        try:
            # ── PHASE 1: GATHER CONTEXT ───────────────────────────
            if context is None or classification is None:
                tracker.transition(AgentPhase.planning, "Gathering repository context")
                context, classification = await self._gather_context(goal, repo_path, tracker)

            # ── PHASE 2: GENERATE SPEC ────────────────────────────
            tracker.step("Generating implementation spec")
            spec, spec_path = await self._spec_gen.generate_and_save(
                goal, repo_path, context, classification, spec_tier,
            )
            result.spec = spec
            result.spec_path = spec_path

            logger.info(
                "Spec generated: id=%s tier=%s files=%d phases=%d",
                spec.id, spec.tier.value, len(spec.file_changes), len(spec.phases),
            )
            tracker.step(
                "Spec generated",
                f"{spec.tier.value} tier, {len(spec.file_changes)} files, "
                f"{len(spec.phases)} phases",
            )

            if mode == ForgeMode.plan:
                result.success = True
                result.duration_seconds = time.monotonic() - start
                return result

            # ── PHASE 2b: CREATE BRANCH ───────────────────────────
            if self._auto_branch or self._auto_pr:
                try:
                    from orchestrator.git_ops import GitOps
                    git = GitOps(repo_path)
                    branch = git.create_branch(spec)
                    result.branch = branch
                    tracker.step("Created branch", branch)
                except Exception as exc:
                    logger.warning("Branch creation failed: %s", exc)
                    tracker.step("Branch creation skipped", str(exc))

            # ── PHASE 3: EXECUTE VIA YOLO ─────────────────────────
            tracker.transition(AgentPhase.executing, "Executing spec via YOLO engine")
            yolo_plan = spec.to_yolo_plan(repo_path)
            cfg = yolo_config or YoloConfig()

            # Inject spec context into each phase's objective for better prompts
            for phase in yolo_plan.phases:
                spec_files = self._get_phase_files(spec, phase.index)
                if spec_files:
                    file_detail = "\n".join(
                        f"- `{fc.path}` ({fc.action}): {fc.description}"
                        for fc in spec_files
                    )
                    phase.objective = (
                        f"{phase.objective}\n\n"
                        f"## Spec File Changes\n{file_detail}"
                    )

            engine = YoloEngine()
            exec_result = await engine.execute(
                goal, repo_path, config=cfg, status=tracker,
            )
            result.execution_result = exec_result

            if not exec_result.success:
                logger.warning("YOLO execution failed: %s", exec_result.error_message)
                tracker.step("Execution failed", exec_result.error_message or "unknown")

                if mode == ForgeMode.execute:
                    result.error = exec_result.error_message
                    result.duration_seconds = time.monotonic() - start
                    return result

            # ── PHASE 4: VERIFY ───────────────────────────────────
            tracker.transition(AgentPhase.verifying, "Running semantic verification")
            verification = await self._verifier.verify_and_save(spec, repo_path)
            result.verification_results.append(verification[0])

            logger.info(
                "Verification: score=%.0f%% passed=%s critical=%d major=%d",
                verification[0].score * 100, verification[0].passed,
                verification[0].critical_count, verification[0].major_count,
            )
            tracker.step(
                "Verification complete",
                f"score={verification[0].score:.0%} "
                f"critical={verification[0].critical_count} "
                f"major={verification[0].major_count}",
            )

            if verification[0].passed:
                result.success = True
                await self._auto_commit_and_pr(result, repo_path, tracker)
                tracker.transition(AgentPhase.complete, "Forge complete — verification passed")
                result.duration_seconds = time.monotonic() - start
                return result

            if mode == ForgeMode.execute:
                result.success = verification[0].passed
                result.duration_seconds = time.monotonic() - start
                return result

            # ── PHASE 5: FIX LOOP (forge mode only) ──────────────
            for fix_round in range(1, self._max_fix_loops + 1):
                result.fix_attempts = fix_round
                tracker.step(
                    f"Fix attempt {fix_round}/{self._max_fix_loops}",
                    f"Addressing {verification[0].critical_count} critical, "
                    f"{verification[0].major_count} major issues",
                )

                # Build fix prompt from verification comments
                fix_goal = self._build_fix_goal(goal, verification[0])
                fix_plan = self._spec_gen._build_prompt(
                    fix_goal, context, classification, SpecTier.quick,
                )

                # Re-execute with fix focus
                fix_cfg = YoloConfig(
                    max_retries=1,
                    skip_planning=True,
                    timeout_per_phase=cfg.timeout_per_phase,
                )
                fix_result = await engine.execute(
                    fix_goal, repo_path, config=fix_cfg, status=tracker,
                )

                # Re-verify
                tracker.step(f"Re-verifying after fix {fix_round}")
                verification = await self._verifier.verify_and_save(spec, repo_path)
                result.verification_results.append(verification[0])

                if verification[0].passed:
                    result.success = True
                    await self._auto_commit_and_pr(result, repo_path, tracker)
                    tracker.transition(
                        AgentPhase.complete,
                        f"Forge complete — passed after {fix_round} fix(es)",
                    )
                    break

                logger.info(
                    "Fix %d: score=%.0f%% (was %.0f%%)",
                    fix_round,
                    verification[0].score * 100,
                    result.verification_results[-2].score * 100 if len(result.verification_results) > 1 else 0,
                )

            if not result.success:
                result.error = (
                    f"Verification failed after {self._max_fix_loops} fix attempts. "
                    f"Final score: {verification[0].score:.0%}"
                )
                tracker.fail("Forge failed", result.error)

        except Exception as exc:
            logger.exception("Forge run failed: %s", exc)
            result.error = str(exc)
            tracker.fail("Forge exception", str(exc))

        result.duration_seconds = time.monotonic() - start
        return result

    async def _auto_commit_and_pr(
        self,
        result: ForgeResult,
        repo_path: str,
        tracker: StatusTracker,
    ) -> None:
        """Commit changes and optionally create a PR."""
        if not (self._auto_branch or self._auto_pr) or not result.spec:
            return

        try:
            from orchestrator.git_ops import GitOps
            git = GitOps(repo_path)

            # Commit
            commit_hash = git.commit(result.spec)
            if commit_hash:
                result.commits.append(commit_hash)
                tracker.step("Committed changes", commit_hash)

            # Push and PR
            if self._auto_pr and result.branch:
                if git.push(result.branch):
                    tracker.step("Pushed to remote", result.branch)

                    v = result.final_verification
                    pr_result = git.create_pr(
                        result.spec,
                        verification=v,
                        draft=self._pr_draft,
                        labels=self._pr_labels,
                    )
                    if "pr_url" in pr_result:
                        result.pr_url = pr_result["pr_url"]
                        result.pr_number = pr_result.get("pr_number")
                        tracker.step("PR created", result.pr_url)
                    elif "error" in pr_result:
                        tracker.step("PR creation failed", pr_result["error"])
        except Exception as exc:
            logger.warning("Auto commit/PR failed: %s", exc)
            tracker.step("Auto commit/PR skipped", str(exc))

    async def _gather_context(
        self,
        goal: str,
        repo_path: str,
        tracker: StatusTracker,
    ) -> tuple[ContextPacket, TaskClassification]:
        """Audit repo and classify task."""
        from tools.repo_auditor import RepoAuditor
        from tools.repo_mapper import RepoMapper
        from tools.context_packer import ContextPacker
        from routing.classifier import TaskClassifier

        root = Path(repo_path).resolve()

        tracker.step("Auditing repository")
        audit = RepoAuditor(root).audit()

        tracker.step("Mapping repository structure")
        repo_map = RepoMapper(root).map()

        tracker.step("Packing context")
        context = ContextPacker(root).pack(audit, repo_map, goal)

        tracker.step("Classifying task")
        classification = TaskClassifier().classify(goal, context)

        return context, classification

    @staticmethod
    def _get_phase_files(spec: GeneratedSpec, phase_index: int) -> list:
        """Get file changes for a specific phase from the spec."""
        if spec.phases and phase_index < len(spec.phases):
            return spec.phases[phase_index].file_changes
        if phase_index == 0:
            return spec.file_changes
        return []

    @staticmethod
    def _build_fix_goal(original_goal: str, verification: VerificationResult) -> str:
        """Build a fix-focused goal from verification comments."""
        issues = []
        for c in verification.comments:
            if c.severity in (Severity.critical, Severity.major):
                file_ref = f" in `{c.file_path}`" if c.file_path else ""
                issues.append(f"- [{c.severity.value}]{file_ref}: {c.message}")
                if c.suggestion:
                    issues.append(f"  Fix: {c.suggestion}")

        if not issues:
            return f"Fix remaining issues from: {original_goal}"

        issues_text = "\n".join(issues)
        return (
            f"Fix the following issues from the implementation of: {original_goal}\n\n"
            f"## Issues to Fix\n{issues_text}\n\n"
            f"Address all CRITICAL issues first, then MAJOR issues."
        )
