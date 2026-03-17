"""Lightweight startup preflight for the ClawSmith chat session.

Runs fast checks to verify that at least one inference path is available,
launches companion services (MCP server, Ollama), and surfaces clear,
actionable guidance when dependencies are missing.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_OLLAMA_HOST = "127.0.0.1"
_OLLAMA_PORT = 11434


@dataclass
class PreflightIssue:
    """A single problem detected during startup checks."""

    severity: str  # "error" | "warning"
    component: str
    message: str
    repair_hint: str = ""
    auto_repairable: bool = False


@dataclass
class PreflightResult:
    """Aggregated startup check report."""

    issues: list[PreflightIssue] = field(default_factory=list)
    ollama_installed: bool = False
    ollama_reachable: bool = False
    has_api_keys: bool = False
    available_tiers: list[str] = field(default_factory=list)
    config_ok: bool = False
    mcp_running: bool = False
    models_missing: list[str] = field(default_factory=list)

    @property
    def can_run_tasks(self) -> bool:
        return bool(self.available_tiers)

    @property
    def healthy(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def _ollama_reachable(timeout: float = 1.0) -> bool:
    """Quick TCP probe to check if ``ollama serve`` is listening."""
    try:
        with socket.create_connection((_OLLAMA_HOST, _OLLAMA_PORT), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def _api_keys_present() -> dict[str, bool]:
    """Return which provider API keys are set in the environment."""
    result: dict[str, bool] = {}
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY"):
        val = os.environ.get(name, "").strip()
        result[name] = bool(val) and val not in ("your-key-here", "sk-...")
    return result


def _ollama_list_models() -> set[str]:
    """Return base names of models available in the local Ollama instance."""
    import concurrent.futures
    import json
    import urllib.request

    def _fetch() -> set[str]:
        url = f"http://{_OLLAMA_HOST}:{_OLLAMA_PORT}/api/tags"
        with urllib.request.urlopen(url, timeout=3) as resp:
            data = json.loads(resp.read())
        names: set[str] = set()
        for m in data.get("models", []):
            full_name = m.get("name", "")
            base = full_name.split(":")[0]
            if base:
                names.add(base)
        return names

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(_fetch).result(timeout=4)
    except Exception:
        return set()


def _required_ollama_models() -> list[str]:
    """Return the bare Ollama model names needed by all Ollama-backed tiers."""
    try:
        from config.config_loader import get_config

        cfg = get_config()
        models: list[str] = []
        for tier in (cfg.models.local_router, cfg.models.local_code,
                     cfg.models.premium, cfg.models.prompt_polisher):
            if tier.provider != "ollama":
                continue
            name = tier.model_name
            if name.startswith("ollama/"):
                name = name[len("ollama/"):]
            if name not in models:
                models.append(name)
        return models
    except Exception:
        return ["mistral", "codellama", "qwen2.5-coder:14b"]


def _config_ok() -> bool:
    try:
        from config.config_loader import load_config

        load_config()
        return True
    except Exception:
        return False


def _ollama_install_hint() -> str:
    system = platform.system()
    if system == "Windows":
        return "winget install Ollama.Ollama"
    if system == "Darwin":
        return "brew install ollama"
    return "curl -fsSL https://ollama.com/install.sh | sh"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


PREFLIGHT_STEP_COUNT = 6


def run_preflight(
    on_step: Callable[[int, str], None] | None = None,
) -> PreflightResult:
    """Execute all startup checks. Designed to complete in <3 seconds.

    *on_step(completed, description)* is called after each check finishes so
    callers can drive a progress bar.
    """
    _n = 0

    def _tick(desc: str) -> None:
        nonlocal _n
        _n += 1
        if on_step is not None:
            on_step(_n, desc)

    try:
        from dotenv import load_dotenv

        load_dotenv(_REPO_ROOT / ".env")
    except Exception:
        pass
    _tick("Environment loaded")

    result = PreflightResult()

    # --- Config -------------------------------------------------------
    result.config_ok = _config_ok()
    if not result.config_ok:
        result.issues.append(
            PreflightIssue(
                severity="error",
                component="Config",
                message="config/settings.yaml is missing or invalid",
                repair_hint="Run: clawsmith onboard",
            )
        )
    _tick("Config validated")

    # --- Ollama -------------------------------------------------------
    result.ollama_installed = _ollama_installed()
    if result.ollama_installed:
        result.ollama_reachable = _ollama_reachable()
        if result.ollama_reachable:
            result.available_tiers.extend(
                ["local_router", "local_code", "premium", "prompt_polisher"]
            )
        else:
            result.issues.append(
                PreflightIssue(
                    severity="warning",
                    component="Ollama",
                    message="Ollama is installed but not running",
                    repair_hint="Start it with: ollama serve",
                    auto_repairable=True,
                )
            )
    else:
        result.issues.append(
            PreflightIssue(
                severity="warning",
                component="Ollama",
                message=(
                    "Ollama is not installed — local model tiers unavailable"
                ),
                repair_hint=(
                    f"Install: {_ollama_install_hint()}\n"
                    "           Or visit: https://ollama.com"
                ),
            )
        )
    _tick("Ollama checked")

    # --- Ollama models ------------------------------------------------
    if result.ollama_reachable:
        available_models = _ollama_list_models()
        required = _required_ollama_models()
        result.models_missing = [
            m for m in required if m not in available_models
        ]
    _tick("Local models scanned")

    # --- API keys (informational — all tiers now use local models) -----
    keys = _api_keys_present()
    result.has_api_keys = any(keys.values())
    if not result.has_api_keys:
        result.issues.append(
            PreflightIssue(
                severity="warning",
                component="API Keys",
                message="No API keys configured — all tiers use local Ollama models",
                repair_hint=(
                    "This is fine for local-only usage. Add OPENAI_API_KEY "
                    "to .env only if you want cloud fallback."
                ),
            )
        )
    _tick("API keys checked")

    # --- MCP server ---------------------------------------------------
    if _mcp_reachable(timeout=0.3):
        result.mcp_running = True
    elif result.config_ok:
        thread = start_mcp_server_background()
        result.mcp_running = thread is not None and _mcp_reachable(timeout=0.3)
        if not result.mcp_running:
            result.issues.append(
                PreflightIssue(
                    severity="warning",
                    component="MCP Server",
                    message="MCP server could not be started",
                    repair_hint="Run manually: clawsmith start",
                )
            )
    _tick("MCP server ready")

    return result


# ---------------------------------------------------------------------------
# Auto-repair helpers
# ---------------------------------------------------------------------------


def pull_ollama_model(model_name: str, *, quiet: bool = False) -> bool:
    """Pull a single model via ``ollama pull``.

    When *quiet* is ``False`` (the default) Ollama's own progress output is
    visible in the terminal.  Set *quiet* to ``True`` to suppress it so the
    caller can show its own indicator.  Returns ``True`` on success.
    """
    try:
        pipe = subprocess.DEVNULL if quiet else None
        result = subprocess.run(
            ["ollama", "pull", model_name],
            stdout=pipe,
            stderr=pipe,
            timeout=1800,
        )
        return result.returncode == 0
    except Exception:
        return False


def try_start_ollama() -> bool:
    """Attempt to launch ``ollama serve`` in the background.

    Returns ``True`` if the server becomes reachable within ~5 seconds.
    """
    try:
        popen_kwargs: dict = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = (
                subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
            )
        else:
            popen_kwargs["start_new_session"] = True

        subprocess.Popen(["ollama", "serve"], **popen_kwargs)

        for _ in range(10):
            time.sleep(0.5)
            if _ollama_reachable():
                return True
        return _ollama_reachable()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# MCP server lifecycle
# ---------------------------------------------------------------------------

_MCP_HOST = "127.0.0.1"
_MCP_PORT = 8765


def _mcp_reachable(timeout: float = 0.5) -> bool:
    """Quick TCP probe to check if the MCP server is already listening."""
    try:
        with socket.create_connection((_MCP_HOST, _MCP_PORT), timeout=timeout):
            return True
    except (OSError, TimeoutError):
        return False


def start_mcp_server_background() -> threading.Thread | None:
    """Launch the MCP server in a daemon thread.

    Returns the thread if the server was started, or ``None`` if it was
    already running.  The thread is a daemon so it dies with the process.
    """
    try:
        from config.config_loader import get_config

        cfg = get_config()
        host = cfg.mcp_server.host
        port = cfg.mcp_server.port
    except Exception:
        host, port = _MCP_HOST, _MCP_PORT

    if _mcp_reachable(timeout=0.2):
        return None

    def _serve() -> None:
        import logging

        for name in ("uvicorn", "uvicorn.access", "uvicorn.error",
                      "fastmcp", "httpx", "httpcore"):
            logging.getLogger(name).setLevel(logging.WARNING)

        try:
            import io
            import contextlib

            from mcp_server.server import mcp as mcp_app

            with contextlib.redirect_stdout(io.StringIO()), \
                 contextlib.redirect_stderr(io.StringIO()):
                mcp_app.run(transport="sse", host=host, port=port)
        except Exception:
            pass

    t = threading.Thread(target=_serve, daemon=True, name="clawsmith-mcp")
    t.start()

    deadline = time.monotonic() + 1.5
    while time.monotonic() < deadline:
        time.sleep(0.2)
        if _mcp_reachable(timeout=0.1):
            break

    return t
