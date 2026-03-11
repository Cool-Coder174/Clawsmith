"""Memory writer — persists learned project context (stack, preferences, decisions) to disk.

When ClawSmith learns something about a project — detected frameworks,
user preferences, architectural decisions — the writer serializes it
into the memory directory so it's available in future sessions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.logging_setup import get_logger

from .models import (
    ArchitectureData,
    PreferencesData,
    RepoEntry,
    ToolingProfile,
)

log = get_logger("memory.writer")


class MemoryWriter:
    """Writes ClawSmith state to inspectable Markdown and JSON files."""

    def __init__(self, workspace_root: Path) -> None:
        self.root = workspace_root
        self.clawsmith_dir = workspace_root / "clawsmith"
        self.memory_dir = workspace_root / "memory"

    def ensure_dirs(self) -> None:
        self.clawsmith_dir.mkdir(parents=True, exist_ok=True)
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Architecture
    # ------------------------------------------------------------------

    def write_architecture(self, data: ArchitectureData) -> Path:
        self.ensure_dirs()
        path = self.clawsmith_dir / "architecture.md"
        lines: list[str] = [
            "# ClawSmith Architecture Profile",
            "",
            "## Hardware",
            f"- **Tier:** {data.hardware_tier}",
            f"- **OS:** {data.os_name} {data.os_version}".rstrip(),
            f"- **CPU:** {data.cpu_summary}",
            f"- **RAM:** {data.ram_gb} GB",
            f"- **GPU:** {data.gpu_summary} ({data.vram_gb} GB VRAM)"
            if data.gpu_summary
            else "- **GPU:** none",
            "",
        ]

        if data.installed_models:
            lines += [
                "## Installed Models",
                "",
                "| Model | Runtime | Path |",
                "|-------|---------|------|",
            ]
            for m in data.installed_models:
                lines.append(f"| {m.display_name} | {m.runtime} | {m.path} |")
            lines.append("")

        if data.installed_runtimes:
            lines += [
                "## Installed Runtimes",
                "",
                "| Name | Version | Path |",
                "|------|---------|------|",
            ]
            for r in data.installed_runtimes:
                lines.append(f"| {r.name} | {r.version} | {r.path} |")
            lines.append("")

        if data.approved_agent_clis:
            lines += ["## Agent CLIs", ""]
            for cli in data.approved_agent_clis:
                lines.append(f"- `{cli}`")
            lines.append("")

        if data.repos:
            lines += [
                "## Linked Repos",
                "",
                "| Name | Path | Role | Languages | In-Scope | Read-Only |",
                "|------|------|------|-----------|----------|-----------|",
            ]
            for repo in data.repos:
                langs = ", ".join(repo.languages) if repo.languages else ""
                lines.append(
                    f"| {repo.name} | {repo.path} | {repo.role} "
                    f"| {langs} | {repo.in_scope} | {repo.read_only} |"
                )
            lines.append("")

        if data.mutation_permissions:
            lines += [
                "## Mutation Permissions",
                "",
                "| Scope | Allowed | Requires Approval | Notes |",
                "|-------|---------|-------------------|-------|",
            ]
            for mp in data.mutation_permissions:
                lines.append(
                    f"| {mp.scope} | {mp.allowed} | {mp.requires_approval} | {mp.notes} |"
                )
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        log.info("Wrote architecture profile -> %s", path)
        return path

    # ------------------------------------------------------------------
    # Preferences
    # ------------------------------------------------------------------

    def write_preferences(self, data: PreferencesData) -> Path:
        self.ensure_dirs()
        path = self.clawsmith_dir / "preferences.md"

        lines: list[str] = [
            "# ClawSmith Preferences",
            "",
            "## Model Routing",
            f"- **Default routing:** {data.default_model_routing}",
            f"- **Default execution:** {data.default_task_execution}",
            "",
        ]

        if data.preferred_local_models:
            lines += ["## Preferred Local Models", ""]
            for m in data.preferred_local_models:
                lines.append(f"- {m}")
            lines.append("")

        if data.preferred_remote_models:
            lines += ["## Preferred Remote Models", ""]
            for m in data.preferred_remote_models:
                lines.append(f"- {m}")
            lines.append("")

        if data.preferred_shells:
            lines += ["## Preferred Shells", ""]
            for s in data.preferred_shells:
                lines.append(f"- `{s}`")
            lines.append("")

        if data.preferred_editors:
            lines += ["## Preferred Editors", ""]
            for e in data.preferred_editors:
                lines.append(f"- {e}")
            lines.append("")

        if data.coding_conventions:
            lines += [
                "## Coding Conventions",
                "",
                "| Language | Convention | Source |",
                "|----------|------------|--------|",
            ]
            for cc in data.coding_conventions:
                lines.append(f"| {cc.language} | {cc.convention} | {cc.source} |")
            lines.append("")

        if data.stack_notes:
            lines += ["## Stack Notes", ""]
            for sn in data.stack_notes:
                lines.append(f"- **{sn.key}:** {sn.value}")
            lines.append("")

        if data.build_commands:
            lines += ["## Build Commands", ""]
            for repo, cmds in data.build_commands.items():
                lines.append(f"### {repo}")
                for cmd in cmds:
                    lines.append(f"```\n{cmd}\n```")
            lines.append("")

        if data.test_commands:
            lines += ["## Test Commands", ""]
            for repo, cmds in data.test_commands.items():
                lines.append(f"### {repo}")
                for cmd in cmds:
                    lines.append(f"```\n{cmd}\n```")
            lines.append("")

        if data.last_known_working_setups:
            lines += ["## Last Known Working Setups", ""]
            for key, val in data.last_known_working_setups.items():
                lines.append(f"- **{key}:** {val}")
            lines.append("")

        path.write_text("\n".join(lines), encoding="utf-8")
        log.info("Wrote preferences -> %s", path)
        return path

    # ------------------------------------------------------------------
    # JSON files
    # ------------------------------------------------------------------

    def write_tooling_profile(self, data: ToolingProfile) -> Path:
        self.ensure_dirs()
        path = self.clawsmith_dir / "tooling-profile.json"
        path.write_text(
            json.dumps(data.model_dump(), indent=2),
            encoding="utf-8",
        )
        log.info("Wrote tooling profile -> %s", path)
        return path

    def write_repo_graph(
        self,
        repos: list[RepoEntry],
        edges: list[dict],
    ) -> Path:
        self.ensure_dirs()
        path = self.clawsmith_dir / "repo-graph.json"
        payload = {
            "repos": [r.model_dump() for r in repos],
            "edges": edges,
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        log.info("Wrote repo graph -> %s", path)
        return path

    def write_scope_rules(self, rules: dict) -> Path:
        self.ensure_dirs()
        path = self.clawsmith_dir / "scope-rules.json"
        path.write_text(json.dumps(rules, indent=2), encoding="utf-8")
        log.info("Wrote scope rules -> %s", path)
        return path

    # ------------------------------------------------------------------
    # Memory entries (daily journal)
    # ------------------------------------------------------------------

    def write_memory_entry(
        self,
        content: str,
        tags: list[str] | None = None,
    ) -> Path:
        self.ensure_dirs()
        today = datetime.now(tz=UTC).strftime("%Y-%m-%d")
        path = self.memory_dir / f"{today}.md"

        ts = datetime.now(tz=UTC).strftime("%H:%M:%S UTC")
        tag_str = ""
        if tags:
            tag_str = " " + " ".join(f"`#{t}`" for t in tags)

        entry = f"\n## {ts}{tag_str}\n\n{content}\n"

        if path.exists():
            with path.open("a", encoding="utf-8") as fh:
                fh.write(entry)
        else:
            header = f"# Memory — {today}\n"
            path.write_text(header + entry, encoding="utf-8")

        log.info("Wrote memory entry -> %s", path)
        return path

    # ------------------------------------------------------------------
    # Top-level MEMORY.md
    # ------------------------------------------------------------------

    def write_memory_md(self, summary: str) -> Path:
        path = self.root / "MEMORY.md"
        ts = datetime.now(tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
        content = (
            f"# MEMORY\n\n"
            f"> Auto-generated by ClawSmith — last updated {ts}\n\n"
            f"{summary}\n"
        )
        path.write_text(content, encoding="utf-8")
        log.info("Wrote MEMORY.md -> %s", path)
        return path
