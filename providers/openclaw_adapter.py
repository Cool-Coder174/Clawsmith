"""OpenClaw integration adapter — bridges ClawSmith with the OpenClaw gateway."""

from __future__ import annotations

from pathlib import Path

from orchestrator.logging_setup import get_logger
from orchestrator.schemas import PipelineResult

logger = get_logger("openclaw_adapter")

OPENCLAW_TOOL_DEFINITIONS: list[dict] = [
    {"name": "repo_audit", "description": "Audit a repository for languages, frameworks, CI, and tooling.", "input": "repo_path: str"},
    {"name": "repo_map", "description": "Generate a directory-tree map with entrypoints and important files.", "input": "repo_path: str, max_lines: int"},
    {"name": "repo_pack_context", "description": "Audit, map, and pack repository context for a task.", "input": "repo_path: str, task_description: str, file_list: list[str]|None"},
    {"name": "route_pick_model", "description": "Classify a task and route it to the best model tier.", "input": "task_description: str, context_json: str|None"},
    {"name": "cost_estimate", "description": "Estimate cost of running a task across all model tiers.", "input": "task_description: str, context_size_tokens: int"},
    {"name": "cursor_run_job", "description": "Parse a JobSpec from JSON and execute it.", "input": "job_spec_json: str"},
    {"name": "cursor_run_bat", "description": "Execute a .bat file within the workspace.", "input": "bat_path: str, timeout: int"},
    {"name": "build_run", "description": "Detect and run build/install commands for a repository.", "input": "repo_path: str, ecosystem: str|None"},
    {"name": "tests_run", "description": "Detect and run test commands for a repository.", "input": "repo_path: str, ecosystem: str|None"},
    {"name": "git_create_worktree", "description": "Create a git worktree with a new branch.", "input": "repo_path: str, branch_name: str, worktree_path: str"},
    {"name": "logs_read_recent", "description": "Read the last N lines from the ClawSmith log file.", "input": "lines: int"},
    {"name": "prompts_generate_task_prompt", "description": "Generate a structured task prompt from repo context + routing.", "input": "task_description: str, repo_path: str"},
]


class OpenClawAdapter:
    """Adapter that lets OpenClaw route tasks into ClawSmith's pipeline."""

    def __init__(self, pipeline: object | None = None) -> None:
        if pipeline is not None:
            self.pipeline = pipeline
        else:
            from orchestrator.pipeline import OrchestrationPipeline
            self.pipeline = OrchestrationPipeline()

    def register_as_skill(self, output_path: Path | None = None) -> Path:
        """Generate a SKILL.md file documenting ClawSmith's MCP tools.

        Returns the path where the file was written.
        """
        from config.config_loader import get_config

        if output_path is None:
            output_path = Path("SKILL.md")

        cfg = get_config()
        endpoint = f"http://{cfg.mcp_server.host}:{cfg.mcp_server.port}/sse"

        lines: list[str] = [
            "# ClawSmith — OpenClaw Skill Registration",
            "",
            "AI-powered local code orchestration with model routing and controlled execution.",
            "",
            "## MCP Endpoint",
            "",
            f"- **URL:** `{endpoint}`",
            f"- **Transport:** `{cfg.mcp_server.transport}`",
            "",
            "## Available Tools",
            "",
        ]
        for tool in OPENCLAW_TOOL_DEFINITIONS:
            lines.append(f"### `{tool['name']}`")
            lines.append(f"{tool['description']}")
            lines.append(f"- **Input:** `{tool['input']}`")
            lines.append("")

        lines.extend([
            "## Required Environment Variables",
            "",
            "- `CLAWSMITH_CONFIG_PATH` — path to ClawSmith config YAML",
            "- `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY`)",
            "",
            "## Setup",
            "",
            "```bash",
            "scripts\\\\windows\\\\start_mcp_server.bat",
            "```",
            "",
        ])

        output_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Skill file written to %s", output_path)
        return output_path

    async def forward_task(
        self,
        task: str,
        repo_path: str = ".",
        dry_run: bool = False,
    ) -> dict:
        """Accept a task (e.g. from an OpenClaw webhook) and run it through the pipeline."""
        result = await self.pipeline.run(task, repo_path, dry_run)
        return self.format_response(result)

    @staticmethod
    def format_response(result: PipelineResult) -> dict:
        """Convert a PipelineResult to a flat dict suitable for OpenClaw channel output."""
        response: dict = {
            "success": result.success,
            "model_used": None,
            "tier": None,
            "cost_usd": None,
            "prompt_preview": result.generated_prompt[:300] if result.generated_prompt else "",
            "execution_exit_code": None,
            "error": result.error_message,
        }

        if result.routing_decision:
            response["model_used"] = result.routing_decision.model_name
            response["tier"] = result.routing_decision.selected_tier.value
            response["cost_usd"] = result.routing_decision.estimated_cost_usd

        if result.execution_result:
            response["execution_exit_code"] = result.execution_result.exit_code

        return response
