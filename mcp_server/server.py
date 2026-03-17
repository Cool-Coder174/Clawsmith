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
# OpenClaw integration tools
# ---------------------------------------------------------------------------

@mcp.tool
async def openclaw_forward_task(
    task: str,
    repo_path: str = ".",
    dry_run: bool = False,
) -> str:
    """Forward a task through the full ClawSmith orchestration pipeline.

    This is the primary integration point for OpenClaw: it runs audit, map,
    classify, route, prompt, dispatch, and execute, returning a structured
    result with model used, tier, cost, and execution outcome.
    """
    try:
        from providers.openclaw_adapter import OpenClawAdapter

        adapter = OpenClawAdapter()
        result = await adapter.forward_task(task, repo_path, dry_run)
        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def shared_providers() -> str:
    """List LLM providers and models available for shared use by OpenClaw.

    Returns which API key providers are configured (without exposing the keys),
    which model tiers are available, and which local models are installed.
    OpenClaw can use the ``shared_complete`` tool to route completions through
    any of the listed providers.
    """
    try:
        cfg = get_config()
        oc = cfg.openclaw

        if not oc.share_api_keys and not oc.share_local_models:
            return json.dumps({"shared": False, "reason": "Sharing disabled in config"})

        import os
        import shutil

        result: dict = {"shared": True, "api_providers": [], "tiers": [], "local_models": []}

        if oc.share_api_keys:
            for env_key, provider_name in [
                ("OPENAI_API_KEY", "openai"),
                ("ANTHROPIC_API_KEY", "anthropic"),
                ("OPENROUTER_API_KEY", "openrouter"),
            ]:
                if os.environ.get(env_key):
                    result["api_providers"].append(provider_name)

            for tier_name in ("local_router", "local_code", "premium", "prompt_polisher"):
                tier_cfg = getattr(cfg.models, tier_name)
                result["tiers"].append({
                    "tier": tier_name,
                    "provider": tier_cfg.provider,
                    "model": tier_cfg.model_name,
                    "max_tokens": tier_cfg.max_tokens,
                    "is_local": tier_cfg.provider == "ollama",
                })

        if oc.share_local_models and shutil.which("ollama"):
            try:
                import subprocess
                proc = subprocess.run(
                    ["ollama", "list"],
                    capture_output=True, text=True, timeout=10,
                )
                if proc.returncode == 0:
                    for line in proc.stdout.strip().splitlines()[1:]:
                        parts = line.split()
                        if parts:
                            result["local_models"].append(parts[0])
            except Exception:
                pass

        return json.dumps(result, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def shared_complete(
    prompt: str,
    tier: str = "local_code",
    system_prompt: str = "",
    max_tokens: int = 1024,
    temperature: float = 0.2,
) -> str:
    """Run an LLM completion through ClawSmith's providers on behalf of OpenClaw.

    This lets OpenClaw use ClawSmith's API keys and local models without needing
    its own credentials.  Specify a ``tier`` (local_router, local_code, premium,
    prompt_polisher) to pick which provider/model to use.
    """
    try:
        cfg = get_config()
        oc = cfg.openclaw

        tier_cfg = getattr(cfg.models, tier, None)
        if tier_cfg is None:
            return json.dumps({"error": f"Unknown tier: {tier}"})

        is_local = tier_cfg.provider == "ollama"
        if is_local and not oc.share_local_models:
            return json.dumps({"error": "Local model sharing is disabled in config"})
        if not is_local and not oc.share_api_keys:
            return json.dumps({"error": "API key sharing is disabled in config"})

        from providers.registry import get_registry

        provider = get_registry().get_provider(tier)
        completion = await provider.complete(
            prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return completion.model_dump_json(indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def openclaw_skill_manifest() -> str:
    """Return the ClawSmith skill manifest for OpenClaw registration.

    The manifest includes all available MCP tools, capabilities, endpoints,
    and required environment variables — everything OpenClaw needs to
    discover and route tasks to ClawSmith.
    """
    try:
        from providers.openclaw_adapter import OpenClawAdapter

        adapter = OpenClawAdapter()
        manifest = adapter.build_skill_manifest()
        return json.dumps(manifest, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


# ---------------------------------------------------------------------------
# Forge / Spec-Driven Development tools
# ---------------------------------------------------------------------------

@mcp.tool
async def forge_spec(
    goal: str,
    repo_path: str = ".",
    tier: str | None = None,
    model: str | None = None,
) -> str:
    """Generate an LLM-powered implementation spec for a goal.

    Analyses the codebase, reasons about architecture, and produces a
    structured file-level implementation plan. Uses local Ollama models
    by default for zero-cost spec generation.

    Args:
        goal: What to build or change.
        repo_path: Repository root path.
        tier: 'quick', 'full', or 'epic'. Auto-selects if omitted.
        model: Ollama model name. Defaults to qwen2.5-coder:14b.

    Returns:
        JSON with spec ID, markdown content, file changes, and metadata.
    """
    try:
        from orchestrator.spec_generator import SpecGenerator, SpecTier

        root = _resolve_repo_path(repo_path)
        audit = RepoAuditor(root).audit()
        repo_map_result = RepoMapper(root).map()
        context = ContextPacker(root).pack(audit, repo_map_result, goal)
        classification = TaskClassifier().classify(goal, context)

        gen_kwargs = {}
        if model:
            gen_kwargs["model"] = model
        generator = SpecGenerator(**gen_kwargs)

        spec_tier = SpecTier(tier) if tier else None
        spec, spec_path = await generator.generate_and_save(
            goal, repo_path, context, classification, spec_tier,
        )

        return json.dumps({
            "spec_id": spec.id,
            "tier": spec.tier.value,
            "summary": spec.summary,
            "file_changes": len(spec.file_changes),
            "phases": len(spec.phases),
            "risks": spec.risks,
            "open_questions": spec.open_questions,
            "generation_time_seconds": spec.generation_time_seconds,
            "model_used": spec.model_used,
            "saved_to": str(spec_path),
            "markdown": spec.to_markdown(),
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def forge_verify(
    spec_id: str,
    repo_path: str = ".",
    model: str | None = None,
) -> str:
    """Verify the current implementation against a generated spec.

    Compares git diffs to the spec's expected file changes and uses
    LLM analysis to categorize issues as CRITICAL, MAJOR, MINOR, or INFO.

    Args:
        spec_id: ID of the spec to verify against.
        repo_path: Repository root path.
        model: Ollama model for verification.

    Returns:
        JSON verification report with score, verdict, and categorized comments.
    """
    try:
        from orchestrator.spec_generator import GeneratedSpec
        from orchestrator.verifier import SpecVerifier

        root = _resolve_repo_path(repo_path)
        spec_json = root / ".clawsmith" / "specs" / f"{spec_id}.json"
        if not spec_json.exists():
            return json.dumps({"error": f"Spec not found: {spec_id}"})

        spec = GeneratedSpec.model_validate_json(spec_json.read_text(encoding="utf-8"))

        verify_kwargs = {}
        if model:
            verify_kwargs["model"] = model
        verifier = SpecVerifier(**verify_kwargs)

        result, report_path = await verifier.verify_and_save(spec, repo_path)

        return json.dumps({
            "spec_id": spec_id,
            "passed": result.passed,
            "score": result.score,
            "critical_count": result.critical_count,
            "major_count": result.major_count,
            "total_comments": len(result.comments),
            "files_expected": result.files_expected,
            "files_found": result.files_found,
            "files_missing": result.files_missing,
            "files_unplanned": result.files_unplanned,
            "summary": result.summary,
            "comments": [c.model_dump() for c in result.comments],
            "saved_to": str(report_path),
            "markdown": result.to_markdown(),
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def forge_run(
    goal: str,
    repo_path: str = ".",
    mode: str = "forge",
    tier: str | None = None,
    model: str | None = None,
    max_fixes: int = 2,
) -> str:
    """Run the full forge pipeline: spec > execute > verify > fix.

    This is the primary spec-driven development command. It generates
    an implementation spec, executes it through coding agents, verifies
    the result, and auto-fixes issues.

    Args:
        goal: What to build or change.
        repo_path: Repository root.
        mode: 'plan' (spec only), 'execute' (no auto-fix), 'forge' (full loop).
        tier: Spec tier ('quick', 'full', 'epic'). Auto-selects if omitted.
        model: Ollama model name.
        max_fixes: Max fix loops after verification failure.

    Returns:
        JSON with spec, execution, and verification results.
    """
    try:
        from orchestrator.forge import ForgeEngine, ForgeMode
        from orchestrator.agent_status import StatusTracker
        from orchestrator.schemas import YoloConfig
        from orchestrator.spec_generator import SpecTier

        forge_mode = ForgeMode(mode)
        spec_tier = SpecTier(tier) if tier else None

        engine_kwargs = {}
        if model:
            engine_kwargs["spec_model"] = model
            engine_kwargs["verify_model"] = model

        engine = ForgeEngine(max_fix_loops=max_fixes, **engine_kwargs)
        tracker = StatusTracker()
        cfg = YoloConfig()

        result = await engine.run(
            goal, repo_path,
            mode=forge_mode,
            spec_tier=spec_tier,
            yolo_config=cfg,
            status=tracker,
        )

        return json.dumps(result.summary(), indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def forge_refine(
    spec_id: str,
    feedback: str,
    repo_path: str = ".",
    model: str | None = None,
) -> str:
    """Refine an existing spec based on feedback.

    Takes a generated spec and user feedback, produces an updated version
    that addresses the feedback while preserving the original intent.

    Args:
        spec_id: ID of the spec to refine.
        feedback: What to change about the spec.
        repo_path: Repository root.
        model: Ollama model name.

    Returns:
        JSON with the refined spec ID, markdown, and metadata.
    """
    try:
        from orchestrator.spec_generator import GeneratedSpec, SpecGenerator

        root = _resolve_repo_path(repo_path)
        spec_json = root / ".clawsmith" / "specs" / f"{spec_id}.json"
        if not spec_json.exists():
            return json.dumps({"error": f"Spec not found: {spec_id}"})

        original = GeneratedSpec.model_validate_json(spec_json.read_text(encoding="utf-8"))

        gen_kwargs = {}
        if model:
            gen_kwargs["model"] = model
        generator = SpecGenerator(**gen_kwargs)

        refinement_goal = (
            f"Refine the following implementation spec based on feedback.\n\n"
            f"## Original Goal\n{original.goal}\n\n"
            f"## Current Spec\n{original.to_markdown()}\n\n"
            f"## Feedback\n{feedback}\n\n"
            f"Produce an updated spec that addresses the feedback."
        )

        refined = await generator.generate(refinement_goal, tier=original.tier)
        refined.goal = original.goal

        specs_dir = root / ".clawsmith" / "specs"
        refined_path = specs_dir / f"{refined.id}.md"
        refined_path.write_text(refined.to_markdown(), encoding="utf-8")
        json_path = specs_dir / f"{refined.id}.json"
        json_path.write_text(refined.model_dump_json(indent=2), encoding="utf-8")

        return json.dumps({
            "original_spec_id": spec_id,
            "refined_spec_id": refined.id,
            "tier": refined.tier.value,
            "file_changes": len(refined.file_changes),
            "phases": len(refined.phases),
            "generation_time_seconds": refined.generation_time_seconds,
            "saved_to": str(refined_path),
            "markdown": refined.to_markdown(),
        }, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


@mcp.tool
async def forge_list_specs(repo_path: str = ".") -> str:
    """List all generated specs in a repository.

    Returns:
        JSON array of spec summaries with IDs, goals, tiers, and timestamps.
    """
    try:
        root = _resolve_repo_path(repo_path)
        specs_dir = root / ".clawsmith" / "specs"
        if not specs_dir.exists():
            return json.dumps([])

        from orchestrator.spec_generator import GeneratedSpec

        specs = []
        for f in sorted(specs_dir.glob("*.json")):
            try:
                spec = GeneratedSpec.model_validate_json(f.read_text(encoding="utf-8"))
                specs.append({
                    "id": spec.id,
                    "goal": spec.goal[:100],
                    "tier": spec.tier.value,
                    "file_changes": len(spec.file_changes),
                    "phases": len(spec.phases),
                    "model_used": spec.model_used,
                    "created_at": spec.created_at,
                })
            except Exception:
                pass

        return json.dumps(specs, indent=2)
    except Exception as exc:
        return json.dumps({"error": str(exc)}, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cfg = get_config()
    mcp.run(transport=cfg.mcp_server.transport)
