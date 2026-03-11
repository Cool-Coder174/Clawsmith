"""YOLO execution engine — autonomous multi-phase task execution.

Replicates the core utility of Traycer's YOLO mode for OpenClaw:
give it a goal, and it decomposes, plans, executes, verifies, retries,
and reports with minimal human intervention.

Execution flow::

    Goal → Audit/Classify → Complexity Analysis → Decompose into Phases
         → Build FIFO Queue → For each phase:
               Plan → Execute (pipeline) → Verify → Fix/Retry
         → Aggregate Results → YoloResult
"""

from __future__ import annotations

import time
from pathlib import Path

from orchestrator.agent_status import AgentPhase, StatusTracker
from orchestrator.logging_setup import get_logger
from orchestrator.pipeline import OrchestrationPipeline
from orchestrator.planner import TaskPlanner
from orchestrator.schemas import (
    ContextPacket,
    PipelineResult,
    TaskClassification,
    YoloConfig,
    YoloPhase,
    YoloPhaseResult,
    YoloPhaseStatus,
    YoloPlan,
    YoloResult,
)
from orchestrator.task_queue import QueueExhausted, QueuePaused, TaskQueue
from routing.classifier import TaskClassifier
from tools.context_packer import ContextPacker
from tools.repo_auditor import RepoAuditor
from tools.repo_mapper import RepoMapper

logger = get_logger("yolo")


