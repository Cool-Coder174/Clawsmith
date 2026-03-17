"""Phase executor — orchestrates per-phase CLI agent execution.

Replaces the old ``OrchestrationPipeline().run()`` call inside
``YoloEngine._execute_phase``.  For each phase:

1. Generate the phase-specific prompt via ``PhasePromptBuilder``
2. Hand it to an ``ExecutionBackend`` (default: ``CliAgentBackend``)
3. Capture stdout, stderr, exit code, and timing
4. Run verification if enabled
5. Handle retry / pause / fail transitions
6. Log everything via ``PhaseRunLogger``
7. Persist a ``RunManifest`` for resume support
"""

from __future__ import annotations

import time
from pathlib import Path

from execution.backend import BackendConfig, ExecutionBackend
from execution.cli_agent import CliAgentBackend
from execution.models import (
    PhaseExecStatus,
    PhaseExecutionResult,
    RunManifest,
)
from execution.prompt_builder import PhasePromptBuilder
from execution.run_logger import PhaseRunLogger
from orchestrator.agent_status import AgentPhase, StatusTracker, VerifyStage
from orchestrator.logging_setup import get_logger
from orchestrator.schemas import (
    ContextPacket,
    PipelineResult,
    TaskClassification,
    YoloConfig,
    YoloPhase,
    YoloPhaseResult,
    YoloPhaseStatus,
    YoloPlan,
)
from orchestrator.task_queue import TaskQueue

logger = get_logger("phase_executor")


