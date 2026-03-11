"""Centralized prompt generator — single source of truth for task-prompt construction.

Both the MCP server and orchestrator flows should delegate here so that
prompt format, ordering, and future Phase-6 polishing improvements are
applied consistently.
"""

from __future__ import annotations

from orchestrator.schemas import ContextPacket, RoutingDecision


class PromptGenerator:
    """Builds a structured task prompt from packed context and a routing decision."""

    def generate(
        self,
        task_description: str,
        context: ContextPacket,
        routing: RoutingDecision,
    ) -> str:
        """Assemble a markdown task prompt from the supplied artifacts.

        Parameters
        ----------
        task_description:
            Free-text description of the task the user wants performed.
        context:
            A fully-packed ``ContextPacket`` produced by the context-packer.
        routing:
            The ``RoutingDecision`` returned by the model router.

        Returns
        -------
        str
            A ready-to-use markdown prompt.
        """
        relevant_files_section = "\n".join(
            f"- `{path}`" for path in context.relevant_files
        )
        build_test_section = "\n".join(
            f"- {cmd}" for cmd in context.build_test_commands
        )
        constraints_section = "\n".join(
            f"- {c}" for c in context.constraints
        )
        acceptance_criteria = "\n".join(
            f"- {step}" for step in context.recommended_steps
        )

        return (
            f"## Task\n{task_description}\n\n"
            f"## Repository Architecture\n{context.architecture_summary}\n\n"
            f"## Relevant Files\n"
            f"{relevant_files_section or '_No specific files identified._'}\n\n"
            f"## Build & Test Commands\n"
            f"{build_test_section or '_No build/test commands detected._'}\n\n"
            f"## Routing Decision\n"
            f"- **Model tier:** {routing.selected_tier.value}\n"
            f"- **Model:** {routing.model_name}\n"
            f"- **Provider:** {routing.provider}\n"
            f"- **Reasoning:** {routing.reasoning}\n"
            f"- **Confidence:** {routing.confidence_score:.2f}\n\n"
            f"## Acceptance Criteria\n"
            f"{acceptance_criteria or '_No automated acceptance steps detected._'}\n\n"
            f"## Constraints\n"
            f"{constraints_section or '_No specific constraints._'}"
        )
