"""ClawSmith MCP server — exposes all orchestration capabilities as FastMCP tools."""

from __future__ import annotations

import asyncio
import collections
import json
import re
from pathlib import Path

from fastmcp import FastMCP

from config.config_loader import get_config
from jobs.executor import JobExecutor
from orchestrator.logging_setup import get_logger
from orchestrator.schemas import ContextPacket, JobSpec
from prompts.generator import PromptGenerator
from routing.classifier import TaskClassifier
from routing.cost_estimator import CostEstimator
from routing.router import ModelRouter
from tools.build_detector import BuildDetector
from tools.context_packer import ContextPacker
from tools.repo_auditor import RepoAuditor
from tools.repo_mapper import RepoMapper

mcp = FastMCP(name="Clawsmith")

_REPO_ROOT = Path(__file__).parent.parent

logger = get_logger("mcp_server")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _run_command_async(
    args: list[str],
    cwd: Path,
    timeout: int = 300,
    *,
    shell: bool = False,
) -> dict:
    """Run a subprocess and return a structured result dict."""
    try:
        if shell:
            proc = await asyncio.create_subprocess_shell(
                " ".join(args),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )
        raw_stdout, raw_stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except TimeoutError:
        try:
            proc.kill()  # type: ignore[possibly-undefined]
        except Exception:
            pass
        return {
            "exit_code": -2,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "success": False,
        }

    stdout_text = raw_stdout.decode("utf-8", errors="replace") if raw_stdout else ""
    stderr_text = raw_stderr.decode("utf-8", errors="replace") if raw_stderr else ""
    return {
        "exit_code": proc.returncode or 0,
        "stdout": stdout_text,
        "stderr": stderr_text,
        "success": proc.returncode == 0,
    }


