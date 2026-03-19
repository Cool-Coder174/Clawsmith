"""Spec-driven CLI commands for ClawSmith."""

import asyncio
import json
import os
from pathlib import Path

import click

from orchestrator.spec_generator import GeneratedSpec, SpecGenerator, SpecTier
from verification.spec_validation import validate_spec_against_code


def load_spec_json(spec_path: str) -> dict:
    """Load a spec from JSON file."""
    with open(spec_path, 'r') as f:
        return json.load(f)


async def generate_spec_async(
    goal: str,
    repo_path: str,
    tier: SpecTier | None = None,
) -> tuple[GeneratedSpec, Path]:
    """Generate a spec asynchronously."""
    root = Path(repo_path).resolve()

    from tools.context_packer import ContextPacker
    from tools.repo_auditor import RepoAuditor
    from tools.repo_mapper import RepoMapper
    from routing.classifier import TaskClassifier

    audit = RepoAuditor(root).audit()
    repo_map = RepoMapper(root).map()
    context = ContextPacker(root).pack(audit, repo_map, goal)
    classification = TaskClassifier().classify(goal, context)

    generator = SpecGenerator()
    spec = await generator.generate(goal, context, classification, tier)

    specs_dir = root / ".clawsmith" / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    spec_path = specs_dir / f"{spec.id}.md"
    spec_path.write_text(spec.to_markdown(), encoding="utf-8")

    json_path = specs_dir / f"{spec.id}.json"
    json_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")

    return spec, spec_path


@click.group()
def cli():
    """Clawsmith CLI for AI orchestration."""
    pass


@cli.command()
@click.argument('goal')
@click.option('--output', default=None, help='Output spec file path (default: .clawsmith/specs/<id>.md)')
@click.option('--tier', type=click.Choice(['quick', 'full', 'epic']), default=None, help='Spec tier')
@click.option('--repo', default='.', help='Repository path')
def generate(goal, output, tier, repo):
    """Generate a product spec from user intent."""
    tier_enum = SpecTier(tier) if tier else None

    spec, spec_path = asyncio.run(generate_spec_async(goal, repo, tier_enum))

    output_path = Path(output) if output else spec_path
    if output and output != str(spec_path):
        import shutil
        shutil.copy(spec_path, output_path)

    click.echo(f"✅ Spec generated: {spec.id}")
    click.echo(f"   Goal: {spec.goal}")
    click.echo(f"   Tier: {spec.tier.value}")
    click.echo(f"   Files: {len(spec.file_changes)}")
    click.echo(f"   Phases: {len(spec.phases)}")
    click.echo(f"   Time: {spec.generation_time_seconds:.1f}s")
    click.echo(f"   Saved: {spec_path}")


@cli.command()
@click.argument('spec_file', default='.clawsmith/specs/latest.json')
@click.option('--codebase', default='.', help='Codebase directory to validate against')
def validate(spec_file, codebase):
    """Validate spec against codebase."""
    spec_path = Path(spec_file)
    if not spec_path.exists():
        # Try in .clawsmith/specs/
        spec_path = Path('.clawsmith/specs') / spec_file
        if not spec_path.exists():
            spec_path = spec_path.with_suffix('.json')
        if not spec_path.exists():
            click.echo(f"❌ Spec file not found: {spec_file}")
            return

    is_valid, message = validate_spec_against_code(str(spec_path), codebase)
    if is_valid:
        click.echo(f"✅ {message}")
    else:
        click.echo(f"❌ {message}")


@cli.command(name='list-specs')
@click.option('--repo', default='.', help='Repository path')
def list_specs(repo):
    """List all available specs."""
    root = Path(repo).resolve()
    specs_dir = root / ".clawsmith" / "specs"

    if not specs_dir.exists():
        click.echo("No specs found. Run `clawsmith generate <goal>` to create one.")
        return

    specs = []
    for f in sorted(specs_dir.glob("*.json"), reverse=True):
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

    if not specs:
        click.echo("No specs found.")
        return

    click.echo(f"Found {len(specs)} spec(s):\n")
    for s in specs:
        phases_info = f", {s['phase_count']} phases" if s['phase_count'] else ""
        click.echo(f"  [{s['id']}] {s['goal'][:60]}")
        click.echo(f"          tier={s['tier']} | {s['file_count']} files{phases_info}")


