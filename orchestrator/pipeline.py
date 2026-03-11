"""Full 10-step orchestration pipeline with generic agent CLI support."""

from __future__ import annotations

import time
from pathlib import Path

from agents.registry import get_agent_registry
from agents.router import AgentNotAvailableError, AgentRouter
from config.config_loader import get_config
from jobs.executor import JobExecutor
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
    ) -> PipelineResult:
        start = time.monotonic()
        root = Path(repo_path).resolve()

        if not root.exists() or not root.is_dir():
            return PipelineResult(
                task_description=task_description,
                repo_path=str(root),
                dry_run=dry_run,
                success=False,
                error_message=f"Repository path does not exist or is not a directory: {root}",
                duration_seconds=time.monotonic() - start,
            )

        try:
            # 1. Audit
            logger.info("Step 1/10: Auditing repository at %s", root)
            audit_report = RepoAuditor(root).audit()

            # 2. Map
            logger.info("Step 2/10: Mapping repository structure")
            repo_map = RepoMapper(root).map()

            # 3. Pack context
            logger.info("Step 3/10: Packing context")
            context_packet = ContextPacker(root).pack(audit_report, repo_map, task_description)

            # 4. Classify
            logger.info("Step 4/10: Classifying task")
            classification = TaskClassifier().classify(task_description, context_packet)

            # 5. Route to model tier
            logger.info("Step 5/10: Routing to model tier")
            routing_decision = ModelRouter().route_task(classification)

            # 5b. Route to agent CLI
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
                logger.info("Step 5b: Selected agent CLI: %s", agent_id)
            except AgentNotAvailableError:
                logger.info("Step 5b: No agent CLI available; proceeding without agent invocation")

            prompt_gen = PromptGenerator()

            # 6. Polish (conditional — premium tier only)
            polished_preamble: str | None = None
            if routing_decision.selected_tier == ModelTier.premium:
                logger.info("Step 6/10: Polishing prompt (premium tier)")
                draft = (
                    f"Task: {task_description}\n\n"
                    f"Architecture:\n{context_packet.architecture_summary}"
                )
                polished_preamble = await prompt_gen.polish_prompt(draft, context_packet)
            else:
                logger.info("Step 6/10: Skipping polish (non-premium tier)")

            # 7. Generate final prompt
            logger.info("Step 7/10: Generating final prompt")
            generated_prompt = prompt_gen.generate_task_prompt(
                task_description, context_packet, classification
            )
            if polished_preamble:
                generated_prompt = (
                    f"## Polished Preamble\n{polished_preamble}\n\n{generated_prompt}"
                )

            # 8. Dispatch to provider
            completion = None
            if not dry_run:
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
                except ProviderError as exc:
                    logger.error("Provider dispatch failed: %s", exc)
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
                    )
            else:
                logger.info("Step 8/10: Skipping dispatch (dry-run)")

            # 9. Execute job
            execution_result = None
            completion_text = completion.get("text", "") if completion else ""
            has_code = any(indicator in completion_text for indicator in _CODE_INDICATORS)

            if completion and has_code and not dry_run:
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
                logger.info("Step 9/10: Skipping execution (dry-run mode)")
            else:
                logger.info("Step 9/10: Skipping execution (no code changes detected)")

            # 10. Return result
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
                success=True,
                duration_seconds=time.monotonic() - start,
            )

        except Exception as exc:
            logger.exception("Pipeline failed: %s", exc)
            return PipelineResult(
                task_description=task_description,
                repo_path=str(root),
                dry_run=dry_run,
                success=False,
                error_message=str(exc),
                duration_seconds=time.monotonic() - start,
            )
