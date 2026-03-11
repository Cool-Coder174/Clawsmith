"""Adapter for the Cursor Agent CLI."""

from __future__ import annotations

import os
from pathlib import Path

from agents.base import AgentAdapter, AgentRunResult, DetectionResult, InvocationSpec
from agents.capabilities import AgentCapability


class CursorAdapter(AgentAdapter):

    @property
    def agent_id(self) -> str:
        return "cursor"

    @property
    def display_name(self) -> str:
        return "Cursor Agent"

    @property
    def executable_names(self) -> list[str]:
        return ["cursor", "cursor.exe"]

    @property
    def version_commands(self) -> list[list[str]]:
        return [["cursor", "--version"]]

    @property
    def capabilities(self) -> frozenset[AgentCapability]:
        return frozenset({
            AgentCapability.interactive_chat,
            AgentCapability.headless_prompt,
            AgentCapability.file_editing,
            AgentCapability.shell_execution,
            AgentCapability.mcp_client,
        })

    @property
    def installation_hint(self) -> str:
        return (
            "Install Cursor from https://cursor.sh and set CURSOR_CLI_PATH "
            "in .env, or add it to PATH."
        )

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
        executable = os.environ.get("CURSOR_CLI_PATH", "cursor")
        args = [executable, "--command", prompt, "--no-interactive"]
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

    def validate_availability(self, detection: DetectionResult) -> bool:
        if os.environ.get("CURSOR_CLI_PATH"):
            return Path(os.environ["CURSOR_CLI_PATH"]).exists()
        return super().validate_availability(detection)
