"""Persists YoloPlan decompositions as human-readable markdown artifacts.

Plans are stored under ``.clawsmith/plans/<plan-id>/`` with:
    plan.md       — the full plan in markdown
    plan.json     — machine-readable snapshot for ``exec`` and ``verify``
    status.json   — mutable execution state (phase completion, findings)
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from orchestrator.logging_setup import get_logger
from orchestrator.schemas import YoloPlan

logger = get_logger("plan_writer")

PLANS_DIR = ".clawsmith" / Path("plans")


def _plans_root(repo_path: str | Path) -> Path:
    return Path(repo_path).resolve() / ".clawsmith" / "plans"


def write_plan(plan: YoloPlan, repo_path: str | Path) -> Path:
    """Persist a YoloPlan as markdown + JSON artifacts.

    Returns the directory containing the plan files.
    """
    plan_dir = _plans_root(repo_path) / plan.id
    plan_dir.mkdir(parents=True, exist_ok=True)

    plan_dir_path = plan_dir / "plan.json"
    plan_dir_path.write_text(plan.model_dump_json(indent=2), encoding="utf-8")

    md = _render_markdown(plan)
    (plan_dir / "plan.md").write_text(md, encoding="utf-8")

    status = _initial_status(plan)
    (plan_dir / "status.json").write_text(
        json.dumps(status, indent=2), encoding="utf-8",
    )

    logger.info("Plan written to %s", plan_dir)
    return plan_dir


def load_plan(plan_id: str, repo_path: str | Path) -> YoloPlan:
    """Load a previously saved plan by ID."""
    plan_path = _plans_root(repo_path) / plan_id / "plan.json"
    if not plan_path.exists():
        raise FileNotFoundError(f"Plan not found: {plan_path}")
    return YoloPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))


def load_status(plan_id: str, repo_path: str | Path) -> dict:
    """Load the mutable status for a plan."""
    status_path = _plans_root(repo_path) / plan_id / "status.json"
    if not status_path.exists():
        raise FileNotFoundError(f"Plan status not found: {status_path}")
    return json.loads(status_path.read_text(encoding="utf-8"))


def update_status(
    plan_id: str,
    repo_path: str | Path,
    *,
    phase_index: int | None = None,
    phase_status: str | None = None,
    run_id: str | None = None,
    findings: list[dict] | None = None,
) -> dict:
    """Update execution status for a plan. Returns the updated status dict."""
    status = load_status(plan_id, repo_path)

    if run_id:
        status["run_id"] = run_id
    status["updated_at"] = time.time()

    if phase_index is not None and phase_status:
        for ps in status.get("phases", []):
            if ps["index"] == phase_index:
                ps["status"] = phase_status
                break

    if findings:
        status.setdefault("findings", []).extend(findings)

    status_path = _plans_root(repo_path) / plan_id / "status.json"
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status


def list_plans(repo_path: str | Path) -> list[dict]:
    """List all saved plans with summary info."""
    root = _plans_root(repo_path)
    if not root.exists():
        return []

    plans = []
    for plan_dir in sorted(root.iterdir()):
        if not plan_dir.is_dir():
            continue
        status_path = plan_dir / "status.json"
        plan_path = plan_dir / "plan.json"
        if not plan_path.exists():
            continue

        try:
            status = (
                json.loads(status_path.read_text(encoding="utf-8"))
                if status_path.exists() else {}
            )
            plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
            plans.append({
                "id": plan_dir.name,
                "goal": plan_data.get("goal", ""),
                "phases": len(plan_data.get("phases", [])),
                "status": status.get("overall_status", "planned"),
                "created_at": plan_data.get("created_at", 0),
            })
        except Exception as exc:
            logger.warning("Skipping malformed plan %s: %s", plan_dir.name, exc)

    return plans


def _render_markdown(plan: YoloPlan) -> str:
    """Render a YoloPlan as a human-readable markdown document."""
    lines = [
        f"# Plan: {plan.goal}",
        "",
        f"**ID:** `{plan.id}`  ",
        f"**Complexity:** {plan.complexity.bucket.value} "
        f"(score={plan.complexity.raw_score:.2f}, "
        f"recommended phases={plan.complexity.recommended_phases})  ",
        f"**Created:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(plan.created_at))}  ",
        "",
        "---",
        "",
    ]

    if plan.complexity.reasoning:
        lines += [
            "## Analysis",
            plan.complexity.reasoning,
            "",
        ]

    lines.append("## Phases")
    lines.append("")

    for phase in plan.phases:
        lines.append(f"### Phase {phase.index + 1}: {phase.title}")
        lines.append("")
        lines.append(f"**Objective:** {phase.objective}  ")
        lines.append(f"**Type:** {phase.task_type.value}  ")
        lines.append(
            f"**Estimated complexity:** {phase.estimated_complexity:.0%}  "
        )

        if phase.files_in_scope:
            lines.append("")
            lines.append("**Files in scope:**")
            for f in phase.files_in_scope:
                lines.append(f"- `{f}`")

        if phase.acceptance_criteria:
            lines.append("")
            lines.append("**Acceptance criteria:**")
            for ac in phase.acceptance_criteria:
                lines.append(f"- {ac}")

        if phase.depends_on:
            lines.append(
                f"\n**Depends on:** {', '.join(phase.depends_on)}"
            )

        lines.append("")

    return "\n".join(lines)


def _initial_status(plan: YoloPlan) -> dict:
    return {
        "plan_id": plan.id,
        "goal": plan.goal,
        "overall_status": "planned",
        "created_at": plan.created_at,
        "updated_at": plan.created_at,
        "run_id": None,
        "phases": [
            {
                "index": p.index,
                "title": p.title,
                "status": "pending",
            }
            for p in plan.phases
        ],
        "findings": [],
    }