class PhaseExecutor:
    """Executes YOLO phases through a pluggable CLI backend.

    Usage::

        executor = PhaseExecutor(repo_path="/my/repo")
        result = await executor.execute_phase(
            phase, plan, queue, tracker, config,
            context=context, classification=classification,
        )
    """

    def __init__(
        self,
        repo_path: str | Path,
        *,
        backend: ExecutionBackend | None = None,
        backend_config: BackendConfig | None = None,
        prompt_builder: PhasePromptBuilder | None = None,
        run_logger: PhaseRunLogger | None = None,
        run_id: str | None = None,
    ) -> None:
        self._root = Path(repo_path).resolve()
        self._prompt_builder = prompt_builder or PhasePromptBuilder()

        bc = backend_config or BackendConfig(
            working_directory=str(self._root),
        )
        self._backend = backend or CliAgentBackend(config=bc)
        self._run_logger = run_logger or PhaseRunLogger(
            base_dir=self._root / "logs" / "runs",
        )
        self._run_id = run_id
        self._manifest: RunManifest | None = None

    @property
    def backend(self) -> ExecutionBackend:
        return self._backend

    def init_run(
        self,
        run_id: str,
        goal: str,
        plan: YoloPlan,
        config: YoloConfig,
    ) -> RunManifest:
        """Initialize a new execution run with manifest and log directory."""
        self._run_id = run_id

        self._run_logger.init_run(
            run_id=run_id,
            goal=goal,
            repo_path=str(self._root),
            total_phases=len(plan.phases),
            backend_id=self._backend.backend_id,
        )

        self._manifest = RunManifest(
            run_id=run_id,
            goal=goal,
            repo_path=str(self._root),
            backend_id=self._backend.backend_id,
            total_phases=len(plan.phases),
            plan_snapshot=plan.model_dump(),
            config_snapshot=config.model_dump(),
        )
        manifest_dir = self._root / "logs" / "runs"
        self._manifest.save(manifest_dir)
        return self._manifest

    async def execute_phase(
        self,
        phase: YoloPhase,
        plan: YoloPlan,
        queue: TaskQueue,
        tracker: StatusTracker,
        config: YoloConfig,
        *,
        context: ContextPacket | None = None,
        classification: TaskClassification | None = None,
    ) -> YoloPhaseResult:
        """Execute a single phase with retry support.

        Returns a ``YoloPhaseResult`` compatible with the existing queue/YOLO
        result aggregation.
        """
        phase_start = time.monotonic()
        phase_num = phase.index + 1
        attempt = 0
        last_error: str | None = None
        last_review_findings: list[dict] | None = None

        while attempt <= config.max_retries:
            attempt += 1
            tracker.set_yolo_progress(phase_num, queue.total, phase.title, attempt)

            if attempt == 1:
                tracker.transition(
                    AgentPhase.executing,
                    f"Phase {phase_num}/{queue.total}: {phase.title}",
                    f"backend={self._backend.backend_id}",
                )
            else:
                tracker.transition(
                    AgentPhase.retrying,
                    f"Retrying phase {phase_num}/{queue.total} (attempt {attempt})",
                    last_error or "",
                )

            # 1. Generate prompt
            tracker.step("Generating phase prompt", phase.title)
            prompt = self._prompt_builder.build(
                phase, plan,
                context=context,
                classification=classification,
                attempt=attempt,
                last_error=last_error,
                review_findings=last_review_findings,
            )

            # 2. Execute via backend
            tracker.step("Executing via CLI agent", self._backend.display_name)
            exec_result = await self._backend.execute_phase(
                prompt,
                phase_id=phase.id,
                phase_index=phase.index,
                phase_title=phase.title,
                working_directory=str(self._root),
                timeout_seconds=config.timeout_per_phase,
            )
            exec_result.attempt = attempt
            exec_result.retry_count = attempt - 1

            # 3. Log the phase
            if self._run_id:
                self._run_logger.log_phase(self._run_id, exec_result)

            # 4. Verify
            tracker.transition(
                AgentPhase.verifying,
                f"Verifying phase {phase_num}/{queue.total}",
            )
            self._verify(exec_result, tracker, phase=phase, plan=plan)

            # 5. Evaluate result
            if exec_result.success and exec_result.verification_passed is not False:
                duration = time.monotonic() - phase_start
                pipeline_result = self._to_pipeline_result(exec_result, phase)
                yolo_result = queue.complete(phase, pipeline_result, duration)

                self._update_manifest(phase, yolo_result)
                return yolo_result

            # Phase failed — capture error and review findings for retry
            error_msg = exec_result.error_message or "Phase execution failed"
            if exec_result.verification_passed is False:
                error_msg = (
                    f"Verification failed: {exec_result.verification_detail}"
                )
            last_error = error_msg
            last_review_findings = exec_result.metadata.get("review_comments")

            can_retry = attempt <= config.max_retries
            if not can_retry:
                duration = time.monotonic() - phase_start
                pipeline_result = self._to_pipeline_result(exec_result, phase)
                yolo_result = queue.fail(phase, last_error, can_retry=False)
                yolo_result.pipeline_result = pipeline_result
                yolo_result.duration_seconds = duration
                self._update_manifest(phase, yolo_result)
                return yolo_result

            queue.fail(phase, last_error, can_retry=True)
            _ = queue.next()

        duration = time.monotonic() - phase_start
        yolo_result = queue.fail(
            phase, last_error or "Max retries exceeded", can_retry=False,
        )
        yolo_result.duration_seconds = duration
        self._update_manifest(phase, yolo_result)
        return yolo_result

    def finalize_run(
        self,
        success: bool,
        duration: float,
        completed: int,
        failed: int,
        error: str | None = None,
    ) -> None:
        """Finalize logging and persist the manifest."""
        if self._run_id:
            self._run_logger.finalize_run(
                self._run_id, success, duration, completed, failed, error,
            )
        if self._manifest:
            self._manifest.is_complete = success and failed == 0
            self._manifest.is_failed = failed > 0
            self._manifest.failure_reason = error
            self._manifest.save(self._root / "logs" / "runs")

        self._backend.cleanup()

    def _verify(
        self,
        result: PhaseExecutionResult,
        tracker: StatusTracker,
        *,
        phase: YoloPhase | None = None,
        plan: YoloPlan | None = None,
    ) -> None:
        """Run verification checks on the execution result.

        Performs three layers of checks:
        1. Exit code and build error detection
        2. Compile/syntax error scanning in stdout/stderr
        3. Diff-vs-plan verification (file scope and acceptance criteria)
        """
        tracker.verify(VerifyStage.build, "Checking exit code")

        if result.exit_code != 0:
            result.verification_passed = False
            result.verification_detail = (
                f"Non-zero exit code: {result.exit_code}"
            )
            tracker.verify(
                VerifyStage.fix_errors,
                "Build errors detected",
                f"exit_code={result.exit_code}",
            )
            if result.stderr:
                preview = result.stderr[:300].strip()
                tracker.verify(VerifyStage.fix_errors, "Error output", preview)
            return

        tracker.verify(VerifyStage.build, "Exit code OK")

        tracker.verify(VerifyStage.compile_check, "Checking for compile errors")
        stderr_lower = (result.stderr or "").lower()
        stdout_lower = (result.stdout or "").lower()
        combined = stderr_lower + stdout_lower

        compile_markers = (
            "syntaxerror", "compileerror", "typeerror",
            "error ts", "traceback", "fatal error",
        )
        has_errors = any(m in combined for m in compile_markers)

        if has_errors:
            result.verification_passed = False
            result.verification_detail = "Compile/syntax errors detected in output"
            tracker.verify(
                VerifyStage.fix_errors, "Compile errors found in output",
            )
            return

        tracker.verify(VerifyStage.compile_check, "No compile errors")

        tracker.verify(VerifyStage.compare_main, "Checking for merge conflicts")
        full_output = (result.stdout or "") + (result.stderr or "")
        conflict_markers = ("<<<<<<< ", "======= ", ">>>>>>> ")
        has_conflicts = any(m in full_output for m in conflict_markers)

        if has_conflicts:
            result.verification_passed = False
            result.verification_detail = "Merge conflict markers detected"
            tracker.verify(
                VerifyStage.fix_conflicts, "Merge conflicts detected",
            )
            return

        tracker.verify(VerifyStage.compare_main, "No conflicts")

        # Diff-vs-plan verification
        if phase and phase.files_in_scope:
            tracker.verify(VerifyStage.done, "Running diff-vs-plan checks")
            try:
                from orchestrator.verifier import PlanVerifier

                report = PlanVerifier().verify_phase(
                    phase_index=phase.index,
                    phase_title=phase.title,
                    plan_id=plan.id if plan else "",
                    expected_files=phase.files_in_scope,
                    acceptance_criteria=phase.acceptance_criteria,
                    repo_path=str(self._root),
                )

                result.metadata["review_comments"] = report.to_findings_list()
                result.metadata["changed_files"] = report.changed_files
                result.metadata["verification_score"] = report.score

                if not report.passed:
                    findings_summary = "; ".join(
                        c.one_line() for c in report.comments[:3]
                    )
                    result.verification_passed = False
                    result.verification_detail = (
                        f"Diff-vs-plan check failed (score={report.score:.0%}): "
                        f"{findings_summary}"
                    )
                    tracker.verify(
                        VerifyStage.done,
                        "Diff-vs-plan issues found",
                        f"score={report.score:.0%}, "
                        f"critical={report.critical_count}, "
                        f"major={report.major_count}",
                    )
                    return
            except Exception as exc:
                logger.warning("Diff-vs-plan verification failed: %s", exc)

        tracker.verify(VerifyStage.done, "Verification complete")
        result.verification_passed = True
        result.verification_detail = "All checks passed"

    def _to_pipeline_result(
        self,
        exec_result: PhaseExecutionResult,
        phase: YoloPhase,
    ) -> PipelineResult:
        """Convert a PhaseExecutionResult into a PipelineResult for compatibility."""
        from orchestrator.schemas import ExecutionResult

        execution = ExecutionResult(
            job_id=exec_result.phase_id,
            exit_code=exec_result.exit_code,
            stdout=exec_result.stdout,
            stderr=exec_result.stderr,
            duration_seconds=exec_result.duration_seconds,
            success=exec_result.success,
            error_message=exec_result.error_message,
            agent_used=exec_result.backend_id,
        )

        return PipelineResult(
            task_description=phase.objective,
            repo_path=str(self._root),
            generated_prompt=exec_result.prompt_generated,
            execution_result=execution,
            success=exec_result.success,
            error_message=exec_result.error_message,
            duration_seconds=exec_result.duration_seconds,
        )

    def _update_manifest(
        self,
        phase: YoloPhase,
        result: YoloPhaseResult,
    ) -> None:
        """Update the run manifest after a phase completes or fails."""
        if not self._manifest:
            return

        if result.status == YoloPhaseStatus.completed:
            self._manifest.last_completed_index = max(
                self._manifest.last_completed_index, phase.index,
            )

        self._manifest.phase_results = [
            r for r in self._manifest.phase_results
            if r.phase_id != phase.id
        ]
        self._manifest.phase_results.append(
            PhaseExecutionResult(
                phase_id=phase.id,
                phase_index=phase.index,
                title=phase.title,
                status=(
                    PhaseExecStatus.completed
                    if result.status == YoloPhaseStatus.completed
                    else PhaseExecStatus.failed
                ),
                attempt=result.attempts,
            )
        )

        manifest_dir = self._root / "logs" / "runs"
        self._manifest.save(manifest_dir)
