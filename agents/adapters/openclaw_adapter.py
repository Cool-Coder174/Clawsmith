"""Adapter for OpenClaw — ACP/gateway bridge, not a local headless CLI.

OpenClaw is fundamentally different from local agent CLIs like Cursor/Claude/Gemini.
It acts as a gateway or ACP (Agent Communication Protocol) bridge that forwards tasks
to remote agent runtimes. This adapter represents that honestly instead of pretending
it behaves like a local executable.
"""

from __future__ import annotations

from pathlib import Path

from agents.base import AgentAdapter, AgentRunResult, DetectionResult, InvocationSpec
from agents.capabilities import AgentCapability


class OpenClawAdapter(AgentAdapter):
    """ACP bridge adapter — delegates to an OpenClaw gateway endpoint.

    Unlike local CLI adapters, OpenClaw invocations may be forwarded over
    HTTP/ACP rather than launched as a local subprocess.  The ``build_invocation``
    method produces a stub command; actual execution may be handled by the
    OpenClaw integration layer in ``providers/openclaw_adapter.py``.
    """

    @property
    def agent_id(self) -> str:
        return "openclaw"

    @property
    def display_name(self) -> str:
        return "OpenClaw (ACP Bridge)"

    @property
    def executable_names(self) -> list[str]:
        return ["openclaw", "openclaw.exe"]

    @property
    def version_commands(self) -> list[list[str]]:
        return [["openclaw", "--version"]]

    @property
    def capabilities(self) -> frozenset[AgentCapability]:
        return frozenset({
            AgentCapability.headless_prompt,
            AgentCapability.acp_client,
            AgentCapability.structured_output,
            AgentCapability.json_output,
        })

    @property
    def installation_hint(self) -> str:
        return (
            "OpenClaw functions as a gateway/ACP bridge. Configure the endpoint "
            "in config/settings.yaml under the openclaw section."
        )

    @property
    def is_gateway(self) -> bool:
        """OpenClaw is a gateway adapter, not a local CLI agent."""
        return True

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
        args = ["openclaw", "run", "--prompt", prompt]
        if model:
            args.extend(["--model", model])
        if output_format:
            args.extend(["--output-format", output_format])
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
        return detection.found
