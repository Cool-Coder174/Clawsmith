"""Preflight environment checker for ClawSmith."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

if TYPE_CHECKING:
    from config.config_loader import ClawsmithConfig

_REPO_ROOT = Path(__file__).parent.parent


class DoctorChecker:
    """Runs a suite of preflight checks and reports results via ``rich``."""

    _PASS = "[bold green]PASS[/bold green]"
    _WARN = "[bold yellow]WARN[/bold yellow]"
    _FAIL = "[bold red]FAIL[/bold red]"

    def __init__(self) -> None:
        self._console = Console()
        self._passes = 0
        self._warnings = 0
        self._failures = 0
        self._cfg: ClawsmithConfig | None = None
        self._table = Table(title="ClawSmith Doctor", show_lines=True)
        self._table.add_column("Check", style="cyan", min_width=30)
        self._table.add_column("Status", min_width=10)
        self._table.add_column("Detail")

    def run(self) -> bool:
        """Execute all checks and print the report. Returns ``True`` if no failures."""
        self._check_python_version()
        self._check_pip()
        self._check_git()
        self._check_cursor_cli()
        self._check_agent_clis()
        self._check_env_file()
        self._check_env_keys()
        self._check_settings_yaml()
        self._check_config_parses()
        self._check_model_tiers()
        self._check_openclaw()
        self._check_dir("logs/", _REPO_ROOT / "logs", fail=False)
        self._check_dir("artifacts/", _REPO_ROOT / "artifacts", fail=False)
        self._check_dir("jobs/generated/", _REPO_ROOT / "jobs" / "generated", fail=False)
        self._check_dir("jobs/templates/", _REPO_ROOT / "jobs" / "templates", fail=True)
        self._check_bat_templates()
        self._check_agent_profiles()

        self._console.print(self._table)
        self._console.print(
            f"\n{self._passes} checks passed, {self._warnings} warnings, "
            f"{self._failures} failures"
        )

        if self._failures:
            self._console.print(
                "\n[bold red]Doctor found critical issues. "
                "Fix failures before running ClawSmith.[/bold red]"
            )
        elif self._warnings:
            self._console.print(
                "\n[bold yellow]Doctor found warnings. "
                "ClawSmith may work but review the above.[/bold yellow]"
            )
        else:
            self._console.print(
                "\n[bold green]All checks passed. ClawSmith is ready.[/bold green]"
            )

        return self._failures == 0

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _pass(self, name: str, detail: str = "") -> None:
        self._table.add_row(name, self._PASS, detail)
        self._passes += 1

    def _warn(self, name: str, detail: str) -> None:
        self._table.add_row(name, self._WARN, detail)
        self._warnings += 1

    def _fail(self, name: str, detail: str) -> None:
        self._table.add_row(name, self._FAIL, detail)
        self._failures += 1

    def _check_python_version(self) -> None:
        name = "Python version"
        v = sys.version_info
        detail = f"{v.major}.{v.minor}.{v.micro}"
        if v >= (3, 11):
            self._pass(name, detail)
        else:
            self._fail(name, f"{detail} — Python 3.11+ required")

    def _check_pip(self) -> None:
        name = "pip on PATH"
        if shutil.which("pip"):
            self._pass(name)
        else:
            self._warn(name, "pip not found on PATH")

    def _check_git(self) -> None:
        name = "git on PATH"
        if shutil.which("git"):
            self._pass(name)
        else:
            self._warn(name, "git not found on PATH")

    def _check_cursor_cli(self) -> None:
        name = "Cursor CLI (legacy check)"
        cli_path = os.environ.get("CURSOR_CLI_PATH")
        if cli_path and Path(cli_path).exists():
            self._pass(name, cli_path)
        elif shutil.which("cursor"):
            self._pass(name, "found via PATH")
        else:
            self._warn(name, "Set CURSOR_CLI_PATH or add cursor to PATH")

    def _check_agent_clis(self) -> None:
        """Detect all registered agent CLIs and report availability."""
        try:
            from agents.registry import get_agent_registry

            registry = get_agent_registry(auto_detect=True)
            available = registry.available_agents()
            all_agents = registry.all_agents()

            if available:
                self._pass(
                    "Agent CLIs detected",
                    f"{len(available)}/{len(all_agents)}: {', '.join(available)}",
                )
            else:
                self._warn(
                    "Agent CLIs detected",
                    "No agent CLIs found. Install at least one (cursor, claude, gemini).",
                )
        except Exception as exc:
            self._warn("Agent CLIs detected", f"Detection failed: {exc}")

    def _check_env_file(self) -> None:
        name = ".env file exists"
        if (_REPO_ROOT / ".env").exists():
            self._pass(name)
        else:
            self._warn(name, "Copy .env.example to .env and fill in API keys")

    def _check_env_keys(self) -> None:
        name = "Required .env keys"
        from dotenv import dotenv_values

        vals = dotenv_values(_REPO_ROOT / ".env")
        has_key = any(
            vals.get(k)
            for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY")
        )
        if has_key:
            self._pass(name)
        else:
            self._warn(
                name,
                "No API key set (OPENAI_API_KEY / ANTHROPIC_API_KEY / OPENROUTER_API_KEY)",
            )

    def _check_settings_yaml(self) -> None:
        name = "config/settings.yaml exists"
        if (_REPO_ROOT / "config" / "settings.yaml").exists():
            self._pass(name)
        else:
            self._fail(name, "config/settings.yaml not found")

    def _check_config_parses(self) -> None:
        name = "Config parses"
        try:
            from config.config_loader import load_config

            self._cfg = load_config()
            self._pass(name)
        except Exception as exc:
            self._cfg = None
            self._fail(name, str(exc))

    def _check_model_tiers(self) -> None:
        name = "All 4 model tiers defined"
        if self._cfg is None:
            self._warn(name, "Skipped — config failed to parse")
            return

        missing: list[str] = []
        for tier_name in ("local_router", "local_code", "premium", "prompt_polisher"):
            tier = getattr(self._cfg.models, tier_name, None)
            if tier is None or not tier.model_name:
                missing.append(tier_name)

        if not missing:
            self._pass(name)
        else:
            for t in missing:
                self._warn(name, f"models.{t} has empty model_name")

    def _check_openclaw(self) -> None:
        name = "OpenClaw config present"
        if self._cfg is None:
            self._warn(name, "Skipped — config failed to parse")
            return
        if self._cfg.openclaw is not None:
            self._pass(name)
        else:
            self._warn(name, "openclaw section is missing from config")

    def _check_dir(self, label: str, path: Path, *, fail: bool) -> None:
        name = f"{label} dir exists"
        if path.exists():
            self._pass(name)
        elif fail:
            self._fail(name, f"{path} not found")
        else:
            self._warn(name, f"mkdir {path}")

    def _check_bat_templates(self) -> None:
        templates_dir = _REPO_ROOT / "jobs" / "templates"
        required = (
            "build_and_test.bat.template",
            "cursor_task.bat.template",
            "agent_audit.bat.template",
            "agent_bugfix.bat.template",
            "agent_implement.bat.template",
        )
        for tpl in required:
            name = f"Template: {tpl}"
            if (templates_dir / tpl).exists():
                self._pass(name)
            else:
                self._warn(name, f"Missing from {templates_dir}")

    def _check_agent_profiles(self) -> None:
        name = "Agent profiles dir"
        profiles_dir = _REPO_ROOT / "config" / "agent_profiles"
        templates_dir = _REPO_ROOT / "jobs" / "templates"
        if not profiles_dir.exists():
            self._warn(name, "config/agent_profiles/ does not exist")
            return
        yamls = list(profiles_dir.glob("*.yaml"))
        if yamls:
            self._pass(name, f"{len(yamls)} profile(s) found")
        else:
            self._warn(name, "config/agent_profiles/ has no .yaml files")

        import yaml

        for profile_path in yamls:
            check_name = f"Profile template: {profile_path.name}"
            try:
                data = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
                tpl = data.get("prompt_template", "")
                if not tpl:
                    self._warn(check_name, "prompt_template is not set")
                elif (templates_dir / tpl).exists():
                    self._pass(check_name, tpl)
                else:
                    self._fail(
                        check_name,
                        f"prompt_template '{tpl}' not found in {templates_dir}",
                    )
            except Exception as exc:
                self._fail(check_name, f"Failed to parse: {exc}")


def run_doctor() -> bool:
    """Convenience entry point for CLI use."""
    return DoctorChecker().run()
