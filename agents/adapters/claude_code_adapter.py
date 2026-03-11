"""Adapter for the Claude Code CLI (claude)."""

from __future__ import annotations

from pathlib import Path

from agents.base import AgentAdapter, AgentRunResult, InvocationSpec
from agents.capabilities import AgentCapability


class ClaudeCodeAdapter(AgentAdapter):

    @property
    def agent_id(self) -> str:
        return "claude_code"

    @property
    def display_name(self) -> str:
        return "Claude Code"

    @property
    def executable_names(self) -> list[str]:
        return ["claude", "claude.exe"]

    @property
    def version_commands(self) -> list[list[str]]:
        return [["claude", "--version"], ["claude", "-v"]]

    @property
    def capabilities(self) -> frozenset[AgentCapability]:
        return frozenset({
            AgentCapability.interactive_chat,
            AgentCapability.headless_prompt,
            AgentCapability.structured_output,
            AgentCapability.model_switching,
            AgentCapability.file_editing,
            AgentCapability.shell_execution,
            AgentCapability.sandbox_mode,
            AgentCapability.approval_mode,
            AgentCapability.resume_session,
            AgentCapability.json_output,
            AgentCapability.mcp_client,
        })

    @property
    def installation_hint(self) -> str:
        return "Install Claude Code: npm install -g @anthropic-ai/claude-code"

    def build_invocation(
        self,
        prompt: str,
        *,
        working_directory: str = ".",
        model: str | None = None,
        output_format: str | None = None,
        approval_mode: str | None = None,
        extra_flags: list[str] | None = None,
        env_overrides: dict[str, str] | None = None,
        timeout_seconds: int = 300,
        prompt_file: Path | None = None,
    ) -> InvocationSpec:
        args = ["claude", "-p", prompt, "--verbose"]
        if model:
            args.extend(["--model", model])
        if output_format and output_format == "json":
            args.extend(["--output-format", "json"])
        if approval_mode:
            args.extend(["--allowedTools", approval_mode])
        if extra_flags:
            args.extend(extra_flags)
        return InvocationSpec(
            args=args,
            env_overrides=env_overrides or {},
            cwd=working_directory,
            timeout_seconds=timeout_seconds,
        )

    def parse_result(self, exit_code: int, stdout: str, stderr: str) -> AgentRunResult:
        return AgentRunResult(
            exit_code=exit_code,
            stdout=stdout,
            stderr=stderr,
            success=exit_code == 0,
            agent_id=self.agent_id,
            error_message=stderr.strip() if exit_code != 0 else None,
        )
