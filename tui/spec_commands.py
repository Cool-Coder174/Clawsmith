"""Spec-aware commands for the ClawSmith TUI.

Handles: /spec, /plan, /verify, /yolo
Integrates the spec_generator, verifier, and YOLO engine into the chat TUI.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from orchestrator.agent_status import AgentPhase, StatusTracker
from orchestrator.spec_generator import GeneratedSpec, SpecGenerator, SpecTier
from orchestrator.verifier import SpecVerifier, VerificationReport
from orchestrator.yolo import YoloEngine
from tools.context_packer import ContextPacker
from tools.repo_auditor import RepoAuditor
from tools.repo_mapper import RepoMapper
from routing.classifier import TaskClassifier


async def generate_spec_from_goal(
    goal: str,
    repo_path: str | Path,
    tier: SpecTier | None = None,
) -> tuple[GeneratedSpec, Path]:
    """Generate a structured spec from a goal and save it.
    
    Returns (spec, spec_path).
    """
    root = Path(repo_path).resolve()
    
    # Gather context
    audit = RepoAuditor(root).audit()
    repo_map = RepoMapper(root).map()
    context = ContextPacker(root).pack(audit, repo_map, goal)
    classification = TaskClassifier().classify(goal, context)
    
    # Generate spec
    generator = SpecGenerator()
    spec = await generator.generate(goal, context, classification, tier)
    
    # Save spec
    specs_dir = root / ".clawsmith" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)
    
    spec_path = specs_dir / f"{spec.id}.md"
    spec_path.write_text(spec.to_markdown(), encoding="utf-8")
    
    json_path = specs_dir / f"{spec.id}.json"
    json_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    
    return spec, spec_path


async def verify_spec(
    spec_id: str,
    repo_path: str | Path,
) -> VerificationReport:
    """Verify a spec against the current working tree diff."""
    root = Path(repo_path).resolve()
    
    # Load spec
    spec_file = root / ".clawsmith" / "specs" / f"{spec_id}.json"
    if not spec_file.exists():
        spec_file = root / ".clawsmith" / "specs" / f"{spec_id}.md"
    
    import json
    if spec_file.suffix == ".json":
        spec_data = json.loads(spec_file.read_text(encoding="utf-8"))
    else:
        # Try to extract JSON from markdown
        from orchestrator.spec_generator import SpecGenerator
        text = spec_file.read_text(encoding="utf-8")
        data = SpecGenerator._extract_json(text)
        if data is None:
            raise FileNotFoundError(f"Could not parse spec {spec_id}")
        spec_data = data
    
    # Convert to GeneratedSpec
    spec = GeneratedSpec.model_validate(spec_data)
    
    # Run verification
    verifier = SpecVerifier()
    report = await verifier.verify(spec, str(root))
    
    # Save report
    reports_dir = root / ".clawsmith" / "verifications"
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    from time import strftime
    ts = strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"verify_{spec_id}_{ts}.md"
    report_path.write_text(report.to_markdown(), encoding="utf-8")
    
    return report


def format_spec_summary(spec: GeneratedSpec) -> str:
    """Format a spec as a readable summary for chat output."""
    lines = [
        f"## 📋 Spec: {spec.goal}",
        f"**ID:** `{spec.id}`  |  **Tier:** {spec.tier.value}  |  **Model:** {spec.model_used}",
        f"**Generated in:** {spec.generation_time_seconds:.1f}s",
        "",
    ]
    
    if spec.summary:
        lines.append(f"### Summary\n{spec.summary}\n")
    
    if spec.architecture_impact:
        lines.append(f"### Architecture Impact\n{spec.architecture_impact}\n")
    
    if spec.file_changes:
        lines.append(f"### File Changes ({len(spec.file_changes)} files)")
        for fc in spec.file_changes[:10]:
            lines.append(f"- `{fc.path}` ({fc.action}): {fc.description}")
        if len(spec.file_changes) > 10:
            lines.append(f"  _...and {len(spec.file_changes) - 10} more_")
        lines.append("")
    
    if spec.phases:
        lines.append(f"### Phases ({len(spec.phases)})")
        for phase in spec.phases:
            lines.append(f"**Phase {phase.index + 1}: {phase.title}**")
            lines.append(f"{phase.objective}")
            if phase.acceptance_criteria:
                for ac in phase.acceptance_criteria[:3]:
                    lines.append(f"  - {ac}")
            lines.append("")
    
    if spec.risks:
        lines.append(f"### ⚠️ Risks ({len(spec.risks)})")
        for risk in spec.risks[:3]:
            lines.append(f"- {risk}")
        lines.append("")
    
    if spec.open_questions:
        lines.append(f"### ❓ Open Questions ({len(spec.open_questions)})")
        for q in spec.open_questions[:3]:
            lines.append(f"- {q}")
        lines.append("")
    
    return "\n".join(lines)


def format_verification_report(report: VerificationReport) -> str:
    """Format a verification report for chat output."""
    verdict = "✅ **PASSED**" if report.passed else "❌ **FAILED**"
    
    lines = [
        f"## 🔍 Verification Report",
        f"**Spec:** `{report.spec_id}`",
        f"**Verdict:** {verdict}  |  **Score:** {report.score:.0%}",
        f"**Changed files:** {len(report.changed_files)}  |  **Expected:** {len(report.expected_files)}",
        "",
    ]
    
    if report.comments:
        severity_order = ["CRITICAL", "MAJOR", "MINOR", "INFO"]
        by_severity: dict = {}
        for c in report.comments:
            sev = c.severity.value
            if sev not in by_severity:
                by_severity[sev] = []
            by_severity[sev].append(c)
        
        for sev in severity_order:
            if sev not in by_severity:
                continue
            emoji = {"CRITICAL": "🔴", "MAJOR": "🟠", "MINOR": "🟡", "INFO": "🔵"}[sev]
            lines.append(f"### {emoji} {sev} ({len(by_severity[sev])})")
            for c in by_severity[sev]:
                loc = f" `{c.file}`" if c.file else ""
                lines.append(f"- **[{c.category}]**{loc}: {c.message}")
                if c.suggestion:
                    lines.append(f"  - 💡 {c.suggestion}")
            lines.append("")
    
    if report.diff_summary:
        lines.append("### Diff Summary")
        lines.append(f"```\n{report.diff_summary}\n```\n")
    
    return "\n".join(lines)


def list_specs(repo_path: str | Path) -> list[dict]:
    """List all specs in .clawsmith/specs/."""
    root = Path(repo_path).resolve()
    specs_dir = root / ".clawsmith" / "specs"
    
    if not specs_dir.exists():
        return []
    
    specs = []
    for f in sorted(specs_dir.glob("*.json"), reverse=True):
        import json
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            specs.append({
                "id": data.get("id", f.stem),
                "goal": data.get("goal", "Unknown"),
                "tier": data.get("tier", "unknown"),
                "file_count": len(data.get("file_changes", [])),
                "phase_count": len(data.get("phases", [])),
                "path": str(f),
            })
        except Exception:
            continue
    
    return specs
