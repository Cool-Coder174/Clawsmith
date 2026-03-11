"""Base adapter interface for CLI agent runtimes."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from agents.capabilities import AgentCapability


@dataclass(frozen=True)
class DetectionResult:
    """Result of probing a local machine for a specific agent CLI."""

    found: bool
    executable_path: str | None = None
    version: str | None = None
    confidence: float = 0.0
    notes: str = ""


@dataclass(frozen=True)
class InvocationSpec:
    """A fully resolved command ready for subprocess execution."""

    args: list[str]
    env_overrides: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    timeout_seconds: int = 300


@dataclass(frozen=True)
class AgentRunResult:
    """Parsed result from an agent CLI invocation."""

    exit_code: int
    stdout: str
    stderr: str
    success: bool
    agent_id: str
    error_message: str | None = None


class AgentAdapter(ABC):
    """Abstract adapter that every supported agent CLI must implement."""

    @property
    @abstractmethod
    def agent_id(self) -> str:
        """Stable machine-readable identifier, e.g. 'cursor', 'claude_code'."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name, e.g. 'Cursor Agent'."""
        ...

    @property
    @abstractmethod
    def executable_names(self) -> list[str]:
        """Candidate executable names for PATH detection, e.g. ['cursor', 'cursor.exe']."""
        ...

    @property
    @abstractmethod
    def version_commands(self) -> list[list[str]]:
        """Commands to run to detect version, e.g. [['cursor', '--version']]."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> frozenset[AgentCapability]:
        """Declared capabilities of this agent CLI."""
        ...

    @property
    def installation_hint(self) -> str:
        """Human-readable hint for installing this CLI."""
        return f"Install {self.display_name} and ensure it is on PATH."

    @property
    def supports_headless(self) -> bool:
        return AgentCapability.headless_prompt in self.capabilities

    @property
    def supports_model_switching(self) -> bool:
        return AgentCapability.model_switching in self.capabilities

    @property
    def supports_json_output(self) -> bool:
        return AgentCapability.json_output in self.capabilities

    @property
    def supports_mcp(self) -> bool:
        return AgentCapability.mcp_client in self.capabilities

    @property
    def supports_acp(self) -> bool:
        return AgentCapability.acp_client in self.capabilities

    @abstractmethod
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
        """Build a concrete invocation command for this agent."""
        ...

    @abstractmethod
    def parse_result(self, exit_code: int, stdout: str, stderr: str) -> AgentRunResult:
        """Parse raw subprocess output into a structured result."""
        ...

    def validate_availability(self, detection: DetectionResult) -> bool:
        """Return True if the detection result indicates the agent is usable."""
        return detection.found and detection.confidence >= 0.5
