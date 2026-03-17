"""Spec-aware prompt builder — enhances phase prompts with spec detail.

When a GeneratedSpec is available, this builder injects:
- File-level change descriptions from the spec
- Dependencies between files
- Phase-specific acceptance criteria from the spec
- Previous phase summaries for context threading
- Rollback notes for safety

Falls back to the standard PhasePromptBuilder when no spec is provided.
"""

from __future__ import annotations

from orchestrator.logging_setup import get_logger
from orchestrator.schemas import (
    ContextPacket,
    TaskClassification,
    YoloPhase,
    YoloPlan,
)
from orchestrator.spec_generator import GeneratedSpec, SpecPhase, FileChange
from execution.prompt_builder import PhasePromptBuilder

logger = get_logger("spec_prompt_builder")


class SpecPromptBuilder(PhasePromptBuilder):
    """Builds phase prompts enriched with spec-level detail.

    When constructed with a ``GeneratedSpec``, injects file-level
    implementation details, dependencies, and phase context into
    each prompt. Without a spec, behaves identically to the base
    ``PhasePromptBuilder``.
    """

    def __init__(self, spec: GeneratedSpec | None = None) -> None:
        self._spec = spec
        self._phase_summaries: dict[int, str] = {}

    def record_phase_result(self, phase_index: int, summary: str) -> None:
        """Record a summary of a completed phase for context threading."""
        self._phase_summaries[phase_index] = summary

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
        if not self._spec:
            return super().build(
                phase, plan, context, classification,
                attempt=attempt, last_error=last_error,
            )

        sections: list[str] = []

        sections.append(self._header(phase, plan))
        sections.append(self._goal_section(plan.goal))
        sections.append(self._phase_objective(phase))

        # Spec-specific sections
        spec_phase = self._get_spec_phase(phase.index)
        sections.append(self._spec_file_changes(phase, spec_phase))
        sections.append(self._spec_dependencies(spec_phase))

        # Context from previous phases
        if self._phase_summaries:
            sections.append(self._previous_phases_section(phase.index))

        # Standard context sections
        if context:
            sections.append(self._architecture_section(context))
            sections.append(self._files_section(context, phase))
            sections.append(self._build_test_section(context))

        # Spec acceptance criteria override
        sections.append(self._spec_acceptance(phase, spec_phase))
        sections.append(self._constraints_section(context, phase))

        # Rollback notes
        if spec_phase and spec_phase.rollback_notes:
            sections.append(self._rollback_section(spec_phase))

        if last_error and attempt > 1:
            sections.append(self._retry_section(last_error, attempt))

        sections.append(self._footer(phase, plan))

        prompt = "\n\n".join(s for s in sections if s)
        logger.info(
            "Built spec-enhanced prompt for phase %d/%d '%s' (attempt %d, %d chars)",
            phase.index + 1, len(plan.phases), phase.title,
            attempt, len(prompt),
        )
        return prompt

    def _get_spec_phase(self, phase_index: int) -> SpecPhase | None:
        """Get the spec phase matching the current execution phase."""
        if not self._spec or not self._spec.phases:
            return None
        if phase_index < len(self._spec.phases):
            return self._spec.phases[phase_index]
        return None

    def _spec_file_changes(
        self,
        phase: YoloPhase,
        spec_phase: SpecPhase | None,
    ) -> str:
        """Render file-level change details from the spec."""
        changes: list[FileChange] = []

        if spec_phase and spec_phase.file_changes:
            changes = spec_phase.file_changes
        elif self._spec and self._spec.file_changes and phase.index == 0:
            changes = self._spec.file_changes

        if not changes:
            return ""

        lines = ["## Implementation Details (from Spec)"]
        for fc in changes:
            lines.append(f"### `{fc.path}` — {fc.action.upper()}")
            lines.append(fc.description)
            if fc.key_changes:
                lines.append("**Specific changes:**")
                for kc in fc.key_changes:
                    lines.append(f"- {kc}")
            lines.append("")

        return "\n".join(lines)

    @staticmethod
    def _spec_dependencies(spec_phase: SpecPhase | None) -> str:
        """Render file dependencies from the spec phase."""
        if not spec_phase or not spec_phase.file_changes:
            return ""

        deps = []
        for fc in spec_phase.file_changes:
            if fc.dependencies:
                deps.append(f"- `{fc.path}` depends on: {', '.join(f'`{d}`' for d in fc.dependencies)}")

        if not deps:
            return ""

        return "## File Dependencies\n" + "\n".join(deps)

    def _previous_phases_section(self, current_index: int) -> str:
        """Summarize what previous phases accomplished."""
        relevant = {
            idx: summary
            for idx, summary in sorted(self._phase_summaries.items())
            if idx < current_index
        }

        if not relevant:
            return ""

        lines = ["## Previous Phase Context"]
        for idx, summary in relevant.items():
            lines.append(f"**Phase {idx + 1}:** {summary}")
        lines.append("")
        lines.append(
            "Build on the work done in previous phases. Do not redo "
            "what has already been completed."
        )
        return "\n".join(lines)

    def _spec_acceptance(
        self,
        phase: YoloPhase,
        spec_phase: SpecPhase | None,
    ) -> str:
        """Merge spec acceptance criteria with phase criteria."""
        criteria = list(phase.acceptance_criteria)

        if spec_phase and spec_phase.acceptance_criteria:
            for ac in spec_phase.acceptance_criteria:
                if ac not in criteria:
                    criteria.append(ac)

        if not criteria:
            return (
                "## Success Criteria\n"
                "- The phase objective is fully met\n"
                "- No new build or test errors introduced"
            )

        return "## Success Criteria\n" + "\n".join(f"- {c}" for c in criteria)

    @staticmethod
    def _rollback_section(spec_phase: SpecPhase) -> str:
        """Include rollback notes for safety."""
        return (
            "## Rollback Notes\n"
            f"If this phase needs to be reverted: {spec_phase.rollback_notes}"
        )
