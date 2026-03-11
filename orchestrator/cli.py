"""ClawSmith CLI — Click entrypoints for the orchestration pipeline."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.syntax import Syntax

console = Console()


@click.group()
def cli() -> None:
    """ClawSmith — AI-powered code orchestration with intelligent model routing."""


@cli.command("run-task")
@click.option("--task", required=True, help="Task description for the pipeline.")
@click.option("--repo-path", default=".", help="Path to the repository root.")
@click.option("--dry-run", is_flag=True, help="Run pipeline steps 1-7 without dispatching to provider or executing jobs.")
def run_task(task: str, repo_path: str, dry_run: bool) -> None:
    """Run the full orchestration pipeline for a task."""
    from orchestrator.logging_setup import setup_logging
    from orchestrator.pipeline import OrchestrationPipeline

    setup_logging()
    result = asyncio.run(OrchestrationPipeline().run(task, repo_path, dry_run))

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

    if result.execution_result:
        er = result.execution_result
        console.print(f"  Exit code: {er.exit_code}")
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
@click.option("--job-file", required=True, type=click.Path(exists=True), help="Path to a JobSpec JSON file.")
def run_job(job_file: str) -> None:
    """Execute a job from a JSON spec file."""
    from jobs.executor import JobExecutor
    from orchestrator.logging_setup import setup_logging
    from orchestrator.schemas import JobSpec

    setup_logging()
    raw = Path(job_file).read_text(encoding="utf-8")
    job = JobSpec.model_validate_json(raw)
    result = asyncio.run(JobExecutor().execute(job, dry_run=job.dry_run))

    if result.success:
        console.print("[bold green]Job completed successfully[/bold green]")
    else:
        console.print(f"[bold red]Job failed:[/bold red] {result.error_message}")

    console.print(f"  Job ID:    {result.job_id}")
    console.print(f"  Exit code: {result.exit_code}")
    console.print(f"  Duration:  {result.duration_seconds:.2f}s")

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
