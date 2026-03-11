"""Full 10-step orchestration pipeline with generic agent CLI support.

Integrates ``StatusTracker`` so every caller (CLI, TUI, MCP) gets
real-time lifecycle events:

    deployed -> planning -> executing -> verifying -> complete | failed
"""

from __future__ import annotations

import time
from pathlib import Path

from agents.registry import get_agent_registry
from agents.router import AgentNotAvailableError, AgentRouter
from config.config_loader import get_config
from jobs.executor import JobExecutor
from orchestrator.agent_status import (
    AgentPhase,
    StatusTracker,
    VerifyStage,
)
from orchestrator.logging_setup import get_logger
from orchestrator.prompt_generator import PromptGenerator
from orchestrator.schemas import (
    JobSpec,
    ModelTier,
    PipelineResult,
)
from providers.base import ProviderError
from providers.registry import get_registry
from routing.classifier import TaskClassifier
from routing.router import ModelRouter
from tools.context_packer import ContextPacker
from tools.repo_auditor import RepoAuditor
from tools.repo_mapper import RepoMapper

logger = get_logger("pipeline")

SYSTEM_PROMPT = (
    "You are an expert software engineer. Implement the requested changes "
    "precisely, following the repo's existing conventions."
)

_CODE_INDICATORS = (
    "```", "def ", "class ", "import ", "from ",
    "function ", "const ", "let ", "var ",
)


