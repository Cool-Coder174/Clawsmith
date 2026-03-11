"""First-run onboarding for ClawSmith."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel

_REPO_ROOT = Path(__file__).parent.parent

_PASS = "[bold green]PASS[/bold green]"
_WARN = "[bold yellow]WARN[/bold yellow]"
_FAIL = "[bold red]FAIL[/bold red]"
_OK = "[bold green] OK [/bold green]"


class Onboarder:
    """Guides the user through first-run setup."""

    def __init__(self) -> None:
        self.console = Console()
        self._issues: list[str] = []
        self._has_ollama = False

    def run(self) -> bool:
        """Execute the full onboarding flow. Returns True on success."""
        self._header()
        self._check_prerequisites()
        mode = self._choose_mode()
        self._setup_env(mode)
        self._create_directories()
        self._verify_config()
        self._offer_model_pull()
        self._next_steps()
        return not self._issues

    # -- UI helpers -------------------------------------------------------

    def _ok(self, msg: str) -> None:
        self.console.print(f"  {_PASS} {msg}")

    def _warn(self, msg: str) -> None:
        self.console.print(f"  {_WARN} {msg}")
        self._issues.append(msg)

    def _fail(self, msg: str) -> None:
        self.console.print(f"  {_FAIL} {msg}")
        self._issues.append(msg)

    def _done(self, msg: str) -> None:
        self.console.print(f"  {_OK} {msg}")

    # -- steps ------------------------------------------------------------

    def _header(self) -> None:
        self.console.print()
        self.console.print(
            Panel(
                "[bold cyan]ClawSmith Onboarding[/bold cyan]\n"
                "Setting up your local-first AI orchestration environment.",
                expand=False,
            )
        )
        self.console.print()

    def _check_prerequisites(self) -> None:
        self.console.print("[bold]Checking prerequisites...[/bold]\n")

        v = sys.version_info
        ver = f"{v.major}.{v.minor}.{v.micro}"
        if v >= (3, 11):
            self._ok(f"Python {ver}")
        else:
            self._fail(f"Python {ver} — 3.11+ required")

        if shutil.which("git"):
            self._ok("git found")
        else:
            self._warn("git not found (needed for repo operations)")

        if shutil.which("pip") or shutil.which("pip3"):
            self._ok("pip found")
        else:
            self._warn("pip not found on PATH")

        if shutil.which("ollama"):
            self._ok("Ollama found (local inference ready)")
            self._has_ollama = True
        else:
            self._warn(
                "Ollama not found (install from https://ollama.com "
                "for local models)"
            )
            self._has_ollama = False

        self._detect_agents()
        self.console.print()

    def _detect_agents(self) -> None:
        try:
            from agents.registry import get_agent_registry

            registry = get_agent_registry(auto_detect=True)
            available = registry.available_agents()
            if available:
                self._ok(f"Agent CLIs: {', '.join(available)}")
            else:
                self._warn(
                    "No agent CLIs found "
                    "(install cursor, claude, or gemini)"
                )
        except Exception:
            self._warn("Agent CLI detection failed")

    def _choose_mode(self) -> str:
        self.console.print("[bold]Setup mode:[/bold]")
        self.console.print(
            "  [1] Local only  — uses Ollama for all inference"
        )
        self.console.print(
            "  [2] Hybrid      — local for simple, cloud for complex "
            "[dim](recommended)[/dim]"
        )
        self.console.print(
            "  [3] Cloud only  — requires API keys"
        )
        self.console.print()

        choice = click.prompt(
            "Choose",
            type=click.Choice(["1", "2", "3"]),
            default="2",
        )

        modes = {"1": "local", "2": "hybrid", "3": "cloud"}
        mode = modes[choice]
        self.console.print(f"\n  Selected: [cyan]{mode}[/cyan]\n")
        return mode

    def _setup_env(self, mode: str) -> None:
        self.console.print("[bold]Setting up environment...[/bold]\n")

        env_path = _REPO_ROOT / ".env"
        example_path = _REPO_ROOT / ".env.example"

        if env_path.exists():
            self._done(".env already exists")
        elif example_path.exists():
            shutil.copy2(example_path, env_path)
            self._done("Created .env from .env.example")
        else:
            env_path.write_text(
                "# ClawSmith environment\n"
                "OPENAI_API_KEY=\n"
                "ANTHROPIC_API_KEY=\n"
                "OPENROUTER_API_KEY=\n"
                "CURSOR_CLI_PATH=\n"
                "LOG_LEVEL=INFO\n",
                encoding="utf-8",
            )
            self._done("Created .env (template)")

        if mode in ("hybrid", "cloud"):
            self.console.print(
                "  [dim]Tip: edit .env and add at least one API key "
                "(OPENAI_API_KEY, ANTHROPIC_API_KEY, "
                "or OPENROUTER_API_KEY)[/dim]"
            )

    def _create_directories(self) -> None:
        dirs = [
            _REPO_ROOT / "logs",
            _REPO_ROOT / "artifacts",
            _REPO_ROOT / "jobs" / "generated",
        ]
        for d in dirs:
            rel = d.relative_to(_REPO_ROOT)
            if d.exists():
                self._done(f"{rel}/ exists")
            else:
                d.mkdir(parents=True, exist_ok=True)
                self._done(f"Created {rel}/")

    def _verify_config(self) -> None:
        cfg_path = _REPO_ROOT / "config" / "settings.yaml"
        if cfg_path.exists():
            try:
                from config.config_loader import load_config

                load_config(cfg_path)
                self._done("config/settings.yaml verified")
            except Exception as exc:
                self._warn(f"Config issue: {exc}")
        else:
            self._warn("config/settings.yaml not found")

    def _offer_model_pull(self) -> None:
        if not self._has_ollama:
            return

        self.console.print()
        if not click.confirm(
            "Pull default local models via Ollama? "
            "(requires 'ollama serve' running)",
            default=False,
        ):
            return

        for model in ("mistral", "codellama"):
            self.console.print(f"  Pulling {model}...")
            try:
                subprocess.run(
                    ["ollama", "pull", model],
                    check=True,
                    timeout=600,
                )
                self._ok(f"Pulled {model}")
            except subprocess.TimeoutExpired:
                self._warn(f"Timeout pulling {model}")
            except Exception as exc:
                self._warn(f"Failed to pull {model}: {exc}")

    def _next_steps(self) -> None:
        self.console.print()
        self.console.print(
            Panel(
                "[bold green]Onboarding complete![/bold green]\n\n"
                "Next steps:\n"
                "  [cyan]clawsmith doctor[/cyan]"
                "      — verify full environment\n"
                "  [cyan]clawsmith smoke-test[/cyan]"
                "  — quick integration check\n"
                "  [cyan]clawsmith start[/cyan]"
                "       — start the MCP server\n"
                "  [cyan]clawsmith chat[/cyan]"
                "        — interactive session",
                title="ClawSmith",
                expand=False,
            )
        )


def run_onboard() -> bool:
    """Convenience entry point for CLI use."""
    return Onboarder().run()