def _resolve_repo_path(repo_path: str) -> Path:
    """Resolve a repo path to an absolute Path, raising ValueError if missing."""
    root = Path(repo_path).resolve()
    if not root.exists():
        raise ValueError(f"Repository path does not exist: {root}")
    return root


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool
async def repo_audit(repo_path: str) -> str:
    """Audit a repository and return a detailed report."""
    try:
        root = _resolve_repo_path(repo_path)
        report = RepoAuditor(root).audit()
        return report.model_dump_json(indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def repo_map(repo_path: str, max_lines: int = 200) -> str:
    """Generate a directory-tree map of a repository with entrypoints and important files."""
    try:
        root = _resolve_repo_path(repo_path)
        repo_map_result = RepoMapper(root, max_lines=max_lines).map()
        return repo_map_result.model_dump_json(indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def repo_pack_context(
    repo_path: str,
    task_description: str,
    file_list: list[str] | None = None,
) -> str:
    """Audit, map, and pack repository context for a given task into a ContextPacket."""
    try:
        root = _resolve_repo_path(repo_path)
        audit = RepoAuditor(root).audit()
        repo_map_result = RepoMapper(root).map()
        packet = ContextPacker(root).pack(audit, repo_map_result, task_description, file_list)
        return packet.model_dump_json(indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def route_pick_model(
    task_description: str,
    context_json: str | None = None,
) -> str:
    """Classify a task and route it to the best model tier, returning the routing decision."""
    try:
        context: ContextPacket | None = None
        if context_json:
            context = ContextPacket.model_validate_json(context_json)
        classification = TaskClassifier().classify(task_description, context)
        decision = ModelRouter().route_task(classification)
        return decision.model_dump_json(indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def cost_estimate(
    task_description: str,
    context_size_tokens: int = 0,
) -> str:
    """Estimate the cost of running a task across all model tiers."""
    try:
        estimates = CostEstimator().estimate(task_description, context_size_tokens)
        return json.dumps([e.model_dump() for e in estimates], indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def agent_run_job(job_spec_json: str) -> str:
    """Parse a JobSpec from JSON and execute it through the full job pipeline.

    The job's ``agent_target`` field selects which agent CLI to use.
    If not set, the system auto-selects the best available agent.
    """
    try:
        job = JobSpec.model_validate_json(job_spec_json)
    except Exception as exc:
        return json.dumps(
            {"error": f"Invalid JobSpec JSON: {exc}"}, indent=2
        )
    try:
        agent_id = "none"
        agent_invocation = ""
        agent_display_name = "ClawSmith"
        try:
            from agents.registry import get_agent_registry
            from agents.router import AgentRouter

            cfg = get_config()
            registry = get_agent_registry(auto_detect=cfg.agents.auto_detect)
            router = AgentRouter(
                registry,
                default_agent=cfg.agents.default_agent,
                fallback_order=cfg.agents.fallback_order,
            )
            decision = router.select_agent(
                requested_agent=job.agent_target, needs_headless=True,
            )
            agent_id = decision.agent_id
            agent_display_name = decision.adapter.display_name
            spec = decision.adapter.build_invocation(
                prompt=job.prompt[:500],
                working_directory=job.working_directory,
                timeout_seconds=job.timeout_seconds,
            )
            agent_invocation = " ".join(
                f'"{a}"' if " " in a else a for a in spec.args
            )
        except Exception:
            pass

        result = await JobExecutor().execute(
            job,
            dry_run=job.dry_run,
            agent_invocation=agent_invocation,
            agent_id=agent_id,
            agent_display_name=agent_display_name,
        )
        return result.model_dump_json(indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def cursor_run_job(job_spec_json: str) -> str:
    """Legacy alias for agent_run_job. Prefer agent_run_job for new integrations."""
    return await agent_run_job(job_spec_json)


@mcp.tool
async def agent_run_bat(bat_path: str, timeout: int = 300) -> str:
    """Execute a .bat file within the workspace, with path-escape protection."""
    try:
        resolved = Path(bat_path).resolve()
        try:
            resolved.relative_to(_REPO_ROOT.resolve())
        except ValueError as exc:
            raise ValueError(
                f"Path {resolved} is outside the workspace root {_REPO_ROOT.resolve()}"
            ) from exc
        if not resolved.exists():
            raise ValueError(f"File does not exist: {resolved}")
        if resolved.suffix.lower() != ".bat":
            raise ValueError(f"File is not a .bat file: {resolved}")

        result = await _run_command_async(
            ["cmd.exe", "/c", str(resolved)],
            cwd=_REPO_ROOT,
            timeout=timeout,
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def cursor_run_bat(bat_path: str, timeout: int = 300) -> str:
    """Legacy alias for agent_run_bat. Prefer agent_run_bat for new integrations."""
    return await agent_run_bat(bat_path, timeout)


@mcp.tool
async def detect_agent_clis() -> str:
    """Detect installed agent CLIs and return a capability matrix."""
    try:
        from agents.registry import get_agent_registry
        registry = get_agent_registry(auto_detect=True)
        matrix = registry.get_capability_matrix()
        return json.dumps(matrix, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def build_run(repo_path: str, ecosystem: str | None = None) -> str:
    """Detect and run build/install commands for a repository."""
    try:
        root = _resolve_repo_path(repo_path)
        commands = BuildDetector(root).detect()
        if ecosystem:
            commands = [c for c in commands if c.ecosystem == ecosystem]
        commands = [c for c in commands if c.purpose in ("build", "install")]

        results = []
        for cmd in commands:
            res = await _run_command_async(
                [cmd.command], cwd=root, timeout=300, shell=True
            )
            res["command"] = cmd.command
            results.append(res)
        return json.dumps(results, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def tests_run(repo_path: str, ecosystem: str | None = None) -> str:
    """Detect and run test commands for a repository."""
    try:
        root = _resolve_repo_path(repo_path)
        commands = BuildDetector(root).detect()
        if ecosystem:
            commands = [c for c in commands if c.ecosystem == ecosystem]
        commands = [c for c in commands if c.purpose == "test"]

        results = []
        for cmd in commands:
            res = await _run_command_async(
                [cmd.command], cwd=root, timeout=300, shell=True
            )
            res["command"] = cmd.command
            results.append(res)
        return json.dumps(results, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


_BRANCH_RE = re.compile(r"^[a-zA-Z0-9_/.\-]+$")


@mcp.tool
async def git_create_worktree(
    repo_path: str,
    branch_name: str,
    worktree_path: str,
) -> str:
    """Create a git worktree with a new branch, validating inputs against injection."""
    try:
        root = _resolve_repo_path(repo_path)
        if not _BRANCH_RE.match(branch_name):
            raise ValueError(
                f"Invalid branch name: {branch_name!r} — "
                "only alphanumerics, underscores, slashes, dots, and hyphens allowed"
            )
        wt = Path(worktree_path).resolve()
        try:
            wt.relative_to(_REPO_ROOT.resolve())
        except ValueError as exc:
            if wt.drive != _REPO_ROOT.resolve().drive:
                raise ValueError(
                    f"Worktree path {wt} is outside the workspace drive"
                ) from exc

        result = await _run_command_async(
            ["git", "worktree", "add", str(wt), "-b", branch_name],
            cwd=root,
        )
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def logs_read_recent(lines: int = 100) -> str:
    """Read the last N lines from the ClawSmith log file."""
    try:
        cfg = get_config()
        log_path = _REPO_ROOT / cfg.execution.logs_dir / "clawsmith.log"
        if not log_path.exists():
            return "[No log file found]"
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            tail = collections.deque(fh, maxlen=lines)
        return "".join(tail)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def prompts_generate_task_prompt(
    task_description: str,
    repo_path: str,
) -> str:
    """Generate a structured task prompt by combining repo context with routing intelligence."""
    try:
        root = _resolve_repo_path(repo_path)
        audit = RepoAuditor(root).audit()
        repo_map_result = RepoMapper(root).map()
        packet = ContextPacker(root).pack(audit, repo_map_result, task_description)
        classification = TaskClassifier().classify(task_description, packet)
        decision = ModelRouter().route_task(classification)

        return PromptGenerator().generate(task_description, packet, decision)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = get_config()
    mcp.run(transport=cfg.mcp_server.transport)
