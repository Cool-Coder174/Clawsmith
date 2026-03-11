"""Builds rich, phase-specific prompts for CLI agent execution.

Each phase prompt is a self-contained instruction document that includes:
- Overall user goal
- Current phase name and index
- Phase objective
- Repository architecture context
- Files and directories in scope
- Constraints and guardrails
- Expected output / success conditions
- Previous error context (for retries)
"""

from __future__ import annotations

from orchestrator.logging_setup import get_logger
from orchestrator.schemas import (
    ContextPacket,
    TaskClassification,
    YoloPhase,
    YoloPlan,
)

logger = get_logger("prompt_builder")


class PhasePromptBuilder:
    """Constructs phase-specific prompts for CLI agent execution."""

    def build(
        self,
        phase: YoloPhase,
        plan: YoloPlan,
        context: ContextPacket | None = None,
        classification: TaskClassification | None = None,
        *,
        attempt: int = 1,
        last_error: str | None = None,
    ) -> str:
        sections: list[str] = []

        sections.append(self._header(phase, plan))
        sections.append(self._goal_section(plan.goal))
        sections.append(self._phase_objective(phase))
        sections.append(self._scope_section(phase))

        if context:
            sections.append(self._architecture_section(context))
            sections.append(self._files_section(context, phase))
            sections.append(self._build_test_section(context))

        sections.append(self._acceptance_section(phase))
        sections.append(self._constraints_section(context, phase))

        if last_error and attempt > 1:
            sections.append(self._retry_section(last_error, attempt))

        sections.append(self._footer(phase, plan))

        prompt = "\n\n".join(s for s in sections if s)
        logger.info(
            "Built prompt for phase %d/%d '%s' (attempt %d, %d chars)",
            phase.index + 1, len(plan.phases), phase.title,
            attempt, len(prompt),
        )
        return prompt

    @staticmethod
    def _header(phase: YoloPhase, plan: YoloPlan) -> str:
        return (
            f"# Phase {phase.index + 1} of {len(plan.phases)}: {phase.title}\n"
            f"**Task type:** {phase.task_type.value}  \n"
            f"**Complexity:** {phase.estimated_complexity:.0%}  \n"
            f"**Plan ID:** {plan.id}"
        )

    @staticmethod
    def _goal_section(goal: str) -> str:
        return (
            "## Overall Goal\n"
            f"{goal}"
        )

    @staticmethod
    def _phase_objective(phase: YoloPhase) -> str:
        return (
            "## Phase Objective\n"
            f"{phase.objective}\n\n"
            "Focus exclusively on this phase's objective. Do not attempt work "
            "belonging to other phases."
        )

    @staticmethod
    def _scope_section(phase: YoloPhase) -> str:
        if not phase.files_in_scope:
            return "## Files in Scope\nNo specific files targeted — use your judgment."
        file_list = "\n".join(f"- `{f}`" for f in phase.files_in_scope)
        return (
            "## Files in Scope\n"
            "Limit changes to these files unless creating new ones is necessary:\n"
            f"{file_list}"
        )

    @staticmethod
    def _architecture_section(context: ContextPacket) -> str:
        if not context.architecture_summary:
            return ""
        return (
            "## Repository Architecture\n"
            f"{context.architecture_summary}"
        )

    @staticmethod
    def _files_section(
        context: ContextPacket,
        phase: YoloPhase,
    ) -> str:
        if not context.relevant_files:
            return ""

        scope_set = set(phase.files_in_scope) if phase.files_in_scope else set()
        relevant = {}
        for path, content in context.relevant_files.items():
            if not scope_set or path in scope_set:
                relevant[path] = content

        if not relevant:
            return ""

        blocks: list[str] = []
        for path, content in list(relevant.items())[:10]:
            blocks.append(f"### `{path}`\n```\n{content}\n```")

        return (
            "## Relevant File Contents\n"
            + "\n\n".join(blocks)
        )

    @staticmethod
    def _build_test_section(context: ContextPacket) -> str:
        if not context.build_test_commands:
            return ""
        cmds = "\n".join(f"- `{c}`" for c in context.build_test_commands)
        return (
            "## Build & Test Commands\n"
            "Run these to verify your changes:\n"
            f"{cmds}"
        )

    @staticmethod
    def _acceptance_section(phase: YoloPhase) -> str:
        if not phase.acceptance_criteria:
            return (
                "## Success Criteria\n"
                "- The phase objective is fully met\n"
                "- No new build or test errors introduced"
            )
        criteria = "\n".join(f"- {c}" for c in phase.acceptance_criteria)
        return (
            "## Success Criteria\n"
            f"{criteria}"
        )

    @staticmethod
    def _constraints_section(
        context: ContextPacket | None,
        phase: YoloPhase,
    ) -> str:
        lines = [
            "Follow the repository's existing coding conventions",
            "Do not modify files outside the stated scope unless necessary",
            "Do not introduce new dependencies without justification",
            "Keep changes minimal and focused on the phase objective",
        ]
        if context and context.constraints:
            lines.extend(context.constraints)
        constraints = "\n".join(f"- {c}" for c in lines)
        return (
            "## Constraints\n"
            f"{constraints}"
        )

    @staticmethod
    def _retry_section(last_error: str, attempt: int) -> str:
        return (
            "## Previous Attempt Failed\n"
            f"**Attempt {attempt - 1} failed with the following error:**\n\n"
            f"```\n{last_error}\n```\n\n"
            "Fix the error described above while still completing the phase objective. "
            "Do not repeat the same mistake."
        )

    @staticmethod
    def _footer(phase: YoloPhase, plan: YoloPlan) -> str:
        deps = ""
        if phase.depends_on:
            deps = (
                f"\n**Dependencies:** This phase depends on completion of "
                f"phase(s): {', '.join(phase.depends_on)}"
            )
        return (
            "---\n"
            f"*ClawSmith phase execution — "
            f"phase {phase.index + 1}/{len(plan.phases)}*"
            f"{deps}"
        )