class YoloEngine:
    """Autonomous multi-phase execution engine.

    Usage::

        engine = YoloEngine()
        result = await engine.execute(
            "Add user authentication with JWT",
            repo_path=".",
        )
    """

    def __init__(self) -> None:
        self._pipeline = OrchestrationPipeline()
        self._planner = TaskPlanner()

    async def execute(
        self,
        goal: str,
        repo_path: str,
        config: YoloConfig | None = None,
        status: StatusTracker | None = None,
    ) -> YoloResult:
        start = time.monotonic()
        cfg = config or YoloConfig()
        tracker = status or StatusTracker()
        root = Path(repo_path).resolve()

        if not root.exists() or not root.is_dir():
            tracker.fail("Invalid repository path", str(root))
            return YoloResult(
                plan_id="",
                goal=goal,
                repo_path=str(root),
                success=False,
                error_message=f"Repository path does not exist: {root}",
                duration_seconds=time.monotonic() - start,
                agent_status=tracker.summary(),
            )

        try:
            return await self._run(goal, root, cfg, tracker, start)
        except Exception as exc:
            logger.exception("YOLO run failed: %s", exc)
            tracker.fail("YOLO engine exception", str(exc))
            return YoloResult(
                plan_id="",
                goal=goal,
                repo_path=str(root),
                success=False,
                error_message=str(exc),
                duration_seconds=time.monotonic() - start,
                agent_status=tracker.summary(),
            )

    async def _run(
        self,
        goal: str,
        root: Path,
        cfg: YoloConfig,
        tracker: StatusTracker,
        start: float,
    ) -> YoloResult:
        # ── DEPLOY ────────────────────────────────────────────────
        tracker.transition(AgentPhase.deployed, "YOLO engine started", str(root))

        # ── DECOMPOSE ─────────────────────────────────────────────
        tracker.transition(AgentPhase.decomposing, "Analysing goal complexity")
        plan = await self._build_plan(goal, root, cfg, tracker)
        tracker.step(
            "Decomposition complete",
            f"{len(plan.phases)} phases, bucket={plan.complexity.bucket.value}",
        )

        # ── BUILD QUEUE ───────────────────────────────────────────
        tracker.transition(
            AgentPhase.queued,
            "Building execution queue",
            f"{len(plan.phases)} phases enqueued",
        )
        queue = TaskQueue(plan.phases)

        # ── EXECUTE PHASES ────────────────────────────────────────
        while not queue.is_exhausted:
            try:
                phase = queue.next()
            except QueuePaused:
                tracker.step("Queue paused", "Waiting for resume")
                break
            except QueueExhausted:
                break

            phase_result = await self._execute_phase(
                phase, plan, root, cfg, tracker, queue,
            )

            if phase_result.status == YoloPhaseStatus.failed and cfg.pause_on_failure:
                queue.pause(f"Phase '{phase.title}' failed after all retries")
                tracker.step("Queue paused on failure", phase.title)
                break

        # ── AGGREGATE RESULTS ─────────────────────────────────────
        duration = time.monotonic() - start
        results = queue.results()
        completed = queue.completed_count
        failed = queue.failed_count
        skipped = queue.skipped_count
        all_ok = failed == 0 and completed == queue.total

        if all_ok:
            tracker.transition(
                AgentPhase.complete,
                "YOLO run complete",
                f"{completed}/{queue.total} phases succeeded in {duration:.1f}s",
            )
        elif queue.is_paused:
            tracker.step(
                "YOLO run paused",
                f"{completed} done, {failed} failed, {queue.remaining} remaining",
            )
        else:
            tracker.fail(
                "YOLO run finished with failures",
                f"{failed} phase(s) failed out of {queue.total}",
            )

        return YoloResult(
            plan_id=plan.id,
            goal=goal,
            repo_path=str(root),
            phase_results=results,
            total_phases=queue.total,
            completed_phases=completed,
            failed_phases=failed,
            skipped_phases=skipped,
            success=all_ok,
            error_message=self._aggregate_errors(results) if not all_ok else None,
            duration_seconds=duration,
            agent_status=tracker.summary(),
        )

    # -- plan building ------------------------------------------------------

    async def _build_plan(
        self,
        goal: str,
        root: Path,
        cfg: YoloConfig,
        tracker: StatusTracker,
    ) -> YoloPlan:
        tracker.step("Auditing repository", str(root))
        audit = RepoAuditor(root).audit()

        tracker.step("Mapping repository")
        repo_map = RepoMapper(root).map()

        tracker.step("Packing context")
        context = ContextPacker(root).pack(audit, repo_map, goal)

        tracker.step("Classifying task")
        classification = TaskClassifier().classify(goal, context)

        tracker.step("Decomposing goal into phases")
        plan = self._planner.decompose(
            goal,
            str(root),
            context=context,
            classification=classification,
            skip_planning=cfg.skip_planning,
        )

        self._context_cache = context
        self._classification_cache = classification
        return plan

    # -- per-phase execution ------------------------------------------------

    async def _execute_phase(
        self,
        phase: YoloPhase,
        plan: YoloPlan,
        root: Path,
        cfg: YoloConfig,
        tracker: StatusTracker,
        queue: TaskQueue,
    ) -> YoloPhaseResult:
        phase_start = time.monotonic()
        phase_num = phase.index + 1
        total = plan.complexity.recommended_phases
        attempt = 0
        last_error: str | None = None

        while attempt <= cfg.max_retries:
            attempt += 1
            tracker.set_yolo_progress(phase_num, queue.total, phase.title, attempt)

            if attempt == 1:
                tracker.transition(
                    AgentPhase.executing,
                    f"Phase {phase_num}/{queue.total}: {phase.title}",
                )
            else:
                tracker.transition(
                    AgentPhase.retrying,
                    f"Retrying phase {phase_num}/{queue.total} (attempt {attempt})",
                    last_error or "",
                )

            objective = self._build_phase_prompt(phase, last_error, attempt)

            pipeline_result = await self._pipeline.run(
                objective,
                str(root),
                dry_run=cfg.dry_run,
                agent_target=cfg.agent_target,
                status=tracker,
            )

            # ── VERIFY ────────────────────────────────────────────
            tracker.transition(
                AgentPhase.verifying,
                f"Verifying phase {phase_num}/{queue.total}",
            )

            if pipeline_result.success:
                duration = time.monotonic() - phase_start
                return queue.complete(phase, pipeline_result, duration)

            last_error = pipeline_result.error_message or "Pipeline returned failure"
            can_retry = attempt <= cfg.max_retries
            if not can_retry:
                duration = time.monotonic() - phase_start
                result = queue.fail(phase, last_error, can_retry=False)
                result.pipeline_result = pipeline_result
                result.duration_seconds = duration
                return result

            queue.fail(phase, last_error, can_retry=True)
            _ = queue.next()

        duration = time.monotonic() - phase_start
        result = queue.fail(phase, last_error or "Max retries exceeded", can_retry=False)
        result.duration_seconds = duration
        return result

    # -- prompt building ----------------------------------------------------

    def _build_phase_prompt(
        self,
        phase: YoloPhase,
        last_error: str | None,
        attempt: int,
    ) -> str:
        parts = [phase.objective]

        if phase.acceptance_criteria:
            criteria = "\n".join(f"- {c}" for c in phase.acceptance_criteria)
            parts.append(f"\n\nAcceptance criteria:\n{criteria}")

        if phase.files_in_scope:
            files = ", ".join(phase.files_in_scope[:15])
            parts.append(f"\n\nFiles in scope: {files}")

        if last_error and attempt > 1:
            parts.append(
                f"\n\nPREVIOUS ATTEMPT FAILED (attempt {attempt - 1}):\n"
                f"{last_error}\n\n"
                "Fix the error described above while still completing the objective."
            )

        return "".join(parts)

    @staticmethod
    def _aggregate_errors(results: list[YoloPhaseResult]) -> str:
        errors = []
        for r in results:
            if r.status == YoloPhaseStatus.failed and r.error_history:
                errors.append(f"Phase '{r.title}': {r.error_history[-1]}")
        return "; ".join(errors) if errors else "Unknown failure"
