"""Developer toolchain and AI tooling detection."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys

from pydantic import BaseModel, Field

from orchestrator.logging_setup import get_logger

logger = get_logger("discovery.toolchain")

_IS_WINDOWS = sys.platform == "win32"


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class ToolInfo(BaseModel):
    name: str
    found: bool = False
    version: str = ""
    path: str = ""
    notes: str = ""


class ToolchainReport(BaseModel):
    developer_tools: list[ToolInfo] = Field(default_factory=list)
    ai_tooling: list[ToolInfo] = Field(default_factory=list)
    package_managers: list[ToolInfo] = Field(default_factory=list)
    compilers: list[ToolInfo] = Field(default_factory=list)
    inference_runtimes: list[ToolInfo] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(cmd: list[str] | str, *, timeout: int = 10, shell: bool = False) -> str:
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=shell,
            creationflags=subprocess.CREATE_NO_WINDOW if _IS_WINDOWS else 0,
        )
        out = result.stdout.strip()
        if not out:
            out = result.stderr.strip()
        return out
    except Exception as exc:
        cmd_str = cmd if isinstance(cmd, str) else " ".join(cmd)
        logger.debug("subprocess failed (%s): %s", cmd_str, exc)
        return ""


def _extract_version(text: str) -> str:
    """Pull the first semver-ish string from text."""
    m = re.search(r"(\d+\.\d+[\.\d]*[\w\-]*)", text)
    return m.group(1) if m else text.split()[-1] if text else ""


def _probe(name: str, cmd: list[str], *, exe: str | None = None, notes: str = "") -> ToolInfo:
    """Check if a tool exists and get its version."""
    exe_name = exe or cmd[0]
    path = shutil.which(exe_name) or ""
    if not path:
        return ToolInfo(name=name, found=False, notes=notes)
    raw = _run(cmd)
    version = _extract_version(raw) if raw else ""
    return ToolInfo(name=name, found=True, version=version, path=path, notes=notes)


# ---------------------------------------------------------------------------
# Developer tools
# ---------------------------------------------------------------------------


def _detect_developer_tools() -> list[ToolInfo]:
    tools = [
        _probe("git", ["git", "--version"]),
        _probe("python", [sys.executable, "--version"], exe=sys.executable),
        _probe("node", ["node", "--version"]),
        _probe("rust/cargo", ["cargo", "--version"], exe="cargo"),
    ]
    return tools


# ---------------------------------------------------------------------------
# Package managers
# ---------------------------------------------------------------------------


def _detect_package_managers() -> list[ToolInfo]:
    managers = [
        _probe("pip", [sys.executable, "-m", "pip", "--version"], exe=sys.executable),
        _probe("npm", ["npm", "--version"]),
        _probe("yarn", ["yarn", "--version"]),
        _probe("pnpm", ["pnpm", "--version"]),
        _probe("cargo", ["cargo", "--version"]),
        _probe("dotnet", ["dotnet", "--version"]),
    ]
    return managers


# ---------------------------------------------------------------------------
# Compilers
# ---------------------------------------------------------------------------


def _detect_compilers() -> list[ToolInfo]:
    compilers: list[ToolInfo] = [
        _probe("gcc", ["gcc", "--version"]),
        _probe("clang", ["clang", "--version"]),
    ]

    if _IS_WINDOWS:
        cl_path = shutil.which("cl") or shutil.which("cl.exe")
        if cl_path:
            compilers.append(ToolInfo(
                name="msvc/cl.exe", found=True,
                path=cl_path, notes="Detected on PATH",
            ))
        else:
            compilers.append(ToolInfo(
                name="msvc/cl.exe", found=False,
                notes="Not on PATH; may exist in VS Developer prompt",
            ))

    return compilers


# ---------------------------------------------------------------------------
# AI tooling & inference runtimes
# ---------------------------------------------------------------------------


_AI_TOOLS: list[tuple[str, list[str], str | None]] = [
    ("openclaw", ["openclaw", "--version"], "openclaw"),
    ("cursor-cli", ["cursor", "--version"], "cursor"),
    ("claude-cli", ["claude", "--version"], "claude"),
    ("gemini-cli", ["gemini", "--version"], "gemini"),
]

_INFERENCE_RUNTIMES: list[tuple[str, list[str], str | None, str]] = [
    ("ollama", ["ollama", "--version"], "ollama", ""),
    ("llama.cpp-server", ["llama-server", "--version"],
     "llama-server", "Also check for llama-cpp-server"),
    ("llamafile", ["llamafile", "--version"], "llamafile", ""),
    ("lmstudio", ["lms", "version"], "lms", "LM Studio CLI"),
    ("vllm", [sys.executable, "-m", "vllm", "--version"], sys.executable, "vLLM Python package"),
    ("text-generation-inference",
     ["text-generation-launcher", "--version"],
     "text-generation-launcher", "HuggingFace TGI"),
]


def _detect_ai_tooling() -> list[ToolInfo]:
    tools: list[ToolInfo] = []
    for name, cmd, exe in _AI_TOOLS:
        tools.append(_probe(name, cmd, exe=exe))
    return tools


def _detect_inference_runtimes() -> list[ToolInfo]:
    runtimes: list[ToolInfo] = []
    for name, cmd, exe, notes in _INFERENCE_RUNTIMES:
        runtimes.append(_probe(name, cmd, exe=exe, notes=notes))
    return runtimes


# ---------------------------------------------------------------------------
# Public facade
# ---------------------------------------------------------------------------


def detect_toolchain() -> ToolchainReport:
    """Scan the local environment for developer tools, AI tooling, and runtimes."""
    logger.info("Starting toolchain detection …")

    dev_tools = _detect_developer_tools()
    pkg_managers = _detect_package_managers()
    compilers = _detect_compilers()
    ai_tools = _detect_ai_tooling()
    inference = _detect_inference_runtimes()

    found_dev = [t.name for t in dev_tools if t.found]
    found_ai = [t.name for t in ai_tools if t.found]
    found_rt = [t.name for t in inference if t.found]
    logger.info(
        "Toolchain detection complete: dev=%s, ai=%s, runtimes=%s",
        found_dev,
        found_ai,
        found_rt,
    )

    return ToolchainReport(
        developer_tools=dev_tools,
        ai_tooling=ai_tools,
        package_managers=pkg_managers,
        compilers=compilers,
        inference_runtimes=inference,
    )