@cli.command(name='verify')
@click.argument('spec_id', required=False)
@click.option('--repo', default='.', help='Repository path')
@click.option('--fix', is_flag=True, help='Auto-fix issues by handing off to agent')
def verify(spec_id, repo, fix):
    """Verify a spec against the current working tree diff."""
    from orchestrator.verifier import SpecVerifier

    root = Path(repo).resolve()

    if not spec_id:
        # List specs
        specs_dir = root / ".clawsmith" / "specs"
        if not specs_dir.exists():
            click.echo("No specs found.")
            return
        click.echo("Available specs:")
        for f in sorted(specs_dir.glob("*.json"), reverse=True)[:10]:
            click.echo(f"  {f.stem}")
        click.echo("\nRun `clawsmith verify <spec-id>` to verify.")
        return

    # Load spec
    spec_file = root / ".clawsmith" / "specs" / f"{spec_id}.json"
    if not spec_file.exists():
        spec_file = root / ".clawsmith" / "specs" / f"{spec_id}.md"

    if not spec_file.exists():
        click.echo(f"❌ Spec not found: {spec_id}")
        return

    try:
        if spec_file.suffix == ".json":
            spec_data = json.loads(spec_file.read_text(encoding="utf-8"))
        else:
            from orchestrator.spec_generator import SpecGenerator
            text = spec_file.read_text(encoding="utf-8")
            data = SpecGenerator._extract_json(text)
            if data is None:
                click.echo(f"❌ Could not parse spec {spec_id}")
                return
            spec_data = data

        spec = GeneratedSpec.model_validate(spec_data)
    except Exception as e:
        click.echo(f"❌ Failed to load spec: {e}")
        return

    # Run verification
    verifier = SpecVerifier()

    async def run_verify():
        return await verifier.verify(spec, str(root))

    report = asyncio.run(run_verify())

    # Print report
    verdict = "✅ PASSED" if report.passed else "❌ FAILED"
    click.echo(f"\n{'='*50}")
    click.echo(f"Verification Report: {spec_id}")
    click.echo(f"{'='*50}")
    click.echo(f"Verdict: {verdict}")
    click.echo(f"Score: {report.score:.0%}")
    click.echo(f"Changed files: {len(report.changed_files)}")
    click.echo(f"Expected files: {len(report.expected_files)}")

    if report.comments:
        click.echo(f"\nFindings ({len(report.comments)}):")
        severity_order = ["CRITICAL", "MAJOR", "MINOR", "INFO"]
        emoji = {"CRITICAL": "🔴", "MAJOR": "🟠", "MINOR": "🟡", "INFO": "🔵"}
        by_severity = {}
        for c in report.comments:
            sev = c.severity.value
            if sev not in by_severity:
                by_severity[sev] = []
            by_severity[sev].append(c)

        for sev in severity_order:
            if sev not in by_severity:
                continue
            click.echo(f"\n{emoji[sev]} {sev} ({len(by_severity[sev])})")
            for c in by_severity[sev]:
                loc = f" ({c.file})" if c.file else ""
                click.echo(f"  - [{c.category}]{loc}: {c.message}")
                if c.suggestion:
                    click.echo(f"    💡 {c.suggestion}")

    if report.diff_summary:
        click.echo(f"\nDiff Summary:\n{report.diff_summary}")

    # Save report
    reports_dir = root / ".clawsmith" / "verifications"
    reports_dir.mkdir(parents=True, exist_ok=True)

    import time
    ts = time.strftime("%Y%m%d_%H%M%S")
    report_path = reports_dir / f"verify_{spec_id}_{ts}.md"
    report_path.write_text(report.to_markdown(), encoding="utf-8")
    click.echo(f"\n📁 Report saved: {report_path}")

    if fix and not report.passed:
        click.echo("\n🔧 Running auto-fix via agent...")
        # TODO: Implement auto-fix by handing off to YOLO engine


if __name__ == "__main__":
    cli()
