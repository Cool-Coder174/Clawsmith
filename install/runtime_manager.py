"""Runtime manager — detects, starts, and manages local inference runtimes (Ollama, llama.cpp)."""

from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

from install.models import InstallResult, RuntimeInfo
from orchestrator.logging_setup import get_logger

log = get_logger("install.runtime_manager")

_RUNTIME_HINTS: dict[str, dict[str, str]] = {
    "ollama": {
        "install_command": "winget install Ollama.Ollama",
        "install_url": "https://ollama.com/download",
    },
    "llama.cpp": {
        "install_command": (
            "git clone https://github.com/ggerganov/llama.cpp && cd llama.cpp "
            "&& cmake -B build && cmake --build build --config Release"
        ),
        "install_url": "https://github.com/ggerganov/llama.cpp/releases",
    },
    "llamafile": {
        "install_command": "Download from GitHub releases and place on PATH",
        "install_url": "https://github.com/Mozilla-Ocho/llamafile/releases",
    },
    "lm_studio": {
        "install_command": "Download installer from lmstudio.ai",
        "install_url": "https://lmstudio.ai/",
    },
    "vllm": {
        "install_command": "pip install vllm",
        "install_url": "https://docs.vllm.ai/",
    },
}

_LM_STUDIO_WINDOWS_PATHS = [
    Path.home() / "AppData" / "Local" / "LM-Studio" / "LM Studio.exe",
    Path("C:/Program Files/LM-Studio/LM Studio.exe"),
    Path("C:/Program Files (x86)/LM-Studio/LM Studio.exe"),
]

_OLLAMA_PULL_TIMEOUT = 1800  # 30 minutes


class RuntimeManager:
    """Detect, inspect, and manage local inference runtimes."""

    # ------------------------------------------------------------------
    # Individual runtime checks
    # ------------------------------------------------------------------

    def _check_ollama(self) -> RuntimeInfo:
        exe = shutil.which("ollama")
        if not exe:
            return RuntimeInfo(name="ollama", installed=False, **_RUNTIME_HINTS["ollama"])

        version = ""
        try:
            result = subprocess.run(
                ["ollama", "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            version = result.stdout.strip() or result.stderr.strip()
        except Exception:
            log.debug("Could not determine ollama version")

        return RuntimeInfo(
            name="ollama",
            installed=True,
            version=version,
            path=exe,
            **_RUNTIME_HINTS["ollama"],
        )

    def _check_llama_cpp(self) -> RuntimeInfo:
        for binary in ("llama-server", "llama-cli", "main"):
            exe = shutil.which(binary)
            if exe:
                return RuntimeInfo(
                    name="llama.cpp",
                    installed=True,
                    path=exe,
                    **_RUNTIME_HINTS["llama.cpp"],
                )
        return RuntimeInfo(name="llama.cpp", installed=False, **_RUNTIME_HINTS["llama.cpp"])

    def _check_llamafile(self) -> RuntimeInfo:
        exe = shutil.which("llamafile")
        if exe:
            return RuntimeInfo(
                name="llamafile",
                installed=True,
                path=exe,
                **_RUNTIME_HINTS["llamafile"],
            )
        return RuntimeInfo(name="llamafile", installed=False, **_RUNTIME_HINTS["llamafile"])

    def _check_lm_studio(self) -> RuntimeInfo:
        if platform.system() == "Windows":
            for p in _LM_STUDIO_WINDOWS_PATHS:
                if p.exists():
                    return RuntimeInfo(
                        name="lm_studio",
                        installed=True,
                        path=str(p),
                        **_RUNTIME_HINTS["lm_studio"],
                    )
        return RuntimeInfo(name="lm_studio", installed=False, **_RUNTIME_HINTS["lm_studio"])

    def _check_vllm(self) -> RuntimeInfo:
        try:
            result = subprocess.run(
                ["pip", "show", "vllm"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                version = ""
                for line in result.stdout.splitlines():
                    if line.lower().startswith("version:"):
                        version = line.split(":", 1)[1].strip()
                        break
                return RuntimeInfo(
                    name="vllm",
                    installed=True,
                    version=version,
                    **_RUNTIME_HINTS["vllm"],
                )
        except Exception:
            log.debug("Could not query pip for vllm")
        return RuntimeInfo(name="vllm", installed=False, **_RUNTIME_HINTS["vllm"])

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    _CHECKERS = {
        "ollama": "_check_ollama",
        "llama.cpp": "_check_llama_cpp",
        "llamafile": "_check_llamafile",
        "lm_studio": "_check_lm_studio",
        "vllm": "_check_vllm",
    }

    def check_runtime(self, runtime_name: str) -> RuntimeInfo:
        """Check if a runtime is installed and get its info."""
        checker_name = self._CHECKERS.get(runtime_name)
        if checker_name is None:
            log.warning("Unknown runtime: %s", runtime_name)
            return RuntimeInfo(name=runtime_name, installed=False)
        checker = getattr(self, checker_name)
        info: RuntimeInfo = checker()
        log.info("Runtime %-12s installed=%s  path=%s", info.name, info.installed, info.path)
        return info

    def check_all_runtimes(self) -> list[RuntimeInfo]:
        """Check all supported runtimes."""
        return [self.check_runtime(name) for name in self._CHECKERS]

    def install_runtime_hint(self, runtime_name: str) -> str:
        """Return human-readable install instructions for a runtime."""
        info = self.check_runtime(runtime_name)
        if info.installed:
            return f"{runtime_name} is already installed at {info.path}"

        parts: list[str] = [f"{runtime_name} is not installed."]
        hint = _RUNTIME_HINTS.get(runtime_name, {})
        if hint.get("install_command"):
            parts.append(f"  Install: {hint['install_command']}")
        if hint.get("install_url"):
            parts.append(f"  Download: {hint['install_url']}")
        return "\n".join(parts)

    def pull_model_via_ollama(self, model_name: str) -> InstallResult:
        """Pull a model using ``ollama pull`` (synchronous subprocess)."""
        info = self.check_runtime("ollama")
        if not info.installed:
            return InstallResult(
                success=False,
                model_id=model_name,
                runtime="ollama",
                install_path="",
                error="Ollama is not installed. " + self.install_runtime_hint("ollama"),
            )

        log.info("Pulling model %s via ollama …", model_name)
        try:
            result = subprocess.run(
                ["ollama", "pull", model_name],
                capture_output=True,
                text=True,
                timeout=_OLLAMA_PULL_TIMEOUT,
            )
            if result.returncode != 0:
                stderr = result.stderr.strip()
                log.error("ollama pull failed: %s", stderr)
                return InstallResult(
                    success=False,
                    model_id=model_name,
                    runtime="ollama",
                    install_path="",
                    error=f"ollama pull failed (exit {result.returncode}): {stderr}",
                )

            log.info("ollama pull succeeded for %s", model_name)
            return InstallResult(
                success=True,
                model_id=model_name,
                runtime="ollama",
                install_path="(managed by ollama)",
                notes=result.stdout.strip(),
            )

        except subprocess.TimeoutExpired:
            log.error("ollama pull timed out after %ds", _OLLAMA_PULL_TIMEOUT)
            return InstallResult(
                success=False,
                model_id=model_name,
                runtime="ollama",
                install_path="",
                error=f"ollama pull timed out after {_OLLAMA_PULL_TIMEOUT}s",
            )
        except Exception as exc:
            log.exception("Unexpected error during ollama pull")
            return InstallResult(
                success=False,
                model_id=model_name,
                runtime="ollama",
                install_path="",
                error=str(exc),
            )
