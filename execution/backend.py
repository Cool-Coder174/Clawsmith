"""Abstract execution backend interface.

Every backend must implement ``execute_phase`` — the contract for running
a single phase's prompt through an external agent.  Backends are swappable:

- ``CliAgentBackend``  — runs ``agent chat "$env:CLAWSMITH_PROMPT"``
- Future: ``CursorBackend``, ``ClaudeCliBackend``, ``LocalScriptBackend``
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from execution.models import PhaseExecutionResult


@dataclass(frozen=True)
class BackendConfig:
    """Settings passed to every backend at construction time."""

    working_directory: str = "."
    timeout_seconds: int = 600
    env_var_name: str = "CLAWSMITH_PROMPT"
    prompt_file_fallback_bytes: int = 30_000
    temp_dir: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)


class ExecutionBackend(ABC):
    """Abstract interface for executing a phase prompt through an agent."""

    @property
    @abstractmethod
    def backend_id(self) -> str:
        """Machine-readable identifier, e.g. 'cli_agent', 'cursor', 'claude_cli'."""
        ...

    @property
    @abstractmethod
    def display_name(self) -> str:
        """Human-readable name for terminal output."""
        ...

    @abstractmethod
    async def execute_phase(
        self,
        prompt: str,
        *,
        phase_id: str,
        phase_index: int,
        phase_title: str,
        working_directory: str | None = None,
        timeout_seconds: int | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> PhaseExecutionResult:
        """Run a single phase prompt through the backend and return results.

        Implementations must:
        1. Deliver the prompt to the agent (env var, file, stdin, etc.)
        2. Invoke the agent process
        3. Capture stdout, stderr, exit code, and timing
        4. Return a populated ``PhaseExecutionResult``
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Return True if the backend is available and ready."""
        ...

    def cleanup(self) -> None:
        """Optional cleanup hook called after a run completes."""
