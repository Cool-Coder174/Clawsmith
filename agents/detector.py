"""Auto-detect installed agent CLIs on the local machine."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from agents.base import AgentAdapter, DetectionResult
from orchestrator.logging_setup import get_logger

logger = get_logger("agent_detector")

_WINDOWS_COMMON_PATHS: list[str] = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs"),
    os.path.expandvars(r"%PROGRAMFILES%"),
    os.path.expandvars(r"%PROGRAMFILES(X86)%"),
    os.path.expandvars(r"%APPDATA%\npm"),
]


class AgentDetector:
    """Scans the local environment for supported agent CLIs."""

    def __init__(
        self,
        adapters: list[AgentAdapter] | None = None,
        extra_paths: list[str] | None = None,
    ) -> None:
        if adapters is not None:
            self._adapters = adapters
        else:
            self._adapters = _get_builtin_adapters()
        self._extra_paths = extra_paths or []

    def detect_all(self) -> dict[str, DetectionResult]:
        """Probe every registered adapter and return detection results keyed by agent_id."""
        results: dict[str, DetectionResult] = {}
        for adapter in self._adapters:
            results[adapter.agent_id] = self._detect_one(adapter)
        return results

    def detect_one(self, agent_id: str) -> DetectionResult:
        """Detect a single agent by its id."""
        for adapter in self._adapters:
            if adapter.agent_id == agent_id:
                return self._detect_one(adapter)
        return DetectionResult(found=False, notes=f"No adapter registered for '{agent_id}'")

    def _detect_one(self, adapter: AgentAdapter) -> DetectionResult:
        path = self._find_executable(adapter)
        if not path:
            return DetectionResult(
                found=False,
                confidence=0.0,
                notes=f"{adapter.display_name} not found on PATH or common locations.",
            )

        version = self._probe_version(adapter, path)
        confidence = 1.0 if version else 0.7

        return DetectionResult(
            found=True,
            executable_path=path,
            version=version,
            confidence=confidence,
            notes="" if version else "Executable found but version check failed.",
        )

    def _find_executable(self, adapter: AgentAdapter) -> str | None:
        for name in adapter.executable_names:
            found = shutil.which(name)
            if found:
                return found

        for base_dir in self._extra_paths + _WINDOWS_COMMON_PATHS:
            for name in adapter.executable_names:
                candidate = Path(base_dir) / name
                if candidate.exists():
                    return str(candidate)

        env_key = f"{adapter.agent_id.upper()}_CLI_PATH"
        env_val = os.environ.get(env_key)
        if env_val and Path(env_val).exists():
            return env_val

        if adapter.agent_id == "cursor":
            cursor_env = os.environ.get("CURSOR_CLI_PATH")
            if cursor_env and Path(cursor_env).exists():
                return cursor_env
            agent_on_path = shutil.which("agent")
            if agent_on_path:
                return agent_on_path

        return None

    @staticmethod
    def _probe_version(adapter: AgentAdapter, executable_path: str) -> str | None:
        for cmd_template in adapter.version_commands:
            cmd = [executable_path if i == 0 else part for i, part in enumerate(cmd_template)]
            try:
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                output = (result.stdout or result.stderr).strip()
                if output and result.returncode == 0:
                    return output.splitlines()[0][:200]
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
                continue
        return None


def _get_builtin_adapters() -> list[AgentAdapter]:
    from agents.adapters.claude_code_adapter import ClaudeCodeAdapter
    from agents.adapters.cursor_adapter import CursorAdapter
    from agents.adapters.gemini_adapter import GeminiAdapter
    from agents.adapters.openclaw_adapter import OpenClawAdapter

    return [CursorAdapter(), ClaudeCodeAdapter(), GeminiAdapter(), OpenClawAdapter()]
