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
"""

from __future__ import annotations

import asyncio
import os
import platform
import shutil
import tempfile
import time
from pathlib import Path

from execution.backend import BackendConfig, ExecutionBackend
from execution.models import PhaseExecStatus, PhaseExecutionResult
from orchestrator.logging_setup import get_logger

logger = get_logger("cli_agent_backend")

_IS_WINDOWS = platform.system() == "Windows"


class CliAgentBackend(ExecutionBackend):
    """Runs phases via ``agent chat`` with prompt delivered through an env var.

    On Windows PowerShell the invocation is::

        $env:CLAWSMITH_PROMPT = "<prompt>"
        agent chat "$env:CLAWSMITH_PROMPT"

    On POSIX shells::

        CLAWSMITH_PROMPT="<prompt>" agent chat "$CLAWSMITH_PROMPT"
    """

    def __init__(
        self,
        config: BackendConfig | None = None,
        *,
        agent_command: str = "agent",
        agent_subcommand: str = "chat",
    ) -> None:
        self._config = config or BackendConfig()
        self._agent_cmd = agent_command
        self._agent_sub = agent_subcommand
        self._temp_files: list[Path] = []

    @property
    def backend_id(self) -> str:
        return "cli_agent"

    @property
    def display_name(self) -> str:
        return "CLI Agent (agent chat)"

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

        if use_file:
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
        return shutil.which(self._agent_cmd) is not None

    def cleanup(self) -> None:
        for f in self._temp_files:
            try:
                if f.exists():
                    f.unlink()
            except OSError:
                pass
        self._temp_files.clear()

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
        return [self._agent_cmd, self._agent_sub, env_ref]

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
