"""ClawSmith CLI — Click entrypoints for the orchestration pipeline."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

console = Console()


@click.group()
def cli() -> None:
    """ClawSmith — CLI-first orchestration and deployment layer for coding agents."""


@cli.command("run-task")
@click.option("--task", required=True, help="Task description for the pipeline.")
@click.option("--repo-path", default=".", help="Path to the repository root.")
@click.option("--dry-run", is_flag=True, help="Skip provider dispatch and job execution.")
@click.option("--agent", default=None, help="Agent CLI id (cursor, claude_code, gemini_cli).")
def run_task(task: str, repo_path: str, dry_run: bool, agent: str | None) -> None:
    """Run the full orchestration pipeline for a task."""
    from orchestrator.logging_setup import setup_logging
    from orchestrator.pipeline import OrchestrationPipeline

    setup_logging()
    result = asyncio.run(OrchestrationPipeline().run(task, repo_path, dry_run, agent_target=agent))

    if result.success:
        console.print("\n[bold green]Pipeline completed successfully[/bold green]")
    else:
        console.print(f"\n[bold red]Pipeline failed:[/bold red] {result.error_message}")

    console.print(f"  Duration: {result.duration_seconds:.2f}s")
    console.print(f"  Dry run:  {result.dry_run}")

    if result.routing_decision:
        rd = result.routing_decision
        console.print(f"  Tier:     {rd.selected_tier.value}")
        console.print(f"  Model:    {rd.model_name}")
        console.print(f"  Cost est: ${rd.estimated_cost_usd:.4f}")
        if rd.agent_target:
            console.print(f"  Agent:    {rd.agent_target}")

    if result.execution_result:
        er = result.execution_result
        console.print(f"  Exit code: {er.exit_code}")
        if er.agent_used:
            console.print(f"  Agent used: {er.agent_used}")
        if er.error_message:
            console.print(f"  Exec error: {er.error_message}")

    if not result.success:
        sys.exit(1)


@cli.command("audit")
@click.option("--repo-path", default=".", help="Path to the repository root.")
def audit(repo_path: str) -> None:
    """Audit a repository and print the JSON report."""
    from orchestrator.logging_setup import setup_logging
    from tools.repo_auditor import RepoAuditor

    setup_logging()
    report = RepoAuditor(Path(repo_path).resolve()).audit()
    report_json = report.model_dump_json(indent=2)
    syntax = Syntax(report_json, "json", theme="monokai", line_numbers=False)
    console.print(syntax)


@cli.command("run-job")
@click.option("--job-file", required=True, type=click.Path(exists=True), help="JobSpec JSON file.")
@click.option("--agent", default=None, help="Agent CLI id. Default: auto-select.")
def run_job(job_file: str, agent: str | None) -> None:
    """Execute a job from a JSON spec file."""
    from agents.registry import get_agent_registry
    from agents.router import AgentNotAvailableError, AgentRouter
    from config.config_loader import get_config
    from jobs.executor import JobExecutor
    from orchestrator.logging_setup import setup_logging
    from orchestrator.schemas import JobSpec

    setup_logging()
    raw = Path(job_file).read_text(encoding="utf-8")
    job = JobSpec.model_validate_json(raw)

    effective_agent = agent or job.agent_target
    agent_id = "none"
    agent_invocation = ""
    agent_display_name = "ClawSmith"

    try:
        config = get_config()
        registry = get_agent_registry(auto_detect=config.agents.auto_detect)
        router = AgentRouter(
            registry,
            default_agent=config.agents.default_agent,
            fallback_order=config.agents.fallback_order,
        )
        decision = router.select_agent(requested_agent=effective_agent, needs_headless=True)
        agent_id = decision.agent_id
        agent_display_name = decision.adapter.display_name
        spec = decision.adapter.build_invocation(
            prompt=job.prompt[:500],
            working_directory=job.working_directory,
            model=job.model_preference,
            timeout_seconds=job.timeout_seconds,
        )
        agent_invocation = " ".join(f'"{a}"' if " " in a else a for a in spec.args)
    except AgentNotAvailableError:
        console.print("[yellow]No agent CLI available; executing build/test only.[/yellow]")

    result = asyncio.run(
        JobExecutor().execute(
            job,
            dry_run=job.dry_run,
            agent_invocation=agent_invocation,
            agent_id=agent_id,
            agent_display_name=agent_display_name,
        )
    )

    if result.success:
        console.print("[bold green]Job completed successfully[/bold green]")
    else:
        console.print(f"[bold red]Job failed:[/bold red] {result.error_message}")

    console.print(f"  Job ID:    {result.job_id}")
    console.print(f"  Exit code: {result.exit_code}")
    console.print(f"  Duration:  {result.duration_seconds:.2f}s")
    if result.agent_used:
        console.print(f"  Agent:     {result.agent_used}")

    if not result.success:
        sys.exit(1)


@cli.command("start-server")
def start_server() -> None:
    """Start the ClawSmith MCP server."""
    from config.config_loader import get_config
    from orchestrator.logging_setup import setup_logging

    setup_logging()
    cfg = get_config()
    console.print(
        f"Starting MCP server on {cfg.mcp_server.host}:{cfg.mcp_server.port} "
        f"(transport={cfg.mcp_server.transport})"
    )
    from mcp_server.server import mcp as mcp_app

    mcp_app.run(transport=cfg.mcp_server.transport)


@cli.command("doctor")
def doctor() -> None:
    """Run preflight checks and report ClawSmith readiness."""
    from orchestrator.doctor import run_doctor

    ok = run_doctor()
    if not ok:
        sys.exit(1)


@cli.command("register-skill")
@click.option("--output", default="SKILL.md", help="Output path for the generated SKILL.md.")
def register_skill(output: str) -> None:
    """Generate an OpenClaw SKILL.md registration artifact."""
    from orchestrator.logging_setup import setup_logging
    from providers.openclaw_adapter import OpenClawAdapter

    setup_logging()
    adapter = OpenClawAdapter()
    path = adapter.register_as_skill(Path(output))
    console.print(f"[bold green]Skill file written to:[/bold green] {path}")


@cli.command("detect-agents")
def detect_agents() -> None:
    """Scan for installed agent CLIs and display a capability matrix."""
    from agents.registry import get_agent_registry

    registry = get_agent_registry(auto_detect=True)
    matrix = registry.get_capability_matrix()

    table = Table(title="Detected Agent CLIs", show_lines=True)
    table.add_column("Agent ID", style="cyan", min_width=14)
    table.add_column("Display Name", min_width=18)
    table.add_column("Available", min_width=10)
    table.add_column("Executable", min_width=20)
    table.add_column("Version", min_width=15)
    table.add_column("Capabilities", min_width=30)

    for agent_id, info in matrix.items():
        avail = "[green]Yes[/green]" if info["available"] else "[red]No[/red]"
        exe = info["executable"] or "-"
        ver = info["version"] or "-"
        caps = ", ".join(info["capabilities"][:5])
        if len(info["capabilities"]) > 5:
            caps += f" (+{len(info['capabilities']) - 5} more)"
        table.add_row(agent_id, info["display_name"], avail, exe, ver, caps)

    console.print(table)
