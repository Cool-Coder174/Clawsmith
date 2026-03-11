"""OpenClaw gateway adapter — bridges ClawSmith with the OpenClaw platform.

This is the primary integration seam between ClawSmith and OpenClaw.  It
handles:

  - **Inbound tasks**: ``forward_task()`` accepts a task (from the webhook
    receiver or MCP tool) and runs it through the pipeline.
  - **Skill registration**: ``register_as_skill()`` generates SKILL.md and
    optionally pushes the manifest to the OpenClaw gateway.
  - **Status callbacks**: optionally reports progress back to OpenClaw via
    the ``OpenClawClient``.
  - **Skill manifest**: ``build_skill_manifest()`` produces a dict suitable
    for OpenClaw's ``/skills/register`` endpoint.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from orchestrator.logging_setup import get_logger
from orchestrator.schemas import PipelineResult

if TYPE_CHECKING:
    from orchestrator.pipeline import OrchestrationPipeline
    from providers.openclaw_client import OpenClawClient

logger = get_logger("openclaw_adapter")

OPENCLAW_TOOL_DEFINITIONS: list[dict[str, str]] = [
    {
        "name": "repo_audit",
        "description": "Audit a repository for languages, frameworks, CI, and tooling.",
        "input": "repo_path: str",
    },
    {
        "name": "repo_map",
        "description": "Generate a directory-tree map with entrypoints.",
        "input": "repo_path: str, max_lines: int",
    },
    {
        "name": "repo_pack_context",
        "description": "Audit, map, and pack repository context for a task.",
        "input": "repo_path: str, task_description: str, file_list: list[str]|None",
    },
    {
        "name": "route_pick_model",
        "description": "Classify a task and route it to the best model tier.",
        "input": "task_description: str, context_json: str|None",
    },
    {
        "name": "cost_estimate",
        "description": "Estimate cost across all model tiers.",
        "input": "task_description: str, context_size_tokens: int",
    },
    {
        "name": "agent_run_job",
        "description": "Parse a JobSpec from JSON and execute it via agent CLI.",
        "input": "job_spec_json: str",
    },
    {
        "name": "agent_run_bat",
        "description": "Execute a .bat file within the workspace.",
        "input": "bat_path: str, timeout: int",
    },
    {
        "name": "detect_agent_clis",
        "description": "Detect installed agent CLIs and return capability matrix.",
        "input": "",
    },
    {
        "name": "build_run",
        "description": "Detect and run build/install commands.",
        "input": "repo_path: str, ecosystem: str|None",
    },
    {
        "name": "tests_run",
        "description": "Detect and run test commands.",
        "input": "repo_path: str, ecosystem: str|None",
    },
    {
        "name": "git_create_worktree",
        "description": "Create a git worktree with a new branch.",
        "input": "repo_path: str, branch_name: str, worktree_path: str",
    },
    {
        "name": "logs_read_recent",
        "description": "Read the last N lines from the ClawSmith log.",
        "input": "lines: int",
    },
    {
        "name": "prompts_generate_task_prompt",
        "description": "Generate a structured task prompt from repo context.",
        "input": "task_description: str, repo_path: str",
    },
    {
        "name": "machine_profile",
        "description": "Get the machine hardware profile (CPU, RAM, GPU, storage).",
        "input": "",
    },
    {
        "name": "recommend_models",
        "description": "Get LLM bundle recommendations based on hardware profile.",
        "input": "intent: str",
    },
    {
        "name": "installed_models",
        "description": "List all locally installed LLM models.",
        "input": "",
    },
    {
        "name": "workspace_graph",
        "description": "Get the workspace graph of linked repos and their dependencies.",
        "input": "",
    },
    {
        "name": "scope_contract",
        "description": "Get or create a scope contract for a task.",
        "input": "task_description: str, primary_repo: str",
    },
    {
        "name": "scope_check",
        "description": "Check if a file or repo is in scope for the current task.",
        "input": "contract_id: str, path: str",
    },
    {
        "name": "mutation_propose",
        "description": "Propose a configuration mutation through the guarded system.",
        "input": "mutation_type: str, reason: str, target: str, changes: dict",
    },
    {
        "name": "mutation_status",
        "description": "Get the status of a mutation proposal.",
        "input": "proposal_id: str",
    },
    {
        "name": "memory_sync",
        "description": "Sync hardware profile and preferences to persistent memory.",
        "input": "",
    },
    {
        "name": "openclaw_forward_task",
        "description": "Forward a task through the full ClawSmith pipeline.",
        "input": "task: str, repo_path: str, dry_run: bool",
    },
    {
        "name": "shared_providers",
        "description": "List LLM providers and models available for shared use.",
        "input": "",
    },
    {
        "name": "shared_complete",
        "description": "Run an LLM completion through ClawSmith's shared providers.",
        "input": "prompt: str, tier: str, system_prompt: str, max_tokens: int, temperature: float",
    },
]


class OpenClawAdapter:
    """Adapter that lets OpenClaw route tasks into ClawSmith's pipeline."""

    def __init__(
        self,
        pipeline: OrchestrationPipeline | None = None,
        client: OpenClawClient | None = None,
    ) -> None:
        if pipeline is not None:
            self.pipeline = pipeline
        else:
            from orchestrator.pipeline import OrchestrationPipeline
            self.pipeline = OrchestrationPipeline()
        self._client = client

    def _get_client(self) -> OpenClawClient | None:
        """Lazily create a client from config if not provided."""
        if self._client is not None:
            return self._client
        try:
            from providers.openclaw_client import get_client
            client = get_client()
            if client._gateway_url:
                self._client = client
                return client
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # Skill registration
    # ------------------------------------------------------------------

    def build_skill_manifest(self) -> dict[str, Any]:
        """Build a skill manifest dict suitable for OpenClaw registration."""
        import os

        from config.config_loader import get_config

        cfg = get_config()
        oc = cfg.openclaw
        endpoint = f"http://{cfg.mcp_server.host}:{cfg.mcp_server.port}/sse"
        webhook_url = (
            f"http://{oc.webhook_host}:{oc.webhook_port}/webhook/task"
        )
        complete_url = (
            f"http://{oc.webhook_host}:{oc.webhook_port}/webhook/complete"
        )

        capabilities = [
            "hardware_discovery",
            "model_recommendation",
            "model_inventory",
            "workspace_graph",
            "scope_engine",
            "guarded_mutation",
            "memory_sync",
            "task_forwarding",
        ]
        if oc.share_api_keys or oc.share_local_models:
            capabilities.append("shared_llm_provider")

        manifest: dict[str, Any] = {
            "name": oc.skill_name,
            "description": (
                "AI-powered local code orchestration with model routing "
                "and controlled execution"
            ),
            "version": "0.1.0",
            "mcp_endpoint": endpoint,
            "transport": cfg.mcp_server.transport,
            "webhook_endpoint": webhook_url,
            "tools": [
                {"name": t["name"], "description": t["description"], "input": t["input"]}
                for t in OPENCLAW_TOOL_DEFINITIONS
            ],
            "capabilities": capabilities,
            "env_required": [
                "CLAWSMITH_CONFIG_PATH",
                "OPENAI_API_KEY",
            ],
        }

        shared: dict[str, Any] = {
            "api_keys_shared": oc.share_api_keys,
            "local_models_shared": oc.share_local_models,
            "complete_endpoint": complete_url if (oc.share_api_keys or oc.share_local_models) else None,
        }

        if oc.share_api_keys:
            shared["api_providers"] = [
                name for env_key, name in [
                    ("OPENAI_API_KEY", "openai"),
                    ("ANTHROPIC_API_KEY", "anthropic"),
                    ("OPENROUTER_API_KEY", "openrouter"),
                ]
                if os.environ.get(env_key)
            ]
            shared["tiers"] = [
                {
                    "tier": tier_name,
                    "provider": getattr(cfg.models, tier_name).provider,
                    "model": getattr(cfg.models, tier_name).model_name,
                    "is_local": getattr(cfg.models, tier_name).provider == "ollama",
                }
                for tier_name in ("local_router", "local_code", "premium", "prompt_polisher")
            ]

        manifest["shared_providers"] = shared
        return manifest

    def register_as_skill(self, output_path: Path | None = None) -> Path:
        """Generate a SKILL.md file documenting ClawSmith's MCP tools.

        Returns the path where the file was written.
        """
        manifest = self.build_skill_manifest()

        if output_path is None:
            output_path = Path("SKILL.md")

        lines: list[str] = [
            "# ClawSmith — OpenClaw Skill Registration",
            "",
            manifest["description"],
            "",
            "## MCP Endpoint",
            "",
            f"- **URL:** `{manifest['mcp_endpoint']}`",
            f"- **Transport:** `{manifest['transport']}`",
            "",
            "## Webhook Endpoint",
            "",
            f"- **URL:** `{manifest['webhook_endpoint']}`",
            "- **Auth:** HMAC-SHA256 via `X-OpenClaw-Signature` header",
            "",
            "## Available Tools",
            "",
        ]
        for tool in OPENCLAW_TOOL_DEFINITIONS:
            lines.append(f"### `{tool['name']}`")
            lines.append(f"{tool['description']}")
            if tool["input"]:
                lines.append(f"- **Input:** `{tool['input']}`")
            lines.append("")

        shared = manifest.get("shared_providers", {})
        if shared.get("api_keys_shared") or shared.get("local_models_shared"):
            lines.extend([
                "## Shared LLM Providers",
                "",
                "ClawSmith shares its API keys and local models with OpenClaw by default.",
                "OpenClaw can send completion requests through ClawSmith without needing",
                "its own provider credentials.",
                "",
            ])
            if shared.get("complete_endpoint"):
                lines.append(f"- **Completion endpoint:** `{shared['complete_endpoint']}`")
                lines.append("- **MCP tool:** `shared_complete`")
                lines.append("- **Discovery:** `shared_providers`")
                lines.append("")
            if shared.get("api_providers"):
                lines.append(f"- **API providers:** {', '.join(shared['api_providers'])}")
            if shared.get("tiers"):
                lines.append("- **Available tiers:**")
                for t in shared["tiers"]:
                    local_tag = " (local)" if t["is_local"] else ""
                    lines.append(f"  - `{t['tier']}` — {t['model']}{local_tag}")
            lines.append("")

        lines.extend([
            "## Capabilities",
            "",
            "- **Hardware Discovery:** Detect CPU, RAM, GPU, and storage.",
            "- **Model Recommendation:** Suggest optimal LLM bundles based on hardware.",
            "- **Model Inventory:** List locally installed models across runtimes.",
            "- **Workspace Graph:** Map linked repos and dependency relationships.",
            "- **Scope Engine:** Create and enforce scope contracts for multi-repo tasks.",
            "- **Guarded Mutation:** Propose, review, and apply config changes with rollback.",
            "- **Memory Sync:** Persist hardware profiles and preferences.",
            "- **Task Forwarding:** Accept tasks via webhook and run through the pipeline.",
            "- **Shared LLM Provider:** Route completions through ClawSmith's API keys and local models.",
            "",
            "## Required Environment Variables",
            "",
            "- `CLAWSMITH_CONFIG_PATH` — path to ClawSmith config YAML",
            "- `OPENAI_API_KEY` (or `ANTHROPIC_API_KEY` / `OPENROUTER_API_KEY`)",
            "- `OPENCLAW_WEBHOOK_SECRET` — HMAC secret for webhook auth",
            "- `OPENCLAW_API_KEY` — API key for OpenClaw gateway communication",
            "",
            "## Setup",
            "",
            "```bash",
            "# Start ClawSmith MCP server + webhook receiver",
            "clawsmith start --webhook",
            "",
            "# Or start separately",
            "clawsmith start-server",
            "clawsmith openclaw webhook",
            "```",
            "",
        ])

        output_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Skill file written to %s", output_path)
        return output_path

    async def register_with_gateway(self) -> dict[str, Any] | None:
        """Push the skill manifest to the OpenClaw gateway via HTTP.

        Returns the gateway response dict, or None if no gateway is configured.
        """
        client = self._get_client()
        if not client:
            logger.info("No OpenClaw gateway configured — skipping remote registration")
            return None

        manifest = self.build_skill_manifest()
        try:
            result = await client.register_skill(manifest)
            logger.info("Registered with OpenClaw gateway: %s", result)
            return result
        except Exception as exc:
            logger.warning("Remote skill registration failed: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Task forwarding
    # ------------------------------------------------------------------

    async def forward_task(
        self,
        task: str,
        repo_path: str = ".",
        dry_run: bool = False,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Accept a task and run it through the pipeline.

        If a client is available and a ``task_id`` is provided, status
        updates are sent back to OpenClaw during execution.
        """
        client = self._get_client()

        if client and task_id:
            await client.report_task_status(task_id, "running", progress_pct=0.0)

        result = await self.pipeline.run(task, repo_path, dry_run)
        formatted = self.format_response(result)

        if task_id:
            formatted["task_id"] = task_id

        if client and task_id:
            if result.success:
                await client.report_task_complete(task_id, formatted)
            else:
                await client.report_task_failed(
                    task_id, result.error_message or "Pipeline failed"
                )

        return formatted

    @staticmethod
    def format_response(result: PipelineResult) -> dict[str, Any]:
        """Convert a PipelineResult to a flat dict suitable for OpenClaw."""
        response: dict[str, Any] = {
            "success": result.success,
            "model_used": None,
            "tier": None,
            "cost_usd": None,
            "prompt_preview": result.generated_prompt[:300] if result.generated_prompt else "",
            "execution_exit_code": None,
            "error": result.error_message,
            "duration_seconds": result.duration_seconds,
            "dry_run": result.dry_run,
        }

        if result.routing_decision:
            response["model_used"] = result.routing_decision.model_name
            response["tier"] = result.routing_decision.selected_tier.value
            response["cost_usd"] = result.routing_decision.estimated_cost_usd
            if result.routing_decision.agent_target:
                response["agent_used"] = result.routing_decision.agent_target

        if result.execution_result:
            response["execution_exit_code"] = result.execution_result.exit_code

        return response
