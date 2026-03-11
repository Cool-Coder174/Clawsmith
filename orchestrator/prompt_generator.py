"""Orchestrator-level prompt generator with polishing support.

Distinct from ``prompts/generator.py`` — this version is routing-agnostic,
builds richer markdown prompts from ContextPacket + TaskClassification,
and adds an LLM-powered polish step for premium-tier tasks.
"""

from __future__ import annotations

from orchestrator.logging_setup import get_logger
from orchestrator.schemas import ContextPacket, TaskClassification
from providers.base import ProviderError

logger = get_logger("prompt_generator")


class PromptGenerator:
    """Builds and optionally polishes structured task prompts."""

    def generate_task_prompt(
        self,
        task: str,
        context: ContextPacket,
        classification: TaskClassification,
    ) -> str:
        relevant_files_list = "\n".join(
            f"- `{path}`" for path in context.relevant_files
        )

        file_contents_blocks: list[str] = []
        for filename, content in context.relevant_files.items():
            file_contents_blocks.append(
                f"### `{filename}`\n```\n{content}\n```"
            )
        file_contents_section = "\n\n".join(file_contents_blocks)

        build_test_section = "\n".join(
            f"- `{cmd}`" for cmd in context.build_test_commands
        )

        acceptance_criteria = "\n".join(
            f"- {step}" for step in context.recommended_steps
        )

        expected_scope = (
            f"Estimated files to touch: ~{classification.files_likely_touched}. "
            f"Relevant files identified: {len(context.relevant_files)}."
        )

        constraints_section = "\n".join(
            f"- {c}" for c in context.constraints
        )

        return (
            "## System Role\n"
            "You are an expert software engineer. Implement the requested changes "
            "precisely, following the repo's existing conventions.\n\n"
            f"## Task Objective\n{task}\n\n"
            f"## Repository Architecture\n{context.architecture_summary}\n\n"
            f"## Relevant Files\n"
            f"{relevant_files_list or '_No specific files identified._'}\n\n"
            f"## File Contents\n"
            f"{file_contents_section or '_No file contents available._'}\n\n"
            f"## Build & Test Commands\n"
            f"{build_test_section or '_No build/test commands detected._'}\n\n"
            f"## Acceptance Criteria\n"
            f"{acceptance_criteria or '_No automated acceptance steps detected._'}\n\n"
            f"## Expected Scope\n{expected_scope}\n\n"
            f"## Constraints\n"
            f"{constraints_section or '_No specific constraints._'}"
        )

    async def polish_prompt(
        self,
        draft_prompt: str,
        context: ContextPacket,
    ) -> str:
        """Refine a draft prompt via the prompt_polisher LLM provider.

        Falls back to the original draft on any ProviderError.
        """
        from providers.registry import get_registry

        system = (
            "You are a prompt-engineering specialist. Refine the following draft prompt "
            "for clarity, specificity, and actionability. Preserve all technical details "
            "and file references. Make the instructions unambiguous.\n\n"
            f"Repository context:\n{context.architecture_summary}\n\n"
            "Constraints to honour:\n"
            + "\n".join(f"- {c}" for c in context.constraints)
        )

        try:
            provider = get_registry().get_provider("prompt_polisher")
            result = await provider.complete(
                prompt=draft_prompt,
                system_prompt=system,
                max_tokens=2048,
                temperature=0.3,
            )
            return result.text
        except (ProviderError, Exception) as exc:
            logger.warning("Prompt polishing failed, using original draft: %s", exc)
            return draft_prompt