class OrchestrationPipeline:
    """Runs the full audit -> route -> prompt -> complete -> execute pipeline."""

    async def run(
        self,
        task_description: str,
        repo_path: str,
        dry_run: bool = False,
        agent_target: str | None = None,
        status: StatusTracker | None = None,
    ) -> PipelineResult:
        start = time.monotonic()
        root = Path(repo_path).resolve()
        tracker = status or StatusTracker()

        if not root.exists() or not root.is_dir():
            tracker.fail("Repository path invalid", str(root))
            return PipelineResult(
                task_description=task_description,
                repo_path=str(root),
                dry_run=dry_run,
                success=False,
                error_message=f"Repository path does not exist or is not a directory: {root}",
                duration_seconds=time.monotonic() - start,
                agent_status=tracker.summary(),
            )

        try:
            return await self._run_pipeline(
                task_description, root, dry_run, agent_target, tracker, start,
            )
        except Exception as exc:
            logger.exception("Pipeline failed: %s", exc)
            tracker.fail("Pipeline exception", str(exc))
            return PipelineResult(
                task_description=task_description,
                repo_path=str(root),
                dry_run=dry_run,
                success=False,
                error_message=str(exc),
                duration_seconds=time.monotonic() - start,
                agent_status=tracker.summary(),
            )

    async def _run_pipeline(
        self,
        task_description: str,
        root: Path,
        dry_run: bool,
        agent_target: str | None,
        tracker: StatusTracker,
        start: float,
    ) -> PipelineResult:
        # ── DEPLOYED ──────────────────────────────────────────────
        tracker.transition(
            AgentPhase.deployed,
            "Agent deployed",
            f"Pipeline started for {root.name}",
            repo=str(root),
        )
        logger.info("Agent deployed — pipeline started for %s", root)

        # ── PLANNING ──────────────────────────────────────────────
        tracker.transition(AgentPhase.planning, "Creating plan")

        # 1. Audit
        tracker.step("Auditing repository", str(root))
        logger.info("Step 1/10: Auditing repository at %s", root)
        audit_report = RepoAuditor(root).audit()

        # 2. Map
        tracker.step("Mapping repository structure")
        logger.info("Step 2/10: Mapping repository structure")
        repo_map = RepoMapper(root).map()

        # 3. Pack context
        tracker.step("Packing context")
        logger.info("Step 3/10: Packing context")
        context_packet = ContextPacker(root).pack(audit_report, repo_map, task_description)

        # 4. Classify
        tracker.step("Classifying task")
        logger.info("Step 4/10: Classifying task")
        classification = TaskClassifier().classify(task_description, context_packet)

        # 5. Route to model tier
        tracker.step("Routing to model tier")
        logger.info("Step 5/10: Routing to model tier")
        routing_decision = ModelRouter().route_task(classification)

        # 5b. Route to agent CLI
        agent_id, agent_display_name, agent_invocation = self._resolve_agent(
            root, agent_target, routing_decision, tracker, task_description,
        )

        prompt_gen = PromptGenerator()

        # 6. Polish (premium only)
        polished_preamble: str | None = None
        if routing_decision.selected_tier == ModelTier.premium:
            tracker.step("Polishing prompt", "premium tier")
            logger.info("Step 6/10: Polishing prompt (premium tier)")
            draft = (
                f"Task: {task_description}\n\n"
                f"Architecture:\n{context_packet.architecture_summary}"
            )
            polished_preamble = await prompt_gen.polish_prompt(draft, context_packet)
        else:
            tracker.step("Skipping polish", "non-premium tier")
            logger.info("Step 6/10: Skipping polish (non-premium tier)")

        # 7. Generate final prompt
        tracker.step("Generating final prompt")
        logger.info("Step 7/10: Generating final prompt")
        generated_prompt = prompt_gen.generate_task_prompt(
            task_description, context_packet, classification,
        )
        if polished_preamble:
            generated_prompt = (
                f"## Polished Preamble\n{polished_preamble}\n\n{generated_prompt}"
            )

        # ── EXECUTING ─────────────────────────────────────────────
        tracker.transition(AgentPhase.executing, "Executing task")

        # 8. Dispatch to provider
        completion = None
        if not dry_run:
            tracker.step(
                "Dispatching to provider",
                f"{routing_decision.selected_tier.value} ({routing_decision.model_name})",
            )
            logger.info(
                "Step 8/10: Dispatching to %s (%s)",
                routing_decision.selected_tier.value,
                routing_decision.model_name,
            )
            config = get_config()
            model_cfg = getattr(config.models, routing_decision.selected_tier.value)
            try:
                provider = get_registry().get_provider(routing_decision.selected_tier.value)
                completion_result = await provider.complete(
                    generated_prompt,
                    system_prompt=SYSTEM_PROMPT,
                    max_tokens=model_cfg.max_tokens,
                    temperature=model_cfg.temperature,
                )
                completion = completion_result.model_dump()
                tracker.step("Provider response received")
            except ProviderError as exc:
                logger.error("Provider dispatch failed: %s", exc)
                tracker.fail("Provider dispatch failed", str(exc))
                return PipelineResult(
                    task_description=task_description,
                    repo_path=str(root),
                    audit_report=audit_report.model_dump(),
                    repo_map=repo_map.model_dump(),
                    context_packet=context_packet,
                    classification=classification,
                    routing_decision=routing_decision,
                    generated_prompt=generated_prompt,
                    dry_run=dry_run,
                    success=False,
                    error_message=f"Provider dispatch failed: {exc}",
                    duration_seconds=time.monotonic() - start,
                    agent_status=tracker.summary(),
                )
        else:
            tracker.step("Skipping dispatch", "dry-run")
            logger.info("Step 8/10: Skipping dispatch (dry-run)")

        # 9. Execute job
        execution_result = None
        completion_text = completion.get("text", "") if completion else ""
        has_code = any(indicator in completion_text for indicator in _CODE_INDICATORS)

        if completion and has_code and not dry_run:
            tracker.step("Executing job", f"agent: {agent_id}")
            logger.info("Step 9/10: Executing job via agent '%s'", agent_id)
            config = get_config()
            job_spec = JobSpec(
                task_type=classification.task_type,
                objective=task_description[:200],
                working_directory=str(root),
                prompt=generated_prompt,
                agent_target=agent_id,
                provider_preference=routing_decision.selected_tier,
                timeout_seconds=config.execution.default_timeout,
                dry_run=dry_run,
            )
            execution_result = await JobExecutor().execute(
                job_spec,
                dry_run=dry_run,
                agent_invocation=agent_invocation,
                agent_id=agent_id,
                agent_display_name=agent_display_name,
            )
        elif dry_run:
            tracker.step("Skipping execution", "dry-run mode")
            logger.info("Step 9/10: Skipping execution (dry-run mode)")
        else:
            tracker.step("Skipping execution", "no code changes detected")
            logger.info("Step 9/10: Skipping execution (no code changes detected)")

        # ── VERIFYING ─────────────────────────────────────────────
        tracker.transition(AgentPhase.verifying, "Verifying results")

        if execution_result and not dry_run:
            await self._verify_results(
                root, execution_result, tracker,
            )
        else:
            tracker.verify(VerifyStage.done, "Skipped verification", "no execution result")

        # ── COMPLETE ──────────────────────────────────────────────
        duration = time.monotonic() - start
        success = execution_result.success if execution_result else True

        if success:
            tracker.transition(
                AgentPhase.complete,
                "Pipeline complete",
                f"{duration:.1f}s",
            )
        else:
            error_msg = execution_result.error_message if execution_result else None
            tracker.fail("Execution failed", error_msg or "unknown error")

        logger.info("Step 10/10: Assembling pipeline result")
        return PipelineResult(
            task_description=task_description,
            repo_path=str(root),
            audit_report=audit_report.model_dump(),
            repo_map=repo_map.model_dump(),
            context_packet=context_packet,
            classification=classification,
            routing_decision=routing_decision,
            generated_prompt=generated_prompt,
            completion=completion,
            execution_result=execution_result,
            dry_run=dry_run,
            success=success,
            duration_seconds=duration,
            agent_status=tracker.summary(),
        )

    def _resolve_agent(
        self,
        root: Path,
        agent_target: str | None,
        routing_decision: object,
        tracker: StatusTracker,
        task_description: str,
    ) -> tuple[str, str, str]:
        agent_id = "none"
        agent_display_name = "ClawSmith"
        agent_invocation = ""

        try:
            config = get_config()
            registry = get_agent_registry(auto_detect=config.agents.auto_detect)
            agent_router = AgentRouter(
                registry,
                default_agent=config.agents.default_agent,
                fallback_order=config.agents.fallback_order,
            )
            agent_decision = agent_router.select_agent(
                requested_agent=agent_target,
                needs_headless=True,
            )
            agent_id = agent_decision.agent_id
            agent_display_name = agent_decision.adapter.display_name
            routing_decision.agent_target = agent_id

            invocation_spec = agent_decision.adapter.build_invocation(
                prompt=task_description[:500],
                working_directory=str(root),
                model=None,
                timeout_seconds=config.execution.default_timeout,
            )
            agent_invocation = " ".join(
                f'"{a}"' if " " in a else a for a in invocation_spec.args
            )
            tracker.step("Selected agent CLI", agent_id)
            logger.info("Step 5b: Selected agent CLI: %s", agent_id)
        except AgentNotAvailableError:
            tracker.step("No agent CLI available", "proceeding without agent invocation")
            logger.info("Step 5b: No agent CLI available; proceeding without agent invocation")

        return agent_id, agent_display_name, agent_invocation

    async def _verify_results(
        self,
        root: Path,
        execution_result: object,
        tracker: StatusTracker,
    ) -> None:
        """Run verification sub-stages on the execution result."""
        # Build check
        tracker.verify(VerifyStage.build, "Checking build output")
        if execution_result.exit_code != 0:
            tracker.verify(
                VerifyStage.fix_errors,
                "Build errors detected",
                f"exit_code={execution_result.exit_code}",
            )
            if execution_result.stderr:
                error_preview = execution_result.stderr[:200].strip()
                tracker.verify(
                    VerifyStage.fix_errors,
                    "Error output captured",
                    error_preview,
                )
        else:
            tracker.verify(VerifyStage.build, "Build succeeded")

        # Compile / lint check
        tracker.verify(VerifyStage.compile_check, "Checking for compile errors")
        stderr_lower = (execution_result.stderr or "").lower()
        has_compile_errors = any(
            kw in stderr_lower
            for kw in ("syntaxerror", "compileerror", "typeerror", "error ts")
        )
        if has_compile_errors:
            tracker.verify(VerifyStage.fix_errors, "Compile errors found in output")
        else:
            tracker.verify(VerifyStage.compile_check, "No compile errors")

        # Conflict check (look for merge conflict markers in stdout/stderr)
        tracker.verify(VerifyStage.compare_main, "Checking for merge conflicts")
        combined = (execution_result.stdout or "") + (execution_result.stderr or "")
        has_conflicts = any(
            marker in combined
            for marker in ("<<<<<<< ", "======= ", ">>>>>>> ")
        )
        if has_conflicts:
            tracker.verify(VerifyStage.fix_conflicts, "Merge conflicts detected in output")
        else:
            tracker.verify(VerifyStage.compare_main, "No conflicts detected")

        tracker.verify(VerifyStage.done, "Verification complete")
