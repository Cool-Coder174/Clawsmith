"""ClawSmith CLI — Click entrypoints for the orchestration pipeline."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
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
    from rich.live import Live
    from rich.text import Text

    from orchestrator.agent_status import StatusTracker
    from orchestrator.logging_setup import setup_logging
    from orchestrator.pipeline import OrchestrationPipeline

    setup_logging()
    tracker = StatusTracker()

    _LIFECYCLE = [
        ("deployed", "Deploy"),
        ("planning", "Plan"),
        ("executing", "Execute"),
        ("verifying", "Verify"),
        ("complete", "Complete"),
    ]
    _PHASE_IDX = {k: i for i, (k, _) in enumerate(_LIFECYCLE)}

    def _build_strip() -> Text:
        phase = tracker.phase.value
        failed = phase == "failed"
        idx = _PHASE_IDX.get(phase, -1)
        line = Text("  ")
        for i, (key, label) in enumerate(_LIFECYCLE):
            if i > 0:
                line.append(" → ", style="dim")
            if failed and key == phase:
                line.append(f"[{label}]", style="bold red")
            elif i < idx:
                line.append(f"✓ {label}", style="green")
            elif i == idx:
                line.append(f"● {label}", style="bold cyan")
            else:
                line.append(f"○ {label}", style="dim")
        latest = tracker.events[-1].step if tracker.events else ""
        if latest:
            line.append(f"  — {latest}", style="dim")
        return line

    with Live(_build_strip(), console=console, refresh_per_second=10, transient=True) as live:
        tracker.on_status(lambda _ev: live.update(_build_strip()))

        result = asyncio.run(
            OrchestrationPipeline().run(
                task, repo_path, dry_run, agent_target=agent, status=tracker,
            )
        )

    # Final status strip (static)
    console.print(_build_strip())
    console.print()

    if result.success:
        console.print("[bold green]Pipeline completed successfully[/bold green]")
    else:
        console.print(f"[bold red]Pipeline failed:[/bold red] {result.error_message}")

    console.print(f"  Duration: {result.duration_seconds:.2f}s")
    console.print(f"  Dry run:  {result.dry_run}")
    if result.agent_status:
        console.print(f"  Phase:    {result.agent_status.get('phase', 'unknown')}")
        console.print(f"  Steps:    {result.agent_status.get('step_count', 0)}")

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


def _yolo_build_strip(tracker: object, lifecycle: list[tuple[str, str]]) -> object:
    """Build a Rich Text progress strip for YOLO mode."""
    from rich.text import Text

    phase_idx = {k: i for i, (k, _) in enumerate(lifecycle)}
    phase = tracker.phase.value
    failed = phase == "failed"
    retrying = phase == "retrying"
    idx = phase_idx.get(phase, -1)
    if retrying:
        idx = phase_idx.get("executing", -1)
    line = Text("  ")
    for i, (key, label) in enumerate(lifecycle):
        if i > 0:
            line.append(" → ", style="dim")
        if failed and i == idx:
            line.append(f"[{label}]", style="bold red")
        elif i < idx:
            line.append(f"✓ {label}", style="green")
        elif i == idx:
            style = "bold yellow" if retrying else "bold cyan"
            line.append(f"● {label}", style=style)
        else:
            line.append(f"○ {label}", style="dim")

    yolo_meta = tracker._yolo_meta
    if yolo_meta:
        cur = yolo_meta.get("yolo_current_phase", "")
        tot = yolo_meta.get("yolo_total_phases", "")
        title = yolo_meta.get("yolo_phase_title", "")
        attempt = yolo_meta.get("yolo_attempt", 1)
        suffix = f" (retry {attempt})" if attempt > 1 else ""
        line.append(f"  — Phase {cur}/{tot}: {title}{suffix}", style="dim")
    elif tracker.events:
        line.append(f"  — {tracker.events[-1].step}", style="dim")
    return line


def _yolo_print_results(result: object, cfg: object) -> None:
    """Print the YOLO run results table and summary."""
    if result.phase_results:
        table = Table(title="Phase Results", show_lines=True)
        table.add_column("#", style="dim", width=3)
        table.add_column("Phase", style="cyan", min_width=20)
        table.add_column("Status", min_width=10)
        table.add_column("Attempts", width=8)
        table.add_column("Duration", width=10)
        table.add_column("Error", min_width=30)

        for pr in result.phase_results:
            status_style = {
                "completed": "green",
                "failed": "bold red",
                "skipped": "dim",
                "paused": "yellow",
            }.get(pr.status.value, "white")
            err_text = pr.error_history[-1][:60] if pr.error_history else ""
            table.add_row(
                str(pr.phase_index + 1),
                pr.title,
                f"[{status_style}]{pr.status.value}[/{status_style}]",
                str(pr.attempts),
                f"{pr.duration_seconds:.1f}s",
                err_text,
            )
        console.print(table)
        console.print()

    # Show verification findings for failed phases
    if result.phase_results:
        for pr in result.phase_results:
            pipeline = getattr(pr, "pipeline_result", None)
            if pipeline and hasattr(pipeline, "agent_status"):
                findings = pipeline.agent_status.get("review_comments", [])
                if findings:
                    console.print(
                        f"[bold yellow]Findings for phase {pr.phase_index + 1} "
                        f"({pr.title}):[/bold yellow]"
                    )
                    for f in findings[:5]:
                        sev = f.get("severity", "INFO")
                        cat = f.get("category", "")
                        msg = f.get("message", "")
                        fpath = f.get("file", "")
                        sev_style = {
                            "CRITICAL": "bold red",
                            "MAJOR": "yellow",
                            "MINOR": "dim",
                            "INFO": "dim",
                        }.get(sev, "white")
                        loc = f" {fpath}" if fpath else ""
                        console.print(
                            f"  [{sev_style}]{sev}[/{sev_style}] "
                            f"[dim]{cat}[/dim]{loc}: {msg}"
                        )
                    if len(findings) > 5:
                        console.print(
                            f"  [dim]... and {len(findings) - 5} more[/dim]"
                        )
                    console.print()

    if result.success:
        console.print("[bold green]YOLO run completed successfully[/bold green]")
    else:
        console.print(f"[bold red]YOLO run failed:[/bold red] {result.error_message}")

    console.print(f"  Duration:   {result.duration_seconds:.2f}s")
    console.print(f"  Phases:     {result.completed_phases}/{result.total_phases} completed")
    if result.failed_phases:
        console.print(f"  Failed:     {result.failed_phases}")
    if result.skipped_phases:
        console.print(f"  Skipped:    {result.skipped_phases}")
    console.print(f"  Backend:    CLI Agent (agent chat)")
    if hasattr(cfg, "dry_run"):
        console.print(f"  Dry run:    {cfg.dry_run}")

    if not result.success:
        sys.exit(1)


_YOLO_LIFECYCLE = [
    ("deployed", "Deploy"),
    ("decomposing", "Decompose"),
    ("planning", "Plan"),
    ("queued", "Queue"),
    ("executing", "Execute"),
    ("verifying", "Verify"),
    ("complete", "Complete"),
]


_FORGE_LIFECYCLE = [
    ("deployed", "Deploy"),
    ("planning", "Spec"),
    ("executing", "Execute"),
    ("verifying", "Verify"),
    ("complete", "Complete"),
]


@cli.command("forge")
@click.option("--goal", required=True, help="What you want to build or change.")
@click.option("--repo-path", default=".", help="Path to the repository root.")
@click.option(
    "--tier", type=click.Choice(["quick", "full", "epic"]), default=None,
    help="Spec detail level. Auto-selects based on complexity if omitted.",
)
@click.option(
    "--mode", type=click.Choice(["plan", "execute", "forge"]), default="forge",
    help="plan=spec only, execute=spec+run+verify, forge=full loop with auto-fix.",
)
@click.option("--model", default=None, help="Ollama model for spec generation and verification.")
@click.option("--max-retries", default=2, type=int, help="Max retries per YOLO phase.")
@click.option("--max-fixes", default=2, type=int, help="Max fix loops after verification failure.")
@click.option("--agent", default=None, help="Agent CLI id for execution.")
@click.option("--dry-run", is_flag=True, help="Generate spec and plan but don't execute.")
def forge(
    goal: str,
    repo_path: str,
    tier: str | None,
    mode: str,
    model: str | None,
    max_retries: int,
    max_fixes: int,
    agent: str | None,
    dry_run: bool,
) -> None:
    """Forge — full spec-driven development loop.

    Generates an LLM-powered implementation spec, executes it through
    coding agents, verifies the result against the spec, and auto-fixes
    issues. The complete Traycer-style pipeline, running locally.

    \b
    Modes:
      plan    - generate spec only, save for review
      execute - spec > agent execution > verify (no auto-fix)
      forge   - full loop: spec > exec > verify > fix > re-verify
    """
    from rich.live import Live

    from orchestrator.agent_status import StatusTracker
    from orchestrator.forge import ForgeEngine, ForgeMode
    from orchestrator.logging_setup import setup_logging
    from orchestrator.schemas import YoloConfig
    from orchestrator.spec_generator import SpecTier

    setup_logging()
    tracker = StatusTracker()

    forge_mode = ForgeMode(mode)
    if dry_run:
        forge_mode = ForgeMode.plan

    spec_tier = SpecTier(tier) if tier else None
    yolo_cfg = YoloConfig(
        max_retries=max_retries,
        agent_target=agent,
        dry_run=dry_run,
    )

    engine_kwargs = {}
    if model:
        engine_kwargs["spec_model"] = model
        engine_kwargs["verify_model"] = model

    engine = ForgeEngine(max_fix_loops=max_fixes, **engine_kwargs)

    console.print()
    console.print(Panel(
        f"[bold cyan]Forge[/bold cyan]  {goal}\n"
        f"[dim]mode={forge_mode.value}  max_fixes={max_fixes}[/dim]",
        expand=False,
    ))
    console.print()

    _PHASE_IDX = {k: i for i, (k, _) in enumerate(_FORGE_LIFECYCLE)}

    def _build_strip():
        from rich.text import Text
        phase = tracker.phase.value
        failed = phase == "failed"
        idx = _PHASE_IDX.get(phase, -1)
        line = Text("  ")
        for i, (key, label) in enumerate(_FORGE_LIFECYCLE):
            if i > 0:
                line.append(" → ", style="dim")
            if failed and i == idx:
                line.append(f"[{label}]", style="bold red")
            elif i < idx:
                line.append(f"✓ {label}", style="green")
            elif i == idx:
                line.append(f"● {label}", style="bold cyan")
            else:
                line.append(f"○ {label}", style="dim")
        latest = tracker.events[-1].step if tracker.events else ""
        if latest:
            line.append(f"  — {latest}", style="dim")
        return line

    with Live(_build_strip(), console=console, refresh_per_second=10, transient=True) as live:
        tracker.on_status(lambda _ev: live.update(_build_strip()))
        result = asyncio.run(
            engine.run(
                goal,
                repo_path,
                mode=forge_mode,
                spec_tier=spec_tier,
                yolo_config=yolo_cfg,
                status=tracker,
            )
        )

    console.print(_build_strip())
    console.print()

    # Print spec summary
    if result.spec:
        spec = result.spec
        console.print(f"[bold]Spec:[/bold] {spec.id} ({spec.tier.value})")
        console.print(f"  File changes: {len(spec.file_changes)}")
        console.print(f"  Phases:       {len(spec.phases)}")
        console.print(f"  Risks:        {len(spec.risks)}")
        if result.spec_path:
            console.print(f"  Saved to:     {result.spec_path}")
        console.print()

    # Print execution result
    if result.execution_result:
        er = result.execution_result
        console.print(f"[bold]Execution:[/bold] {'✅ success' if er.success else '❌ failed'}")
        console.print(f"  Phases: {er.completed_phases}/{er.total_phases} completed")
        if er.failed_phases:
            console.print(f"  Failed: {er.failed_phases}")
        console.print()

    # Print verification results
    if result.verification_results:
        for i, v in enumerate(result.verification_results):
            label = "Initial" if i == 0 else f"After fix {i}"
            passed_style = "green" if v.passed else "red"
            console.print(
                f"[bold]Verification ({label}):[/bold] "
                f"[{passed_style}]{v.score:.0%}[/{passed_style}]  "
                f"🔴{v.critical_count} 🟠{v.major_count}"
            )
        console.print()

    # Final verdict
    if result.success:
        console.print("[bold green]Forge complete ✅[/bold green]")
    else:
        console.print(f"[bold red]Forge failed:[/bold red] {result.error or 'unknown'}")

    console.print(f"  Duration:    {result.duration_seconds:.1f}s")
    console.print(f"  Fix attempts: {result.fix_attempts}")

    if not result.success:
        sys.exit(1)


@cli.command("spec-refine")
@click.option("--spec-id", required=True, help="Spec ID to refine.")
@click.option("--feedback", required=True, help="What to change about the spec.")
@click.option("--repo-path", default=".", help="Path to the repository root.")
@click.option("--model", default=None, help="Ollama model name.")
def spec_refine(spec_id: str, feedback: str, repo_path: str, model: str | None) -> None:
    """Refine an existing spec with feedback.

    Takes a generated spec and user feedback, then produces an updated
    version that addresses the feedback while preserving the original intent.
    """
    import json as json_mod
    from orchestrator.logging_setup import setup_logging
    from orchestrator.spec_generator import GeneratedSpec, SpecGenerator

    setup_logging()
    root = Path(repo_path).resolve()
    specs_dir = root / ".clawsmith" / "specs"
    spec_json = specs_dir / f"{spec_id}.json"

    if not spec_json.exists():
        console.print(f"[bold red]Spec not found:[/bold red] {spec_json}")
        sys.exit(1)

    original = GeneratedSpec.model_validate_json(spec_json.read_text(encoding="utf-8"))

    console.print()
    console.print(Panel(
        f"[bold cyan]Spec Refinement[/bold cyan]\n"
        f"Original: {spec_id}\n"
        f"Feedback: {feedback[:100]}",
        expand=False,
    ))

    gen_kwargs = {}
    if model:
        gen_kwargs["model"] = model
    generator = SpecGenerator(**gen_kwargs)

    # Build refinement prompt
    refinement_goal = (
        f"Refine the following implementation spec based on feedback.\n\n"
        f"## Original Goal\n{original.goal}\n\n"
        f"## Current Spec\n{original.to_markdown()}\n\n"
        f"## Feedback\n{feedback}\n\n"
        f"Produce an updated spec that addresses the feedback while "
        f"preserving the original intent. Keep the same tier ({original.tier.value})."
    )

    with console.status("[cyan]Refining spec via Ollama..."):
        refined = asyncio.run(
            generator.generate(refinement_goal, tier=original.tier)
        )

    # Save with new ID but reference the original
    refined.goal = original.goal  # Keep original goal
    refined_path = specs_dir / f"{refined.id}.md"
    refined_path.write_text(refined.to_markdown(), encoding="utf-8")
    json_path = specs_dir / f"{refined.id}.json"
    json_path.write_text(refined.model_dump_json(indent=2), encoding="utf-8")

    try:
        console.print(Syntax(refined.to_markdown(), "markdown", theme="monokai"))
    except (UnicodeEncodeError, Exception):
        console.print(Panel(refined.to_markdown(), title="Refined Spec", border_style="cyan"))

    console.print()
    console.print(f"[bold green]Spec refined[/bold green] in {refined.generation_time_seconds:.1f}s")
    console.print(f"  Original: {spec_id}")
    console.print(f"  New ID:   {refined.id}")
    console.print(f"  Saved to: {refined_path}")


@cli.command("spec-exec")
@click.option("--spec-id", required=True, help="Spec ID to execute.")
@click.option("--repo-path", default=".", help="Path to the repository root.")
@click.option("--agent", default=None, help="Agent CLI id for execution.")
@click.option("--max-retries", default=2, type=int, help="Max retries per phase.")
@click.option("--verify/--no-verify", default=True, help="Run semantic verification after execution.")
@click.option("--model", default=None, help="Ollama model for verification.")
def spec_exec(
    spec_id: str,
    repo_path: str,
    agent: str | None,
    max_retries: int,
    verify: bool,
    model: str | None,
) -> None:
    """Execute an existing spec through the YOLO engine.

    Takes a previously generated spec and runs it through the agent
    execution pipeline, optionally verifying the result.
    """
    import json as json_mod
    from rich.live import Live

    from orchestrator.agent_status import StatusTracker
    from orchestrator.logging_setup import setup_logging
    from orchestrator.schemas import YoloConfig
    from orchestrator.spec_generator import GeneratedSpec
    from orchestrator.verifier import SpecVerifier
    from orchestrator.yolo import YoloEngine

    setup_logging()
    root = Path(repo_path).resolve()
    specs_dir = root / ".clawsmith" / "specs"
    spec_json = specs_dir / f"{spec_id}.json"

    if not spec_json.exists():
        console.print(f"[bold red]Spec not found:[/bold red] {spec_json}")
        sys.exit(1)

    spec_obj = GeneratedSpec.model_validate_json(spec_json.read_text(encoding="utf-8"))
    plan = spec_obj.to_yolo_plan(repo_path)

    console.print()
    console.print(Panel(
        f"[bold cyan]Spec Execution[/bold cyan]  {spec_id}\n"
        f"Goal: {spec_obj.goal[:80]}\n"
        f"Phases: {len(plan.phases)}  Files: {len(spec_obj.file_changes)}",
        expand=False,
    ))
    console.print()

    tracker = StatusTracker()
    cfg = YoloConfig(
        max_retries=max_retries,
        agent_target=agent,
    )

    strip_fn = lambda: _yolo_build_strip(tracker, _YOLO_LIFECYCLE)

    with Live(strip_fn(), console=console, refresh_per_second=10, transient=True) as live:
        tracker.on_status(lambda _ev: live.update(strip_fn()))
        result = asyncio.run(
            YoloEngine().execute(spec_obj.goal, repo_path, config=cfg, status=tracker)
        )

    console.print(strip_fn())
    console.print()
    _yolo_print_results(result, cfg)

    # Verification
    if verify and result.success:
        console.print()
        console.print("[bold cyan]Running semantic verification...[/bold cyan]")

        verify_kwargs = {}
        if model:
            verify_kwargs["model"] = model
        verifier = SpecVerifier(**verify_kwargs)

        v_result, v_path = asyncio.run(
            verifier.verify_and_save(spec_obj, repo_path)
        )

        verdict_style = "bold green" if v_result.passed else "bold red"
        verdict_text = "PASSED" if v_result.passed else "FAILED"
        console.print(f"[{verdict_style}]Verdict: {verdict_text}[/{verdict_style}]  Score: {v_result.score:.0%}")
        console.print(f"  🔴 Critical: {v_result.critical_count}")
        console.print(f"  🟠 Major:    {v_result.major_count}")
        console.print(f"  Report:      {v_path}")


@cli.command("yolo")
@click.option("--goal", required=True, help="High-level software engineering goal.")
@click.option("--repo-path", default=".", help="Path to the repository root.")
@click.option("--dry-run", is_flag=True, help="Skip provider dispatch and job execution.")
@click.option("--agent", default=None, help="Agent CLI id (cursor, claude_code, gemini_cli).")
@click.option("--max-retries", default=2, type=int, help="Max retries per phase on failure.")
@click.option("--skip-planning", is_flag=True, help="Skip planning — go straight to execution.")
@click.option(
    "--no-pause", is_flag=True,
    help="Abort on phase failure instead of pausing the queue.",
)
def yolo(
    goal: str,
    repo_path: str,
    dry_run: bool,
    agent: str | None,
    max_retries: int,
    skip_planning: bool,
    no_pause: bool,
) -> None:
    """YOLO mode — autonomous multi-phase task execution via CLI agent."""
    from rich.live import Live

    from orchestrator.agent_status import StatusTracker
    from orchestrator.logging_setup import setup_logging
    from orchestrator.schemas import YoloConfig
    from orchestrator.yolo import YoloEngine

    setup_logging()
    tracker = StatusTracker()
    cfg = YoloConfig(
        skip_planning=skip_planning,
        max_retries=max_retries,
        dry_run=dry_run,
        agent_target=agent,
        pause_on_failure=not no_pause,
    )

    console.print()
    console.print(Panel(f"[bold cyan]YOLO Mode[/bold cyan]  {goal}", expand=False))
    console.print("[dim]  Execution backend: CLI Agent (agent chat)[/dim]")
    console.print()

    strip_fn = lambda: _yolo_build_strip(tracker, _YOLO_LIFECYCLE)

    with Live(strip_fn(), console=console, refresh_per_second=10, transient=True) as live:
        tracker.on_status(lambda _ev: live.update(strip_fn()))
        result = asyncio.run(
            YoloEngine().execute(goal, repo_path, config=cfg, status=tracker)
        )

    console.print(strip_fn())
    console.print()
    _yolo_print_results(result, cfg)


@cli.command("resume")
@click.option("--repo-path", default=".", help="Path to the repository root.")
@click.option("--run-id", default=None, help="Specific run ID to resume. Default: most recent.")
@click.option("--max-retries", default=2, type=int, help="Max retries per phase on failure.")
@click.option(
    "--no-pause", is_flag=True,
    help="Abort on phase failure instead of pausing the queue.",
)
def resume(
    repo_path: str,
    run_id: str | None,
    max_retries: int,
    no_pause: bool,
) -> None:
    """Resume a paused or failed YOLO run from the last successful phase."""
    from rich.live import Live

    from orchestrator.agent_status import StatusTracker
    from orchestrator.logging_setup import setup_logging
    from orchestrator.schemas import YoloConfig
    from orchestrator.yolo import YoloEngine

    setup_logging()
    tracker = StatusTracker()
    cfg = YoloConfig(
        max_retries=max_retries,
        pause_on_failure=not no_pause,
    )

    console.print()
    if run_id:
        console.print(Panel(f"[bold yellow]Resuming Run[/bold yellow]  {run_id}", expand=False))
    else:
        console.print(Panel("[bold yellow]Resuming Latest Run[/bold yellow]", expand=False))
    console.print()

    strip_fn = lambda: _yolo_build_strip(tracker, _YOLO_LIFECYCLE)

    try:
        with Live(strip_fn(), console=console, refresh_per_second=10, transient=True) as live:
            tracker.on_status(lambda _ev: live.update(strip_fn()))
            result = asyncio.run(
                YoloEngine().resume(
                    repo_path, config=cfg, status=tracker, run_id=run_id,
                )
            )

        console.print(strip_fn())
        console.print()
        _yolo_print_results(result, cfg)
    except FileNotFoundError as exc:
        console.print(f"[bold red]Cannot resume:[/bold red] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Plan / Exec / Status — first-class spec-driven workflow
# ---------------------------------------------------------------------------


@cli.command("plan")
@click.option("--goal", required=True, help="High-level goal to decompose into a plan.")
@click.option("--repo-path", default=".", help="Path to the repository root.")
@click.option("--skip-planning", is_flag=True, help="Skip design phase insertion.")
def plan_cmd(
    goal: str,
    repo_path: str,
    skip_planning: bool,
) -> None:
    """Decompose a goal into a phased plan and save it as a markdown artifact.

    Audits the repo, classifies the task, decomposes into phases with
    acceptance criteria, then persists the plan under .clawsmith/plans/.
    """
    from orchestrator.logging_setup import setup_logging
    from orchestrator.plan_writer import write_plan, load_status
    from orchestrator.planner import TaskPlanner
    from routing.classifier import TaskClassifier
    from tools.context_packer import ContextPacker
    from tools.repo_auditor import RepoAuditor
    from tools.repo_mapper import RepoMapper

    setup_logging()
    root = Path(repo_path).resolve()

    console.print()
    console.print(Panel(f"[bold cyan]Plan[/bold cyan]  {goal}", expand=False))

    with console.status("[cyan]Auditing repository..."):
        audit = RepoAuditor(root).audit()
        repo_map = RepoMapper(root).map()
        context = ContextPacker(root).pack(audit, repo_map, goal)
        classification = TaskClassifier().classify(goal, context)

    console.print(
        f"[dim]  Complexity: {classification.complexity_score:.2f}  "
        f"Type: {classification.task_type.value}  "
        f"Files: ~{classification.files_likely_touched}[/dim]"
    )

    planner = TaskPlanner()
    yolo_plan = planner.decompose(
        goal, str(root),
        context=context,
        classification=classification,
        skip_planning=skip_planning,
    )

    plan_dir = write_plan(yolo_plan, root)
    console.print()

    table = Table(title="Execution Plan", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Phase", style="cyan", min_width=20)
    table.add_column("Type", min_width=14)
    table.add_column("Complexity", width=10)
    table.add_column("Files", width=5)
    table.add_column("Criteria", min_width=30)

    for phase in yolo_plan.phases:
        criteria_str = "; ".join(phase.acceptance_criteria[:2])
        if len(phase.acceptance_criteria) > 2:
            criteria_str += f" (+{len(phase.acceptance_criteria) - 2})"
        table.add_row(
            str(phase.index + 1),
            phase.title,
            phase.task_type.value,
            f"{phase.estimated_complexity:.0%}",
            str(len(phase.files_in_scope)),
            criteria_str,
        )

    console.print(table)
    console.print()
    console.print(f"[bold green]Plan saved[/bold green]")
    console.print(f"  ID:       {yolo_plan.id}")
    console.print(f"  Phases:   {len(yolo_plan.phases)}")
    console.print(f"  Bucket:   {yolo_plan.complexity.bucket.value}")
    console.print(f"  Saved to: {plan_dir}")
    console.print()
    console.print(
        f"[dim]Next: clawsmith exec --plan-id {yolo_plan.id} --repo-path {repo_path}[/dim]"
    )


@cli.command("exec")
@click.option("--plan-id", required=True, help="Plan ID to execute.")
@click.option("--repo-path", default=".", help="Path to the repository root.")
@click.option("--phase", "phase_num", default=None, type=int, help="Execute a specific phase (1-based).")
@click.option("--agent", default=None, help="Agent CLI id (cursor, claude_code, gemini_cli).")
@click.option("--max-retries", default=2, type=int, help="Max retries per phase.")
@click.option("--dry-run", is_flag=True, help="Skip actual execution.")
@click.option("--no-pause", is_flag=True, help="Abort instead of pausing on failure.")
def exec_cmd(
    plan_id: str,
    repo_path: str,
    phase_num: int | None,
    agent: str | None,
    max_retries: int,
    dry_run: bool,
    no_pause: bool,
) -> None:
    """Execute a saved plan through the YOLO engine.

    Loads the plan from .clawsmith/plans/<plan-id>/ and runs it through
    the phase execution pipeline. Optionally targets a single phase.
    """
    from rich.live import Live

    from orchestrator.agent_status import StatusTracker
    from orchestrator.logging_setup import setup_logging
    from orchestrator.plan_writer import load_plan, update_status
    from orchestrator.schemas import YoloConfig
    from orchestrator.yolo import YoloEngine

    setup_logging()
    root = Path(repo_path).resolve()

    try:
        yolo_plan = load_plan(plan_id, root)
    except FileNotFoundError as exc:
        console.print(f"[bold red]Plan not found:[/bold red] {exc}")
        console.print("[dim]Run 'clawsmith status' to see available plans.[/dim]")
        sys.exit(1)

    if phase_num is not None:
        if phase_num < 1 or phase_num > len(yolo_plan.phases):
            console.print(
                f"[bold red]Invalid phase:[/bold red] {phase_num} "
                f"(plan has {len(yolo_plan.phases)} phases)"
            )
            sys.exit(1)
        phases_to_run = [yolo_plan.phases[phase_num - 1]]
        console.print(
            Panel(
                f"[bold cyan]Exec Phase {phase_num}[/bold cyan]  "
                f"{phases_to_run[0].title}  (plan={plan_id})",
                expand=False,
            )
        )
    else:
        phases_to_run = yolo_plan.phases
        console.print(
            Panel(
                f"[bold cyan]Exec Plan[/bold cyan]  {yolo_plan.goal}  "
                f"({len(phases_to_run)} phases)",
                expand=False,
            )
        )

    cfg = YoloConfig(
        max_retries=max_retries,
        dry_run=dry_run,
        agent_target=agent,
        pause_on_failure=not no_pause,
    )

    tracker = StatusTracker()

    strip_fn = lambda: _yolo_build_strip(tracker, _YOLO_LIFECYCLE)

    with Live(strip_fn(), console=console, refresh_per_second=10, transient=True) as live:
        tracker.on_status(lambda _ev: live.update(strip_fn()))
        result = asyncio.run(
            YoloEngine().execute(
                yolo_plan.goal, repo_path, config=cfg, status=tracker,
            )
        )

    console.print(strip_fn())
    console.print()

    update_status(
        plan_id, root,
        run_id=tracker.run_id,
        phase_status="completed" if result.success else "failed",
    )

    _yolo_print_results(result, cfg)


@cli.command("status")
@click.option("--repo-path", default=".", help="Path to the repository root.")
def status_cmd(repo_path: str) -> None:
    """Show active plans and their execution state."""
    from orchestrator.plan_writer import list_plans

    root = Path(repo_path).resolve()
    plans = list_plans(root)

    if not plans:
        console.print("[dim]No plans found. Run 'clawsmith plan --goal \"...\"' first.[/dim]")
        return

    import time as time_mod

    table = Table(title="ClawSmith Plans", show_lines=True)
    table.add_column("ID", style="cyan", min_width=14)
    table.add_column("Goal", min_width=30)
    table.add_column("Phases", width=7)
    table.add_column("Status", min_width=10)
    table.add_column("Created", min_width=19)

    for p in plans:
        created = (
            time_mod.strftime("%Y-%m-%d %H:%M", time_mod.localtime(p["created_at"]))
            if p["created_at"] else "-"
        )
        status_style = {
            "planned": "dim",
            "running": "bold cyan",
            "completed": "green",
            "failed": "bold red",
            "paused": "yellow",
        }.get(p["status"], "white")
        table.add_row(
            p["id"],
            p["goal"][:50] + ("..." if len(p["goal"]) > 50 else ""),
            str(p["phases"]),
            f"[{status_style}]{p['status']}[/{status_style}]",
            created,
        )

    console.print(table)


@cli.command("spec")
@click.option("--goal", required=True, help="What you want to build or change.")
@click.option("--repo-path", default=".", help="Path to the repository root.")
@click.option(
    "--tier", type=click.Choice(["quick", "full", "epic"]), default=None,
    help="Spec detail level. Auto-selects based on complexity if omitted.",
)
@click.option("--model", default=None, help="Ollama model name. Default: gpt-oss:20b.")
@click.option("--save/--no-save", default=True, help="Save spec to .clawsmith/specs/.")
def spec(
    goal: str,
    repo_path: str,
    tier: str | None,
    model: str | None,
    save: bool,
) -> None:
    """Generate an LLM-powered implementation spec from a goal.

    Analyses the codebase, reasons about architecture, and produces a
    structured file-level implementation plan. Uses local Ollama models
    for zero-cost spec generation.
    """
    from orchestrator.logging_setup import setup_logging
    from orchestrator.spec_generator import SpecGenerator, SpecTier
    from routing.classifier import TaskClassifier
    from tools.context_packer import ContextPacker
    from tools.repo_auditor import RepoAuditor
    from tools.repo_mapper import RepoMapper

    setup_logging()
    root = Path(repo_path).resolve()

    console.print()
    console.print(Panel(f"[bold cyan]Spec Generator[/bold cyan]  {goal}", expand=False))

    with console.status("[cyan]Auditing repository..."):
        audit_report = RepoAuditor(root).audit()
        repo_map = RepoMapper(root).map()
        context = ContextPacker(root).pack(audit_report, repo_map, goal)
        classification = TaskClassifier().classify(goal, context)

    spec_tier = SpecTier(tier) if tier else None
    gen_kwargs = {}
    if model:
        gen_kwargs["model"] = model

    generator = SpecGenerator(**gen_kwargs)

    console.print(f"[dim]  Model: {generator._model}[/dim]")
    console.print(f"[dim]  Complexity: {classification.complexity_score:.2f}[/dim]")
    auto_tier = generator._auto_tier(classification) if not spec_tier else spec_tier
    console.print(f"[dim]  Tier: {auto_tier.value}[/dim]")
    console.print()

    with console.status(f"[cyan]Generating {auto_tier.value} spec via Ollama..."):
        if save:
            result, spec_path = asyncio.run(
                generator.generate_and_save(
                    goal, repo_path, context, classification, spec_tier,
                )
            )
        else:
            result = asyncio.run(
                generator.generate(goal, context, classification, spec_tier)
            )
            spec_path = None

    # Display the spec
    try:
        console.print(Syntax(result.to_markdown(), "markdown", theme="monokai"))
    except (UnicodeEncodeError, Exception):
        # Fallback for terminals that can't handle unicode in syntax highlighting
        console.print(Panel(result.to_markdown(), title="Generated Spec", border_style="cyan"))
    console.print()

    console.print(f"[bold green]Spec generated[/bold green] in {result.generation_time_seconds:.1f}s")
    console.print(f"  ID:           {result.id}")
    console.print(f"  Tier:         {result.tier.value}")
    console.print(f"  File changes: {len(result.file_changes)}")
    console.print(f"  Phases:       {len(result.phases)}")
    console.print(f"  Risks:        {len(result.risks)}")
    if spec_path:
        console.print(f"  Saved to:     {spec_path}")


@cli.command("verify")
@click.option("--spec-id", required=True, help="Spec ID to verify against.")
@click.option("--repo-path", default=".", help="Path to the repository root.")
@click.option("--model", default=None, help="Ollama model name for verification.")
@click.option("--save/--no-save", default=True, help="Save report to .clawsmith/verifications/.")
def verify(
    spec_id: str,
    repo_path: str,
    model: str | None,
    save: bool,
) -> None:
    """Verify implementation against a generated spec.

    Compares git diffs to the spec's expected file changes and uses
    LLM analysis to categorize issues as CRITICAL, MAJOR, MINOR, or INFO.
    """
    import json as json_mod
    from orchestrator.logging_setup import setup_logging
    from orchestrator.spec_generator import GeneratedSpec
    from orchestrator.verifier import SpecVerifier

    setup_logging()
    root = Path(repo_path).resolve()
    specs_dir = root / ".clawsmith" / "specs"
    spec_json = specs_dir / f"{spec_id}.json"

    if not spec_json.exists():
        console.print(f"[bold red]Spec not found:[/bold red] {spec_json}")
        console.print(f"[dim]Available specs in {specs_dir}:[/dim]")
        if specs_dir.exists():
            for f in specs_dir.glob("*.json"):
                console.print(f"  {f.stem}")
        sys.exit(1)

    spec_obj = GeneratedSpec.model_validate_json(spec_json.read_text(encoding="utf-8"))

    console.print()
    console.print(Panel(
        f"[bold cyan]Verification[/bold cyan]  spec={spec_id}  goal={spec_obj.goal[:60]}",
        expand=False,
    ))

    verify_kwargs = {}
    if model:
        verify_kwargs["model"] = model
    verifier = SpecVerifier(**verify_kwargs)

    with console.status("[cyan]Verifying implementation against spec..."):
        if save:
            result, report_path = asyncio.run(
                verifier.verify_and_save(spec_obj, repo_path)
            )
        else:
            result = asyncio.run(verifier.verify(spec_obj, repo_path))
            report_path = None

    # Display report
    try:
        console.print(Syntax(result.to_markdown(), "markdown", theme="monokai"))
    except (UnicodeEncodeError, Exception):
        console.print(Panel(result.to_markdown(), title="Verification Report", border_style="cyan"))
    console.print()

    verdict_style = "bold green" if result.passed else "bold red"
    verdict_text = "PASSED" if result.passed else "FAILED"
    console.print(f"[{verdict_style}]Verdict: {verdict_text}[/{verdict_style}]  Score: {result.score:.0%}")
    console.print(f"  🔴 Critical: {result.critical_count}")
    console.print(f"  🟠 Major:    {result.major_count}")
    console.print(f"  Comments:    {len(result.comments)} total")
    console.print(f"  Duration:    {result.verification_time_seconds:.1f}s")
    if report_path:
        console.print(f"  Report:      {report_path}")

    if not result.passed:
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


@cli.command("onboard")
def onboard() -> None:
    """Guided first-run setup: prerequisites, config, runtime directories."""
    from orchestrator.onboard import run_onboard

    ok = run_onboard()
    if not ok:
        sys.exit(1)


@cli.command("doctor")
def doctor() -> None:
    """Run preflight checks and report ClawSmith readiness."""
    from orchestrator.doctor import run_doctor

    ok = run_doctor()
    if not ok:
        sys.exit(1)


@cli.command("smoke-test")
def smoke_test() -> None:
    """Run a quick integration check to verify the system is alive."""
    from orchestrator.smoke import run_smoke_test

    ok = run_smoke_test()
    if not ok:
        sys.exit(1)


@cli.command("start")
@click.option(
    "--host", default=None, help="Bind address (default from config).",
)
@click.option(
    "--port", default=None, type=int, help="Listen port (default from config).",
)
@click.option(
    "--webhook", is_flag=True,
    help="Also start the OpenClaw webhook receiver alongside the MCP server.",
)
def start(host: str | None, port: int | None, webhook: bool) -> None:
    """Start ClawSmith (MCP server + optional webhook receiver)."""
    import threading

    from config.config_loader import get_config
    from orchestrator.logging_setup import setup_logging

    setup_logging()
    cfg = get_config()
    effective_host = host or cfg.mcp_server.host
    effective_port = port or cfg.mcp_server.port

    console.print(
        f"[bold cyan]ClawSmith[/bold cyan] starting on "
        f"{effective_host}:{effective_port} "
        f"(transport={cfg.mcp_server.transport})"
    )

    if webhook:
        from providers.openclaw_webhook import run_webhook_server

        wh_host = cfg.openclaw.webhook_host
        wh_port = cfg.openclaw.webhook_port
        console.print(
            f"[bold cyan]Webhook receiver[/bold cyan] on "
            f"{wh_host}:{wh_port}"
        )
        wh_thread = threading.Thread(
            target=run_webhook_server,
            kwargs={"host": wh_host, "port": wh_port},
            daemon=True,
        )
        wh_thread.start()

    from mcp_server.server import mcp as mcp_app

    mcp_app.run(transport=cfg.mcp_server.transport)


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


# ---------------------------------------------------------------------------
# openclaw command group
# ---------------------------------------------------------------------------

@cli.group("openclaw")
def openclaw_group() -> None:
    """OpenClaw integration commands."""


@openclaw_group.command("webhook")
@click.option("--host", default=None, help="Bind address (default from config).")
@click.option("--port", default=None, type=int, help="Listen port (default from config).")
def openclaw_webhook(host: str | None, port: int | None) -> None:
    """Start the OpenClaw webhook receiver (standalone)."""
    from orchestrator.logging_setup import setup_logging
    from providers.openclaw_webhook import run_webhook_server

    setup_logging()
    console.print(Panel("[bold cyan]OpenClaw Webhook Receiver[/bold cyan]", expand=False))
    run_webhook_server(host=host, port=port)


@openclaw_group.command("register")
@click.option("--output", default="SKILL.md", help="Output path for SKILL.md.")
@click.option("--remote", is_flag=True, help="Also push manifest to OpenClaw gateway.")
def openclaw_register(output: str, remote: bool) -> None:
    """Register ClawSmith as an OpenClaw skill."""
    from orchestrator.logging_setup import setup_logging
    from providers.openclaw_adapter import OpenClawAdapter

    setup_logging()
    adapter = OpenClawAdapter()

    path = adapter.register_as_skill(Path(output))
    console.print(f"[bold green]Skill file written to:[/bold green] {path}")

    if remote:
        result = asyncio.run(adapter.register_with_gateway())
        if result:
            console.print(
                f"[bold green]Registered with gateway:[/bold green] "
                f"{result.get('skill_id', 'ok')}"
            )
        else:
            console.print("[yellow]Remote registration skipped (no gateway configured).[/yellow]")


@openclaw_group.command("ping")
def openclaw_ping() -> None:
    """Check connectivity to the OpenClaw gateway."""
    from config.config_loader import get_config
    from orchestrator.logging_setup import setup_logging
    from providers.openclaw_client import get_client

    setup_logging()
    cfg = get_config().openclaw

    if not cfg.gateway_url:
        console.print("[yellow]No gateway_url configured in openclaw section.[/yellow]")
        return

    console.print(f"Pinging [cyan]{cfg.gateway_url}[/cyan] ...")
    client = get_client()
    reachable = asyncio.run(client.ping())
    asyncio.run(client.close())

    if reachable:
        console.print("[bold green]Gateway is reachable.[/bold green]")
    else:
        console.print("[bold red]Gateway is not reachable.[/bold red]")


@openclaw_group.command("status")
def openclaw_status() -> None:
    """Show current OpenClaw integration status."""
    from config.config_loader import get_config
    from orchestrator.logging_setup import setup_logging

    setup_logging()
    cfg = get_config().openclaw

    table = Table(title="OpenClaw Integration Status", show_lines=True)
    table.add_column("Setting", style="cyan", min_width=20)
    table.add_column("Value")

    table.add_row("Skill Name", cfg.skill_name)
    table.add_row("MCP Endpoint", cfg.mcp_endpoint)
    table.add_row("Webhook Host:Port", f"{cfg.webhook_host}:{cfg.webhook_port}")
    table.add_row(
        "Gateway URL",
        cfg.gateway_url if cfg.gateway_url else "[dim]not configured[/dim]",
    )
    table.add_row(
        "API Key",
        "[green]set[/green]" if cfg.api_key else "[dim]not set[/dim]",
    )
    table.add_row(
        "Webhook Secret",
        "[green]set[/green]" if cfg.webhook_secret else "[yellow]not set (auth disabled)[/yellow]",
    )
    table.add_row("Auto-register", str(cfg.auto_register))
    table.add_row(
        "Callback URL",
        cfg.callback_url if cfg.callback_url else "[dim]not configured[/dim]",
    )
    table.add_row("Task Timeout", f"{cfg.task_timeout}s")

    console.print(table)

    if cfg.gateway_url:
        from providers.openclaw_client import get_client

        console.print("\nChecking gateway connectivity...", style="dim")
        client = get_client()
        reachable = asyncio.run(client.ping())
        asyncio.run(client.close())
        if reachable:
            console.print("[bold green]Gateway reachable.[/bold green]")
        else:
            console.print("[bold red]Gateway unreachable.[/bold red]")


@openclaw_group.command("manifest")
def openclaw_manifest() -> None:
    """Print the skill manifest JSON that would be sent to OpenClaw."""
    import json as json_mod

    from orchestrator.logging_setup import setup_logging
    from providers.openclaw_adapter import OpenClawAdapter

    setup_logging()
    adapter = OpenClawAdapter()
    manifest = adapter.build_skill_manifest()
    syntax = Syntax(json_mod.dumps(manifest, indent=2), "json", theme="monokai")
    console.print(syntax)


@cli.command("chat")
@click.option("--repo-path", default=".", help="Repository to work with.")
def chat(repo_path: str) -> None:
    """Start an interactive ClawSmith session (agentic TUI)."""
    from tui.session import ChatSession

    ChatSession(repo_path=repo_path).run()


# ---------------------------------------------------------------------------
# skills group
# ---------------------------------------------------------------------------

@cli.group("skills")
def skills_group() -> None:
    """Manage ClawSmith skills — list, generate, and inspect."""


@skills_group.command("list")
@click.option("--repo-path", default=".", help="Repository path.")
def skills_list(repo_path: str) -> None:
    """List all loaded skills."""
    from orchestrator.chat_runtime import ChatRuntime

    runtime = ChatRuntime(repo_path=repo_path)
    runtime.initialize()
    skills = runtime.list_skills()

    if not skills:
        console.print("[dim]No skills found. Run 'clawsmith skills generate' first.[/dim]")
        return

    table = Table(title="Loaded Skills", show_lines=True)
    table.add_column("Name", style="cyan")
    table.add_column("Source", min_width=12)
    table.add_column("Enabled")
    table.add_column("Confidence")
    table.add_column("Triggers", style="dim")

    for s in skills:
        table.add_row(
            s["name"],
            s["source_type"],
            "[green]Yes[/green]" if s["enabled"] else "[red]No[/red]",
            f"{s['confidence']:.0%}",
            ", ".join(s["triggers"][:3]),
        )
    console.print(table)


@skills_group.command("generate")
@click.option("--repo-path", default=".", help="Repository path.")
def skills_generate(repo_path: str) -> None:
    """Auto-generate skills from repository structure and dependencies."""
    from orchestrator.chat_runtime import ChatRuntime

    runtime = ChatRuntime(repo_path=repo_path)
    runtime.initialize()
    count = runtime.regenerate_skills()
    console.print(f"[bold green]Generated {count} skill(s).[/bold green]")

    for s in runtime.list_skills():
        console.print(f"  [cyan]{s['name']}[/cyan] ({s['source_type']}, {s['confidence']:.0%})")


@skills_group.command("score")
@click.option("--task", required=True, help="Task to score skills against.")
@click.option("--repo-path", default=".", help="Repository path.")
def skills_score(task: str, repo_path: str) -> None:
    """Score skills for a given task and explain selection."""
    from orchestrator.chat_runtime import ChatRuntime

    runtime = ChatRuntime(repo_path=repo_path)
    runtime.initialize()
    result = runtime.select_skills_for(task)

    if result["scored"]:
        table = Table(title="Skill Scores", show_lines=True)
        table.add_column("Skill", style="cyan")
        table.add_column("Score", style="green")
        table.add_column("Reason", style="dim")

        for s in result["scored"][:10]:
            table.add_row(s["name"], f"{s['score']:.3f}", s["reason"])
        console.print(table)

    console.print(f"\n{result.get('explanation', '')}")


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


# ---------------------------------------------------------------------------
# New commands: quickstart, detect, recommend, install-model, link-repo,
#               scope, memory (group), mutate (group), rollback
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent


@cli.command("quickstart")
def quickstart() -> None:
    """Guided first-run setup: detect hardware, recommend models, install, configure."""
    try:
        from discovery.profile import generate_profile
        from install.provisioner import ModelProvisioner
        from memory_skill.sync import MemorySync
        from recommendation.engine import RecommendationEngine
        from repo_graph.linker import RepoLinker

        console.print(Panel("[bold cyan]ClawSmith Quickstart[/bold cyan]", expand=False))

        # 1 - Hardware detection
        console.print("\n[bold]Step 1:[/bold] Detecting hardware ...")
        profile = generate_profile()

        # 2 - Display machine profile summary
        prof_table = Table(title="Machine Profile", show_lines=True)
        prof_table.add_column("Property", style="cyan")
        prof_table.add_column("Value")
        prof_table.add_row("Hardware Tier", str(profile.hardware_tier))
        prof_table.add_row("OS", f"{profile.os_info.os_name} {profile.os_info.os_version}")
        cpu_str = (
            f"{profile.cpu_info.model} "
            f"({profile.cpu_info.cores}C/{profile.cpu_info.threads}T)"
        )
        prof_table.add_row("CPU", cpu_str)
        prof_table.add_row("RAM", f"{profile.ram_info.total_gb:.1f} GB")
        if profile.gpu_info:
            prof_table.add_row(
                "GPU", f"{profile.gpu_info.model} ({profile.gpu_info.vram_gb:.1f} GB VRAM)"
            )
        prof_table.add_row("Performance", profile.expected_performance)
        prof_table.add_row("Summary", profile.summary)
        console.print(prof_table)

        # 3 - Model recommendation
        console.print("\n[bold]Step 2:[/bold] Generating model recommendations ...")
        rec = RecommendationEngine().recommend(profile)

        # 4 - Display recommended bundles
        bundle_table = Table(title="Recommended Model Bundles", show_lines=True)
        bundle_table.add_column("Pick", style="green")
        bundle_table.add_column("Model", style="cyan")
        bundle_table.add_column("Size")
        bundle_table.add_column("Runtime")
        bundle_table.add_column("Disk", style="magenta")
        bundle_table.add_column("Why")

        bundle_table.add_row(
            "Primary", rec.primary.display_name, rec.primary.parameter_count,
            rec.primary.runtime, f"{rec.primary.estimated_disk_gb:.1f} GB",
            rec.explanations.get(rec.primary.model_id, ""),
        )
        if rec.lighter:
            bundle_table.add_row(
                "Lighter", rec.lighter.display_name, rec.lighter.parameter_count,
                rec.lighter.runtime, f"{rec.lighter.estimated_disk_gb:.1f} GB",
                rec.explanations.get(rec.lighter.model_id, ""),
            )
        if rec.heavier:
            bundle_table.add_row(
                "Heavier", rec.heavier.display_name, rec.heavier.parameter_count,
                rec.heavier.runtime, f"{rec.heavier.estimated_disk_gb:.1f} GB",
                rec.explanations.get(rec.heavier.model_id, ""),
            )
        console.print(bundle_table)

        # 5-6 - Confirm & install primary bundle
        if click.confirm("Install the primary recommended bundle?", default=True):
            console.print("\n[bold]Step 3:[/bold] Installing primary bundle ...")
            result = ModelProvisioner().provision(rec.primary)
            if result.success:
                console.print("[green]Model installed successfully.[/green]")
            else:
                console.print(f"[yellow]Installation issue:[/yellow] {result.error}")

        # 7 - Detect AI tooling (already in profile)
        console.print("\n[bold]Step 4:[/bold] AI tooling detected:")
        for t in profile.toolchain.ai_tooling:
            status = "[green]found[/green]" if t.found else "[dim]not found[/dim]"
            console.print(f"  {t.name}: {status} {t.version or ''}")

        # 8 - Offer to link current repo
        if click.confirm("Link the current repository to ClawSmith?", default=True):
            console.print("\n[bold]Step 5:[/bold] Linking repository ...")
            graph_path = _REPO_ROOT / "clawsmith" / "repo-graph.json"
            linker = RepoLinker(config_path=graph_path)
            linker.link(Path.cwd())
            console.print("[green]Repository linked.[/green]")

        # 9 - Sync memory
        console.print("\n[bold]Step 6:[/bold] Syncing memory ...")
        MemorySync(_REPO_ROOT).full_sync(profile=profile)
        console.print("[green]Memory synced.[/green]")

        # 10 - Welcome summary
        console.print(
            Panel(
                "[bold green]ClawSmith is ready![/bold green]\n"
                "Run [cyan]clawsmith doctor[/cyan] to verify, or "
                "[cyan]clawsmith run-task --task '...'[/cyan] to get started.",
                title="Setup Complete",
                expand=False,
            )
        )
    except Exception as exc:
        console.print(f"[bold red]Quickstart failed:[/bold red] {exc}")
        sys.exit(1)


@cli.command("detect")
@click.option("--json-output", is_flag=True, help="Output as JSON.")
def detect(json_output: bool) -> None:
    """Detect hardware, software, and AI tooling on this machine."""
    try:
        from discovery.profile import generate_profile

        profile = generate_profile()

        if json_output:
            console.print(profile.model_dump_json(indent=2))
        else:
            table = Table(title="Environment Detection", show_lines=True)
            table.add_column("Property", style="cyan")
            table.add_column("Value")
            table.add_row("Hardware Tier", str(profile.hardware_tier))
            table.add_row(
                "OS",
                f"{profile.os_info.os_name} {profile.os_info.os_version} "
                f"({profile.os_info.architecture})",
            )
            table.add_row("Shell", profile.os_info.shell)
            table.add_row("WSL", str(profile.os_info.is_wsl))
            table.add_row(
                "CPU",
                f"{profile.cpu_info.model} ({profile.cpu_info.cores}C/{profile.cpu_info.threads}T)",
            )
            table.add_row(
                "RAM",
                f"{profile.ram_info.total_gb:.1f} GB total, "
                f"{profile.ram_info.available_gb:.1f} GB available",
            )
            if profile.gpu_info:
                table.add_row(
                    "GPU",
                    f"{profile.gpu_info.model} ({profile.gpu_info.vram_gb:.1f} GB VRAM, "
                    f"{profile.gpu_info.compute_backend})",
                )
            else:
                table.add_row("GPU", "None detected")
            for v in profile.storage_info.volumes:
                table.add_row(
                    f"Disk {v.device_id}",
                    f"{v.free_gb:.1f} GB free / {v.total_gb:.1f} GB total",
                )
            table.add_row(
                "Recommended Model Path",
                profile.storage_info.recommended_model_path or "N/A",
            )
            table.add_row("Feasible Model Sizes", ", ".join(profile.feasible_model_sizes))
            table.add_row("Recommended Backends", ", ".join(profile.recommended_backends))
            table.add_row("Performance", profile.expected_performance)
            if profile.likely_bottlenecks:
                table.add_row("Bottlenecks", "; ".join(profile.likely_bottlenecks))

            devtools = [t for t in profile.toolchain.developer_tools if t.found]
            if devtools:
                table.add_row(
                    "Dev Tools",
                    ", ".join(f"{t.name} {t.version or ''}" for t in devtools),
                )
            ai = [t for t in profile.toolchain.ai_tooling if t.found]
            if ai:
                table.add_row("AI Tooling", ", ".join(f"{t.name} {t.version or ''}" for t in ai))
            runtimes = [t for t in profile.toolchain.inference_runtimes if t.found]
            if runtimes:
                table.add_row(
                    "Inference Runtimes",
                    ", ".join(f"{t.name} {t.version or ''}" for t in runtimes),
                )

            console.print(table)
            console.print(f"\n[bold]Summary:[/bold] {profile.summary}")
    except Exception as exc:
        console.print(f"[bold red]Detection failed:[/bold red] {exc}")
        sys.exit(1)


@cli.command("recommend")
@click.option("--intent", default="coding", help="Model intent: coding, general, reasoning.")
@click.option("--json-output", is_flag=True, help="Output as JSON.")
def recommend(intent: str, json_output: bool) -> None:
    """Recommend local LLMs based on your hardware."""
    try:
        from discovery.profile import generate_profile
        from recommendation.engine import RecommendationEngine

        profile = generate_profile()
        rec = RecommendationEngine().recommend(profile, intent=intent)

        if json_output:
            console.print(rec.model_dump_json(indent=2))
        else:
            console.print(f"\n[bold]Machine:[/bold] {rec.machine_summary}")
            console.print(f"[bold]Hardware tier:[/bold] {rec.hardware_tier}\n")

            table = Table(title=f"Recommended Models (intent={intent})", show_lines=True)
            table.add_column("Pick", style="green")
            table.add_column("Model", style="cyan")
            table.add_column("Size")
            table.add_column("Quant")
            table.add_column("Runtime")
            table.add_column("Disk", style="magenta")
            table.add_column("RAM", style="yellow")
            table.add_column("Context")

            for label, bundle in [
                ("Primary", rec.primary),
                ("Lighter", rec.lighter),
                ("Heavier", rec.heavier),
            ]:
                if bundle:
                    table.add_row(
                        label, bundle.display_name, bundle.parameter_count,
                        bundle.quantization, bundle.runtime,
                        f"{bundle.estimated_disk_gb:.1f} GB",
                        f"{bundle.estimated_ram_gb:.1f} GB",
                        f"{bundle.context_size:,}",
                    )
            console.print(table)

            console.print("\n[bold]Explanations:[/bold]")
            for model_id, explanation in rec.explanations.items():
                console.print(f"  [cyan]{model_id}:[/cyan] {explanation}")
    except Exception as exc:
        console.print(f"[bold red]Recommendation failed:[/bold red] {exc}")
        sys.exit(1)


@cli.command("install-model")
@click.option("--model-id", default=None, help="Specific model ID from catalog.")
@click.option("--target-path", default=None, help="Custom install path.")
@click.option("--runtime", default="ollama", help="Runtime to use.")
def install_model(model_id: str | None, target_path: str | None, runtime: str) -> None:
    """Install a local LLM model."""
    try:
        from install.provisioner import ModelProvisioner
        from recommendation.catalog import get_catalog

        provisioner = ModelProvisioner()

        if model_id is None:
            from discovery.profile import generate_profile
            from recommendation.engine import RecommendationEngine

            console.print("[bold]No model specified -- running recommendation ...[/bold]")
            profile = generate_profile()
            rec = RecommendationEngine().recommend(profile)
            bundle = rec.primary
            console.print(
                f"  Selected model: [cyan]{bundle.display_name}[/cyan] ({bundle.model_id})"
            )
        else:
            catalog = get_catalog()
            matched = [b for b in catalog if b.model_id == model_id]
            if not matched:
                console.print(f"[red]Model '{model_id}' not found in catalog.[/red]")
                console.print("Available models:")
                for b in catalog:
                    console.print(f"  {b.model_id} - {b.display_name}")
                sys.exit(1)
            bundle = matched[0]

        console.print(f"  Model:   {bundle.display_name}")
        console.print(f"  Size:    {bundle.parameter_count} ({bundle.quantization})")
        console.print(f"  Disk:    ~{bundle.estimated_disk_gb:.1f} GB")
        console.print(f"  Runtime: {bundle.runtime}")

        if not click.confirm(f"\nInstall {bundle.display_name}?", default=True):
            console.print("[yellow]Installation cancelled.[/yellow]")
            return

        console.print(f"\n[bold]Installing {bundle.display_name} ...[/bold]")
        result = provisioner.provision(bundle, target_path=target_path)

        if result.success:
            console.print("[bold green]Model installed successfully.[/bold green]")
            console.print(f"  Path: {result.install_path}")
            if result.disk_used_gb:
                console.print(f"  Disk used: {result.disk_used_gb:.2f} GB")
        else:
            console.print(f"[bold red]Installation failed:[/bold red] {result.error}")
            sys.exit(1)
    except Exception as exc:
        console.print(f"[bold red]Installation failed:[/bold red] {exc}")
        sys.exit(1)


@cli.command("link-repo")
@click.argument("repo_path", type=click.Path(exists=True))
@click.option("--role", default="", help="Repo role: primary, shared-lib, cli, service.")
@click.option("--description", default="", help="Short description.")
def link_repo(repo_path: str, role: str, description: str) -> None:
    """Add a repository to the ClawSmith workspace graph."""
    try:
        from repo_graph.linker import RepoLinker

        graph_path = _REPO_ROOT / "clawsmith" / "repo-graph.json"
        linker = RepoLinker(config_path=graph_path)
        node = linker.link(Path(repo_path).resolve(), role=role, description=description)
        console.print(f"[bold green]Linked:[/bold green] {node.name} ({node.path})")
        if node.languages:
            console.print(f"  Languages: {', '.join(node.languages)}")
        if role:
            console.print(f"  Role: {role}")
        if description:
            console.print(f"  Description: {description}")
    except Exception as exc:
        console.print(f"[bold red]Link failed:[/bold red] {exc}")
        sys.exit(1)


@cli.command("scope")
@click.option("--repo", default=".", help="Primary repo for scope evaluation.")
@click.option("--task", default=None, help="Task description for scoping.")
def scope(repo: str, task: str | None) -> None:
    """View or create scope contracts for cross-repo work."""
    try:
        from scope_engine.engine import ScopeEngine

        engine = ScopeEngine(workspace_root=Path(repo).resolve())

        if task:
            repo_name = Path(repo).resolve().name
            console.print(f"[bold]Creating scope contract for:[/bold] {task}")
            contract = engine.create_contract(task, primary_repo=repo_name)
            summary = engine.get_scope_summary(contract)
            console.print(Panel(summary, title="Scope Contract", expand=False))
            saved = engine.save_contract(contract)
            console.print(f"[dim]Contract saved to: {saved}[/dim]")
        else:
            scopes_dir = Path(repo).resolve() / ".clawsmith" / "scopes"
            if not scopes_dir.exists() or not list(scopes_dir.glob("*.json")):
                console.print("  [dim]No active contracts. Use --task to create one.[/dim]")
                return
            for f in sorted(scopes_dir.glob("*.json")):
                contract = engine.load_contract(f)
                console.print(
                    f"  [cyan]{contract.task_id}[/cyan]  primary={contract.primary_repo}  "
                    f"repos={len(contract.repos)}",
                )
    except Exception as exc:
        console.print(f"[bold red]Scope operation failed:[/bold red] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# memory group
# ---------------------------------------------------------------------------

@cli.group("memory")
def memory_group() -> None:
    """Manage persistent architecture and preference memory."""


@memory_group.command("sync")
def memory_sync() -> None:
    """Sync hardware profile and preferences to memory files."""
    try:
        from discovery.profile import generate_profile
        from memory_skill.sync import MemorySync

        console.print("[bold]Detecting hardware ...[/bold]")
        profile = generate_profile()
        console.print("[bold]Syncing memory ...[/bold]")
        written = MemorySync(_REPO_ROOT).full_sync(profile=profile)
        console.print(
            f"[bold green]Memory synced successfully.[/bold green] ({len(written)} files written)"
        )
        for p in written:
            console.print(f"  {p}")
    except Exception as exc:
        console.print(f"[bold red]Memory sync failed:[/bold red] {exc}")
        sys.exit(1)


@memory_group.command("show")
def memory_show() -> None:
    """Display current memory state."""
    try:
        from memory_skill.reader import MemoryReader

        reader = MemoryReader(_REPO_ROOT)
        arch = reader.read_architecture()
        prefs = reader.read_preferences()
        tooling = reader.read_tooling_profile()

        if arch:
            table = Table(title="Architecture", show_lines=True)
            table.add_column("Property", style="cyan")
            table.add_column("Value")
            table.add_row("Hardware Tier", arch.hardware_tier)
            table.add_row("OS", f"{arch.os_name} {arch.os_version}")
            table.add_row("CPU", arch.cpu_summary)
            table.add_row("RAM", f"{arch.ram_gb:.1f} GB")
            table.add_row("GPU", arch.gpu_summary or "None")
            table.add_row("Models", str(len(arch.installed_models)))
            table.add_row("Runtimes", str(len(arch.installed_runtimes)))
            table.add_row("Repos", str(len(arch.repos)))
            console.print(table)
        else:
            console.print("[dim]No architecture data. Run 'clawsmith memory sync' first.[/dim]")

        if prefs:
            console.print("\n[bold]Preferences:[/bold]")
            if prefs.preferred_local_models:
                console.print(f"  Local models: {', '.join(prefs.preferred_local_models)}")
            console.print(f"  Model routing: {prefs.default_model_routing}")
            console.print(f"  Task execution: {prefs.default_task_execution}")

        if tooling:
            found_tools = {k: v for k, v in tooling.developer_tools.items() if v}
            if found_tools:
                console.print(
                    f"\n[bold]Developer tools:[/bold] "
                    f"{', '.join(f'{k} {v}' for k, v in found_tools.items())}",
                )
    except Exception as exc:
        console.print(f"[bold red]Memory show failed:[/bold red] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# mutate group
# ---------------------------------------------------------------------------

@cli.group("mutate")
def mutate_group() -> None:
    """Manage guarded configuration mutations."""


@mutate_group.command("propose")
@click.option("--type", "mutation_type", required=True, help="Mutation type.")
@click.option("--reason", required=True, help="Reason for the change.")
@click.option("--target", required=True, help="Target file or scope.")
def mutate_propose(mutation_type: str, reason: str, target: str) -> None:
    """Propose a configuration mutation."""
    try:
        from mutation_engine.engine import MutationEngine
        from mutation_engine.models import MutationProposal, MutationType

        if mutation_type not in [m.value for m in MutationType]:
            valid = ", ".join(m.value for m in MutationType)
            console.print(f"[red]Invalid mutation type. Valid types: {valid}[/red]")
            sys.exit(1)

        engine = MutationEngine(workspace_root=_REPO_ROOT)
        proposal = MutationProposal(
            mutation_type=MutationType(mutation_type),
            reason=reason,
            target_scope=target,
            affected_files=[target],
        )
        result = engine.propose(proposal)
        console.print(f"[bold green]Proposal created:[/bold green] {result.id}")
        console.print(f"  Type:   {result.mutation_type}")
        console.print(f"  Target: {result.target_scope}")
        console.print(f"  Status: {result.status}")
    except PermissionError as exc:
        console.print(f"[bold yellow]Blocked:[/bold yellow] {exc}")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[bold red]Propose failed:[/bold red] {exc}")
        sys.exit(1)


@mutate_group.command("list")
def mutate_list() -> None:
    """List all mutation proposals."""
    try:
        from mutation_engine.engine import MutationEngine

        engine = MutationEngine(workspace_root=_REPO_ROOT)
        proposals = engine.list_proposals()

        if not proposals:
            console.print("[dim]No mutation proposals found.[/dim]")
            return

        table = Table(title="Mutation Proposals", show_lines=True)
        table.add_column("ID", style="cyan")
        table.add_column("Type")
        table.add_column("Target")
        table.add_column("Status", style="magenta")
        table.add_column("Created")
        for p in proposals:
            table.add_row(p.id, p.mutation_type, p.target_scope, p.status, p.created_at[:19])
        console.print(table)
    except Exception as exc:
        console.print(f"[bold red]List failed:[/bold red] {exc}")
        sys.exit(1)


@mutate_group.command("apply")
@click.argument("proposal_id")
def mutate_apply(proposal_id: str) -> None:
    """Apply an approved mutation proposal."""
    try:
        from mutation_engine.engine import MutationEngine

        engine = MutationEngine(workspace_root=_REPO_ROOT)
        result = engine.apply(proposal_id)
        console.print(f"[bold green]Proposal {proposal_id} applied successfully.[/bold green]")
        console.print(f"  Files changed: {len(result.after_snapshot)}")
        console.print(f"  Rollback: {result.rollback_instructions}")
    except Exception as exc:
        console.print(f"[bold red]Apply failed:[/bold red] {exc}")
        sys.exit(1)


@mutate_group.command("approve")
@click.argument("proposal_id")
def mutate_approve(proposal_id: str) -> None:
    """Approve a validated mutation proposal."""
    try:
        from mutation_engine.engine import MutationEngine

        engine = MutationEngine(workspace_root=_REPO_ROOT)
        result = engine.approve(proposal_id)
        console.print(f"[bold green]Proposal {proposal_id} approved.[/bold green]")
        console.print(f"  Status: {result.status}")
    except Exception as exc:
        console.print(f"[bold red]Approve failed:[/bold red] {exc}")
        sys.exit(1)


@cli.command("rollback")
@click.argument("proposal_id")
def rollback(proposal_id: str) -> None:
    """Roll back an applied mutation."""
    try:
        from mutation_engine.engine import MutationEngine

        engine = MutationEngine(workspace_root=_REPO_ROOT)
        result = engine.rollback(proposal_id)
        console.print(f"[bold green]Proposal {proposal_id} rolled back successfully.[/bold green]")
        console.print(f"  Files restored: {len(result.after_snapshot)}")
    except Exception as exc:
        console.print(f"[bold red]Rollback failed:[/bold red] {exc}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Self-update
# ---------------------------------------------------------------------------


@cli.command("update")
@click.option(
    "--branch", default=None,
    help="Remote branch to pull (default: current branch's upstream).",
)
@click.option("--force", is_flag=True, help="Discard local changes before pulling.")
def update(branch: str | None, force: bool) -> None:
    """Pull the latest source from Git and re-install ClawSmith."""
    import shutil
    import subprocess

    if not shutil.which("git"):
        console.print("[bold red]git is not on PATH — cannot update.[/bold red]")
        sys.exit(1)

    git_dir = _REPO_ROOT / ".git"
    if not git_dir.exists():
        console.print(
            "[bold red]Not a git checkout.[/bold red]  "
            "Clone from source first, then run update."
        )
        sys.exit(1)

    console.print("[bold cyan]ClawSmith self-update[/bold cyan]\n")

    # 1 — Save current version for comparison
    try:
        old_head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        old_head = "unknown"

    # 2 — Optional hard reset
    if force:
        console.print("  Discarding local changes...", style="dim")
        subprocess.run(
            ["git", "reset", "--hard"],
            cwd=str(_REPO_ROOT), capture_output=True,
        )
        subprocess.run(
            ["git", "clean", "-fd"],
            cwd=str(_REPO_ROOT), capture_output=True,
        )

    # 3 — Fetch + pull
    pull_cmd = ["git", "pull", "--ff-only"]
    if branch:
        pull_cmd = ["git", "pull", "--ff-only", "origin", branch]

    console.print("  Pulling latest...", style="dim")
    pull = subprocess.run(
        pull_cmd,
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )

    if pull.returncode != 0:
        stderr = pull.stderr.strip()
        if "not possible to fast-forward" in stderr or "divergent" in stderr:
            console.print(
                "[yellow]Cannot fast-forward.[/yellow] "
                "Use [bold]--force[/bold] to discard local changes, "
                "or merge manually."
            )
        else:
            console.print(f"[bold red]git pull failed:[/bold red] {stderr}")
        sys.exit(1)

    try:
        new_head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(_REPO_ROOT), capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        new_head = "unknown"

    if old_head == new_head:
        console.print(f"  Already up to date ({old_head}).")
    else:
        console.print(f"  Updated [cyan]{old_head}[/cyan] → [cyan]{new_head}[/cyan]")

        try:
            log_out = subprocess.run(
                ["git", "log", "--oneline", f"{old_head}..{new_head}"],
                cwd=str(_REPO_ROOT), capture_output=True, text=True,
            ).stdout.strip()
            if log_out:
                console.print()
                for line in log_out.splitlines()[:15]:
                    console.print(f"    {line}", style="dim")
                total = log_out.count("\n") + 1
                if total > 15:
                    console.print(f"    ... and {total - 15} more", style="dim")
        except Exception:
            pass

    # 4 — Re-install package
    console.print("\n  Re-installing package...", style="dim")
    pip_exe = shutil.which("pip") or shutil.which("pip3") or sys.executable
    pip_cmd = (
        [pip_exe, "install", "-e", ".[dev]"]
        if pip_exe != sys.executable
        else [sys.executable, "-m", "pip", "install", "-e", ".[dev]"]
    )

    pip_result = subprocess.run(
        pip_cmd,
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )

    if pip_result.returncode != 0:
        console.print(
            f"[bold red]pip install failed:[/bold red]\n{pip_result.stderr.strip()}"
        )
        sys.exit(1)

    console.print(
        "\n[bold green]Update complete.[/bold green]  "
        "Restart any running ClawSmith sessions to use the new version."
    )
