"""YOLO execution engine — autonomous multi-phase task execution.

Give it a goal, and it decomposes, plans, executes, verifies, retries,
and reports with minimal human intervention.

Execution flow::

    Goal → Audit/Classify → Complexity Analysis → Decompose into Phases
         → Build FIFO Queue → For each phase:
               Generate Prompt → CLI Agent Execute → Verify → Fix/Retry
         → Aggregate Results → YoloResult

Each phase is executed by building a prompt, setting it into
``CLAWSMITH_PROMPT``, and invoking ``agent chat "$env:CLAWSMITH_PROMPT"``.
The execution backend is pluggable — ``CliAgentBackend`` is the default.
"""

from __future__ import annotations

import time
from pathlib import Path

from execution.backend import BackendConfig, ExecutionBackend
from execution.cli_agent import CliAgentBackend
from execution.models import RunManifest
from execution.phase_executor import PhaseExecutor
from orchestrator.agent_status import AgentPhase, StatusTracker
from orchestrator.logging_setup import get_logger
from orchestrator.planner import TaskPlanner
from orchestrator.schemas import (
    ContextPacket,
    YoloConfig,
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


def _resolve_backend(root: Path, bc: BackendConfig) -> ExecutionBackend:
    """Detect the best available execution backend.

    Tries CLI agents first (via the agent registry/router).  Falls back
    to the local LLM backend when no agent CLI is installed.
    """
    try:
        from agents.registry import get_agent_registry
        from agents.router import AgentNotAvailableError, AgentRouter

        try:
            from config.config_loader import get_config
            config = get_config()
            default_agent = config.agents.default_agent
            fallback_order = config.agents.fallback_order
            auto_detect = config.agents.auto_detect
        except Exception:
            default_agent = None
            fallback_order = None
            auto_detect = True

        registry = get_agent_registry(auto_detect=auto_detect)
        router = AgentRouter(
            registry,
            default_agent=default_agent,
            fallback_order=fallback_order,
        )

        decision = router.select_agent(needs_headless=True)
        detection = registry.get_detection(decision.agent_id)
        adapter = decision.adapter

        agent_cmd = (
            detection.executable_path
            if detection and detection.executable_path
            else adapter.executable_names[0]
        )

        logger.info(
            "Auto-detected agent CLI: %s (%s)",
            decision.agent_id, agent_cmd,
        )
        return CliAgentBackend(
            config=bc,
            agent_command=agent_cmd,
            adapter=adapter,
        )

    except AgentNotAvailableError:
        logger.info("No CLI agent found; falling back to local LLM backend")
    except Exception as exc:
        logger.warning("Agent detection failed (%s); using LLM backend", exc)

    from execution.llm_backend import LlmBackend
    return LlmBackend(config=bc)


class YoloEngine:
    """Autonomous multi-phase execution engine.

    Usage::

        engine = YoloEngine()
        result = await engine.execute(
            "Add user authentication with JWT",
            repo_path=".",
        )

    To use a custom execution backend::

        from execution.backend import BackendConfig
        from execution.cli_agent import CliAgentBackend

        backend = CliAgentBackend(
            config=BackendConfig(timeout_seconds=900),
            agent_command="claude",
            agent_subcommand="chat",
        )
        engine = YoloEngine(backend=backend)
    """

    def __init__(
        self,
        *,
        backend: ExecutionBackend | None = None,
        backend_config: BackendConfig | None = None,
    ) -> None:
        self._planner = TaskPlanner()
        self._backend = backend
        self._backend_config = backend_config

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

        # ── INIT EXECUTOR ─────────────────────────────────────────
        executor = self._create_executor(root)
        manifest = executor.init_run(
            run_id=tracker.run_id,
            goal=goal,
            plan=plan,
            config=cfg,
        )

        # ── BUILD QUEUE ───────────────────────────────────────────
        tracker.transition(
            AgentPhase.queued,
            "Building execution queue",
            f"{len(plan.phases)} phases enqueued, backend={executor.backend.backend_id}",
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

            phase_result = await executor.execute_phase(
                phase, plan, queue, tracker, cfg,
                context=self._context_cache,
                classification=self._classification_cache,
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

        # Finalize logging and manifest
        executor.finalize_run(
            success=all_ok,
            duration=duration,
            completed=completed,
            failed=failed,
            error=self._aggregate_errors(results) if not all_ok else None,
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

    # -- executor factory ---------------------------------------------------

    def _create_executor(self, root: Path) -> PhaseExecutor:
        """Build a PhaseExecutor with the configured or auto-detected backend."""
        bc = self._backend_config or BackendConfig(
            working_directory=str(root),
        )
        backend = self._backend or _resolve_backend(root, bc)
        return PhaseExecutor(
            repo_path=root,
            backend=backend,
            backend_config=bc,
        )

    # -- resume support -----------------------------------------------------

    async def resume(
        self,
        repo_path: str,
        config: YoloConfig | None = None,
        status: StatusTracker | None = None,
        run_id: str | None = None,
    ) -> YoloResult:
        """Resume a paused or failed YOLO run from the last successful phase.

        If ``run_id`` is provided, resumes that specific run. Otherwise, finds
        the most recent resumable run in the logs directory.
        """
        root = Path(repo_path).resolve()
        manifest_dir = root / "logs" / "runs"

        if run_id:
            manifest_path = manifest_dir / f"manifest_{run_id}.json"
            if not manifest_path.exists():
                raise FileNotFoundError(f"Run manifest not found: {manifest_path}")
            manifest = RunManifest.load(manifest_path)
        else:
            manifest = RunManifest.find_resumable(manifest_dir)
            if manifest is None:
                raise FileNotFoundError("No resumable run found")

        logger.info(
            "Resuming run %s from phase %d",
            manifest.run_id, manifest.last_completed_index + 1,
        )

        plan = YoloPlan.model_validate(manifest.plan_snapshot)
        resume_cfg = config or YoloConfig.model_validate(manifest.config_snapshot)

        phases_to_run = [
            p for p in plan.phases if p.index > manifest.last_completed_index
        ]
        if not phases_to_run:
            logger.info("All phases already completed for run %s", manifest.run_id)
            return YoloResult(
                plan_id=plan.id,
                goal=manifest.goal,
                repo_path=str(root),
                success=True,
                duration_seconds=0.0,
            )

        tracker = status or StatusTracker(run_id=manifest.run_id)
        start = time.monotonic()

        tracker.transition(
            AgentPhase.deployed,
            "Resuming YOLO run",
            f"from phase {manifest.last_completed_index + 2}",
        )

        executor = self._create_executor(root)
        executor._run_id = manifest.run_id
        executor._manifest = manifest

        if manifest.plan_snapshot:
            context_data = manifest.plan_snapshot.get("_context_cache")
            if context_data:
                self._context_cache = ContextPacket.model_validate(context_data)
            else:
                tracker.step("Re-gathering context for resume")
                audit = RepoAuditor(root).audit()
                repo_map = RepoMapper(root).map()
                self._context_cache = ContextPacker(root).pack(
                    audit, repo_map, manifest.goal,
                )
                self._classification_cache = TaskClassifier().classify(
                    manifest.goal, self._context_cache,
                )

        queue = TaskQueue(phases_to_run)
        tracker.transition(
            AgentPhase.queued,
            "Execution queue rebuilt",
            f"{len(phases_to_run)} phases remaining",
        )

        while not queue.is_exhausted:
            try:
                phase = queue.next()
            except QueuePaused:
                break
            except QueueExhausted:
                break

            phase_result = await executor.execute_phase(
                phase, plan, queue, tracker, resume_cfg,
                context=self._context_cache,
                classification=self._classification_cache,
            )

            if phase_result.status == YoloPhaseStatus.failed and resume_cfg.pause_on_failure:
                queue.pause(f"Phase '{phase.title}' failed after all retries")
                break

        duration = time.monotonic() - start
        results = queue.results()
        completed = queue.completed_count
        failed = queue.failed_count
        all_ok = failed == 0 and completed == queue.total

        executor.finalize_run(
            success=all_ok,
            duration=duration,
            completed=completed,
            failed=failed,
        )

        return YoloResult(
            plan_id=plan.id,
            goal=manifest.goal,
            repo_path=str(root),
            phase_results=results,
            total_phases=queue.total,
            completed_phases=completed,
            failed_phases=failed,
            success=all_ok,
            error_message=self._aggregate_errors(results) if not all_ok else None,
            duration_seconds=duration,
            agent_status=tracker.summary(),
        )

    @staticmethod
    def _aggregate_errors(results: list[YoloPhaseResult]) -> str:
        errors = []
        for r in results:
            if r.status == YoloPhaseStatus.failed and r.error_history:
                errors.append(f"Phase '{r.title}': {r.error_history[-1]}")
        return "; ".join(errors) if errors else "Unknown failure"
