"""CLI agent execution backend.

Executes each phase by:
1. Setting ``CLAWSMITH_PROMPT`` to the generated prompt text
2. Running ``agent chat "$env:CLAWSMITH_PROMPT"`` (or reading from a temp file
   for large prompts that would exceed env-var limits)
3. Capturing stdout, stderr, exit code, and timing

Large-prompt fallback
---------------------
Windows environment variables have practical limits (~32 KB). When the prompt
exceeds ``prompt_file_fallback_bytes``, the backend writes it to a temp file
and adjusts the invocation to read from that file instead.  The temp file is
cleaned up after execution.

Adapter support
---------------
When constructed with an ``AgentAdapter``, the backend delegates command
construction to ``adapter.build_invocation()`` so that each agent CLI
(Cursor, Claude Code, Gemini, etc.) gets its native invocation pattern.
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

from execution.backend import BackendConfig, ExecutionBackend
from execution.models import PhaseExecStatus, PhaseExecutionResult
from orchestrator.logging_setup import get_logger

if TYPE_CHECKING:
    from agents.base import AgentAdapter

logger = get_logger("cli_agent_backend")

_IS_WINDOWS = platform.system() == "Windows"


class CliAgentBackend(ExecutionBackend):
    """Runs phases via a detected CLI agent with prompt delivery.

    When constructed with an ``adapter``, the backend uses
    ``adapter.build_invocation()`` for correct per-agent invocation.
    Without an adapter it falls back to the classic
    ``{agent_command} {agent_subcommand} $env:CLAWSMITH_PROMPT`` pattern.
    """

    def __init__(
        self,
        config: BackendConfig | None = None,
        *,
        agent_command: str = "agent",
        agent_subcommand: str = "chat",
        adapter: AgentAdapter | None = None,
    ) -> None:
        self._config = config or BackendConfig()
        self._agent_cmd = agent_command
        self._agent_sub = agent_subcommand
        self._adapter = adapter
        self._temp_files: list[Path] = []

    @property
    def backend_id(self) -> str:
        if self._adapter:
            return f"cli_agent:{self._adapter.agent_id}"
        return "cli_agent"

    @property
    def display_name(self) -> str:
        if self._adapter:
            return f"CLI Agent ({self._adapter.display_name})"
        return f"CLI Agent ({self._agent_cmd} {self._agent_sub})"

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
        cwd = working_directory or self._config.working_directory
        timeout = timeout_seconds or self._config.timeout_seconds
        env_name = self._config.env_var_name
        prompt_bytes = len(prompt.encode("utf-8"))

        result = PhaseExecutionResult(
            phase_id=phase_id,
            phase_index=phase_index,
            title=phase_title,
            backend_id=self.backend_id,
            prompt_generated=prompt,
            start_time=time.time(),
        )

        use_file = prompt_bytes > self._config.prompt_file_fallback_bytes
        prompt_file_path: Path | None = None
        env = self._build_env(env_overrides)

        if self._adapter:
            args, env = self._build_adapter_args(prompt, cwd, env, use_file, phase_index)
            if use_file:
                prompt_file_path = self._temp_files[-1] if self._temp_files else None
                if prompt_file_path:
                    result.prompt_file = str(prompt_file_path)
        elif use_file:
            prompt_file_path = self._write_prompt_file(prompt, phase_index)
            result.prompt_file = str(prompt_file_path)
            env[env_name] = str(prompt_file_path)
            args = self._build_file_args(prompt_file_path)
            logger.info(
                "Phase %d prompt too large for env var (%d bytes), using file: %s",
                phase_index, prompt_bytes, prompt_file_path,
            )
        else:
            env[env_name] = prompt
            args = self._build_args()

        command_str = " ".join(args)
        result.command_executed = command_str
        result.status = PhaseExecStatus.executing

        logger.info(
            "Executing phase %d/%s via %s: %s",
            phase_index, phase_title, self.backend_id, command_str,
        )

        try:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=env,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                result.status = PhaseExecStatus.failed
                result.exit_code = -1
                result.error_message = f"Phase timed out after {timeout}s"
                result.stderr = f"TIMEOUT: process killed after {timeout} seconds"
                logger.error("Phase %d timed out after %ds", phase_index, timeout)
                return self._finalize(result)

            result.exit_code = proc.returncode or 0
            result.stdout = stdout_bytes.decode("utf-8", errors="replace")
            result.stderr = stderr_bytes.decode("utf-8", errors="replace")

            if result.exit_code == 0:
                result.status = PhaseExecStatus.completed
            else:
                result.status = PhaseExecStatus.failed
                result.error_message = (
                    f"Agent exited with code {result.exit_code}"
                )
                logger.warning(
                    "Phase %d failed with exit code %d",
                    phase_index, result.exit_code,
                )

        except FileNotFoundError:
            result.status = PhaseExecStatus.failed
            result.exit_code = 127
            result.error_message = (
                f"Command not found: '{self._agent_cmd}'. "
                f"Ensure the agent CLI is installed and on PATH."
            )
            logger.error("Agent command not found: %s", self._agent_cmd)

        except OSError as exc:
            result.status = PhaseExecStatus.failed
            result.exit_code = -1
            result.error_message = f"OS error launching agent: {exc}"
            logger.exception("OS error in phase %d", phase_index)

        finally:
            if prompt_file_path and prompt_file_path.exists():
                try:
                    prompt_file_path.unlink()
                except OSError:
                    pass

        return self._finalize(result)

    async def health_check(self) -> bool:
        cmd = self._agent_cmd
        if self._adapter:
            cmd = self._adapter.executable_names[0]
        return shutil.which(cmd) is not None

    def cleanup(self) -> None:
        for f in self._temp_files:
            try:
                if f.exists():
                    f.unlink()
            except OSError:
                pass
        self._temp_files.clear()

    def _build_adapter_args(
        self,
        prompt: str,
        cwd: str,
        env: dict[str, str],
        use_file: bool,
        phase_index: int,
    ) -> tuple[list[str], dict[str, str]]:
        """Build invocation args using the adapter's native CLI pattern.

        Replaces the adapter's default executable name (e.g. ``"agent"``)
        with the actual detected path (e.g. ``agent.CMD``), and wraps
        ``.cmd``/``.bat`` scripts through ``cmd.exe /c`` on Windows so
        ``create_subprocess_exec`` can launch them.
        """
        assert self._adapter is not None

        prompt_file: Path | None = None
        if use_file:
            prompt_file = self._write_prompt_file(prompt, phase_index)
            logger.info(
                "Phase %d prompt too large, using file: %s",
                phase_index, prompt_file,
            )

        invocation = self._adapter.build_invocation(
            prompt,
            working_directory=cwd,
            timeout_seconds=self._config.timeout_seconds,
            prompt_file=prompt_file,
        )

        args = list(invocation.args)

        if args and self._agent_cmd and self._agent_cmd != args[0]:
            args[0] = self._agent_cmd

        if _IS_WINDOWS and args and args[0].lower().endswith((".cmd", ".bat")):
            args = ["cmd.exe", "/c"] + args

        env.update(invocation.env_overrides)
        return args, env

    def _build_env(self, overrides: dict[str, str] | None = None) -> dict[str, str]:
        env = os.environ.copy()
        env.update(self._config.extra_env)
        if overrides:
            env.update(overrides)
        return env

    def _build_args(self) -> list[str]:
        env_ref = (
            f"$env:{self._config.env_var_name}"
            if _IS_WINDOWS
            else f"${self._config.env_var_name}"
        )
        args = [self._agent_cmd, self._agent_sub, env_ref]
        if _IS_WINDOWS and self._agent_cmd.lower().endswith((".cmd", ".bat")):
            args = ["cmd.exe", "/c"] + args
        return args

    def _build_file_args(self, prompt_file: Path) -> list[str]:
        if _IS_WINDOWS:
            read_expr = f"$(Get-Content -Raw '{prompt_file}')"
            return ["powershell", "-NoProfile", "-Command",
                    f"{self._agent_cmd} {self._agent_sub} {read_expr}"]
        else:
            return [self._agent_cmd, self._agent_sub,
                    f"$(cat '{prompt_file}')"]

    def _write_prompt_file(self, prompt: str, phase_index: int) -> Path:
        temp_dir = self._config.temp_dir or tempfile.gettempdir()
        fd, path_str = tempfile.mkstemp(
            prefix=f"clawsmith_phase_{phase_index}_",
            suffix=".md",
            dir=temp_dir,
        )
        path = Path(path_str)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(prompt)
        self._temp_files.append(path)
        return path

    @staticmethod
    def _finalize(result: PhaseExecutionResult) -> PhaseExecutionResult:
        result.end_time = time.time()
        result.duration_seconds = result.end_time - result.start_time
        return result
