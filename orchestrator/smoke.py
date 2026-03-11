"""Smoke-test: minimal end-to-end verification that ClawSmith is alive."""

from __future__ import annotations

import os
import time
from pathlib import Path

from rich.console import Console

_REPO_ROOT = Path(__file__).parent.parent

_PASS = "[bold green]PASS[/bold green]"
_FAIL = "[bold red]FAIL[/bold red]"


class SmokeTest:
    """Runs lightweight integration checks across subsystems."""

    def __init__(self) -> None:
        self.console = Console()
        self._passes = 0
        self._failures = 0

    def run(self) -> bool:
        self.console.print("\n[bold]ClawSmith Smoke Test[/bold]\n")
        start = time.time()

        self._check_config_loads()
        self._check_routing()
        self._check_provider_path()
        self._check_agent_available()
        self._check_repo_auditor()
        self._check_mcp_importable()
        self._check_dry_run_pipeline()

        elapsed = time.time() - start
        self.console.print(
            f"\n{self._passes} passed, {self._failures} failed "
            f"({elapsed:.1f}s)"
        )

        if self._failures:
            self.console.print(
                "\n[bold red]Smoke test found issues. "
                "Run 'clawsmith doctor' for details.[/bold red]"
            )
        else:
            self.console.print(
                "\n[bold green]All smoke checks passed. "
                "ClawSmith is operational.[/bold green]"
            )
        return self._failures == 0

    # -- helpers ----------------------------------------------------------

    def _ok(self, name: str, detail: str = "") -> None:
        msg = f"  {_PASS} {name}"
        if detail:
            msg += f"  [dim]({detail})[/dim]"
        self.console.print(msg)
        self._passes += 1

    def _fail(self, name: str, detail: str) -> None:
        self.console.print(f"  {_FAIL} {name}  [dim]({detail})[/dim]")
        self._failures += 1

    # -- checks -----------------------------------------------------------

    def _check_config_loads(self) -> None:
        try:
            from config.config_loader import load_config

            cfg = load_config()
            self._ok(
                "Config loads",
                f"premium={cfg.models.premium.model_name}",
            )
        except Exception as exc:
            self._fail("Config loads", str(exc))

    def _check_routing(self) -> None:
        try:
            from orchestrator.schemas import TaskClassification, TaskType
            from routing.router import ModelRouter

            classification = TaskClassification(
                task_type=TaskType.bugfix,
                complexity_score=0.5,
                files_likely_touched=2,
                ambiguity_score=0.3,
                architectural_impact=0.2,
                failure_severity=0.4,
                estimated_tokens=500,
            )
            decision = ModelRouter().route_task(classification)
            self._ok(
                "Routing engine",
                f"tier={decision.selected_tier.value}",
            )
        except Exception as exc:
            self._fail("Routing engine", str(exc))

    def _check_provider_path(self) -> None:
        try:
            from config.config_loader import load_config

            cfg = load_config()
            local_ok = cfg.models.local_code.model_name.startswith("ollama")
            cloud_ok = any([
                os.environ.get("OPENAI_API_KEY"),
                os.environ.get("ANTHROPIC_API_KEY"),
                os.environ.get("OPENROUTER_API_KEY"),
            ])

            paths: list[str] = []
            if local_ok:
                paths.append("local/ollama")
            if cloud_ok:
                paths.append("cloud")

            if paths:
                self._ok("Provider path", ", ".join(paths))
            else:
                self._fail(
                    "Provider path",
                    "No local or cloud provider configured",
                )
        except Exception as exc:
            self._fail("Provider path", str(exc))

    def _check_agent_available(self) -> None:
        try:
            from agents.registry import get_agent_registry

            registry = get_agent_registry(auto_detect=True)
            available = registry.available_agents()
            if available:
                self._ok("Agent CLI", ", ".join(available))
            else:
                self._fail("Agent CLI", "No agent CLIs detected")
        except Exception as exc:
            self._fail("Agent CLI", str(exc))

    def _check_repo_auditor(self) -> None:
        try:
            from tools.repo_auditor import RepoAuditor

            report = RepoAuditor(_REPO_ROOT).audit()
            langs = ", ".join(list(report.languages)[:3])
            self._ok("Repo auditor", f"languages={langs}")
        except Exception as exc:
            self._fail("Repo auditor", str(exc))

    def _check_mcp_importable(self) -> None:
        try:
            import mcp_server.server  # noqa: F401

            self._ok("MCP server", "importable")
        except Exception as exc:
            self._fail("MCP server", str(exc))

    def _check_dry_run_pipeline(self) -> None:
        try:
            import asyncio

            from orchestrator.pipeline import OrchestrationPipeline

            result = asyncio.run(
                OrchestrationPipeline().run(
                    "Test task: verify pipeline runs",
                    str(_REPO_ROOT),
                    dry_run=True,
                )
            )
            if result.success:
                dur = f"{result.duration_seconds:.1f}s"
                self._ok("Dry-run pipeline", dur)
            else:
                self._fail("Dry-run pipeline", result.error_message or "unknown")
        except Exception as exc:
            self._fail("Dry-run pipeline", str(exc))


def run_smoke_test() -> bool:
    """Convenience entry point for CLI use."""
    return SmokeTest().run()
