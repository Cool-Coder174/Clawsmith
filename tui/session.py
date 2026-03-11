"""Main interactive chat session for the ClawSmith TUI."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn

from tui.commands import CommandRouter
from tui.models import ChatMessage, MessageRole, ThoughtPhase
from tui.renderer import Renderer
from tui.theme import CLAWSMITH_THEME, SYM_ARROW, SYM_CHECK, SYM_CROSS
from tui.thinking import ThoughtStream

_REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Subsystem keyword routing
# ---------------------------------------------------------------------------

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "detect": [
        "hardware", "detect", "system info", "machine",
        "specs", "cpu", "gpu", "ram", "what's my",
    ],
    "recommend": [
        "recommend", "suggest", "which model", "best model",
        "llm", "local model",
    ],
    "install": [
        "install model", "download model", "provision",
        "setup model",
    ],
    "audit": [
        "audit", "analyze repo", "review code", "check repo",
    ],
    "memory": [
        "memory", "sync memory", "show memory", "preferences",
    ],
    "scope": [
        "scope", "contract", "what can i access",
        "permissions",
    ],
    "link": [
        "link repo", "add repo", "workspace graph",
    ],
}

# ---------------------------------------------------------------------------
# Heuristics for smart intent classification
# ---------------------------------------------------------------------------

_GREETING_WORDS = frozenset({
    "hello", "hi", "hey", "howdy", "yo", "sup", "greetings",
    "hola", "hiya", "heya",
})

_THANKS_WORDS = frozenset({
    "thanks", "thank", "thx", "cheers", "ty",
})

_CAPABILITY_PHRASES = (
    "what can you", "what do you do", "how can you help",
    "what are you", "who are you", "how do you work",
    "what are your", "tell me about yourself",
    "where do i start", "getting started",
    "how does this work", "how does clawsmith",
    "what is clawsmith", "what's clawsmith",
)

_TASK_VERBS = frozenset({
    "fix", "add", "create", "implement", "refactor", "build",
    "test", "run", "update", "remove", "delete", "write", "change",
    "modify", "move", "rename", "deploy", "debug", "optimize",
    "migrate", "configure", "setup", "generate", "scaffold",
    "lint", "format", "document", "integrate",
    "convert", "upgrade", "downgrade", "patch", "revert",
    "rewrite", "restructure", "analyze", "profile", "benchmark",
    "check", "verify", "validate", "inspect", "scan",
})

_CODE_CONTEXT = frozenset({
    "function", "class", "method", "module", "component",
    "endpoint", "api", "database", "schema", "migration",
    "bug", "error", "exception", "crash", "file", "folder",
    "directory", "package", "library", "dependency",
    "dockerfile", "pipeline", "workflow", "server", "client",
    "route", "handler", "middleware", "controller", "service",
    "view", "template", "config", "spec", "test", "tests",
    "repo", "repository", "codebase", "code",
    "import", "export", "variable", "constant",
    "performance", "latency",
})

_FILE_EXTENSIONS = frozenset({
    ".py", ".js", ".ts", ".jsx", ".tsx", ".rs", ".go",
    ".java", ".cpp", ".c", ".h", ".css", ".html", ".scss",
    ".yaml", ".yml", ".json", ".toml", ".xml", ".sql",
    ".sh", ".bat", ".ps1", ".cfg", ".ini",
})


def _is_conversational(q: str) -> bool:
    """Return True if *q* looks like a greeting, thanks, or meta-question."""
    words = q.split()
    if not words:
        return True
    first = words[0].rstrip("!.,?;:")
    if first in _GREETING_WORDS:
        return True
    if first in _THANKS_WORDS or any(
        w.rstrip("!.,?;:") in _THANKS_WORDS for w in words
    ):
        return True
    return any(p in q for p in _CAPABILITY_PHRASES)


def _looks_like_task(q: str) -> bool:
    """Return True if *q* contains indicators of a coding task."""
    words = {w.rstrip("!.,?;:").lower() for w in q.split()}
    if words & _TASK_VERBS:
        return True
    if words & _CODE_CONTEXT:
        return True
    tokens = q.lower().split()
    if any(
        any(tok.endswith(ext) for ext in _FILE_EXTENSIONS)
        for tok in tokens
    ):
        return True
    return any("/" in tok or "\\" in tok for tok in tokens)


def _extract_missing_model(error_str: str) -> str | None:
    """Extract the model name from an Ollama 'model not found' error."""
    for pattern in (
        r"""model\s+['"]([^'"]+)['"]\s+not found""",
        r"""model\s+(\S+)\s+not found""",
    ):
        m = re.search(pattern, error_str, re.IGNORECASE)
        if m:
            return m.group(1)
    return None


def _detect_intent(query: str) -> str:
    """Classify the query as a subsystem intent, conversation, or task."""
    q = query.lower().strip()

    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return intent

    if _is_conversational(q):
        return "conversation"

    if _looks_like_task(q):
        return "task"

    # Short ambiguous queries default to conversation rather than
    # burning 60s on a full pipeline run.
    if len(q.split()) <= 6:
        return "conversation"

    return "task"


class ChatSession:
    """Interactive REPL that connects the user to ClawSmith subsystems."""

    def __init__(self, repo_path: str = ".") -> None:
        self.console = Console(theme=CLAWSMITH_THEME)
        self.renderer = Renderer(self.console)
        self.commands = CommandRouter()
        self.history: list[ChatMessage] = []
        self.repo_path = Path(repo_path).resolve()
        self._running = True
        self._brain: object | None = None  # set after preflight

    # -- main loop --------------------------------------------------------

    def run(self) -> None:
        """Start the interactive session."""
        self.renderer.logo()
        self.renderer.welcome()
        self._run_preflight()

        while self._running:
            try:
                raw = self.renderer.prompt()
            except (KeyboardInterrupt, EOFError):
                self.stop()
                break

            text = raw.strip()
            if not text:
                continue

            if text.startswith("/"):
                self.commands.dispatch(text, self)
                continue

            self.history.append(
                ChatMessage(role=MessageRole.user, content=text)
            )
            self.renderer.user_message(text)

            response, thoughts = self._execute(text)

            self.history.append(
                ChatMessage(
                    role=MessageRole.agent,
                    content=response,
                    thoughts=thoughts,
                )
            )
            self.renderer.agent_message(response)
            self.renderer.separator()

        self.renderer.farewell()

    def stop(self) -> None:
        self._running = False

    # -- preflight --------------------------------------------------------

    def _run_preflight(self) -> None:
        """Check dependencies at startup and guide the user through repairs."""
        import threading
        from orchestrator.preflight import (
            PREFLIGHT_STEP_COUNT, PreflightResult, run_preflight,
            try_start_ollama,
        )

        result_box: list[PreflightResult | None] = [None]

        progress = Progress(
            "  ",
            SpinnerColumn(style="cyan"),
            TextColumn("[bold]{task.description}[/bold]"),
            BarColumn(
                bar_width=30,
                complete_style="green",
                finished_style="green",
            ),
            TextColumn("[dim]{task.completed}/{task.total}[/dim]"),
            console=self.console,
            transient=True,
        )

        with progress:
            task_id = progress.add_task(
                "Checking dependencies...", total=PREFLIGHT_STEP_COUNT,
            )

            def _on_step(completed: int, description: str) -> None:
                progress.update(
                    task_id, completed=completed,
                    description=description,
                )

            def _check() -> None:
                try:
                    result_box[0] = run_preflight(on_step=_on_step)
                except Exception:
                    pass

            check_thread = threading.Thread(target=_check, daemon=True)
            check_thread.start()
            check_thread.join(timeout=10)

        if check_thread.is_alive() or result_box[0] is None:
            self.console.print(
                f"  [yellow]![/yellow] Dependency check timed out"
            )
            self.console.print(
                f"    [dim]{SYM_ARROW} Continuing without full "
                f"preflight — try /doctor later[/dim]"
            )
            self.renderer.separator()
            return

        result = result_box[0]

        if result.config_ok:
            self.console.print(
                f"  [green]{SYM_CHECK}[/green] Config loaded"
            )
        if result.ollama_installed and result.ollama_reachable:
            self.console.print(
                f"  [green]{SYM_CHECK}[/green] Ollama running"
            )
        if result.has_api_keys:
            self.console.print(
                f"  [green]{SYM_CHECK}[/green] API keys configured"
            )
        if result.mcp_running:
            self.console.print(
                f"  [green]{SYM_CHECK}[/green] MCP server running"
            )

        for issue in result.issues:
            if issue.severity == "error":
                self.console.print(
                    f"  [bold red]{SYM_CROSS}[/bold red] {issue.message}"
                )
            else:
                self.console.print(
                    f"  [yellow]![/yellow] {issue.message}"
                )
            if issue.repair_hint:
                for line in issue.repair_hint.splitlines():
                    self.console.print(
                        f"    [dim]{SYM_ARROW} {line}[/dim]"
                    )

        if result.ollama_installed and not result.ollama_reachable:
            self.console.print()
            try:
                answer = self.console.input(
                    "  Start Ollama now? [bold]\\[Y/n][/bold] "
                ).strip().lower()
            except (KeyboardInterrupt, EOFError):
                answer = "n"

            if answer in ("", "y", "yes"):
                self.console.print(
                    "  Starting Ollama...", style="muted"
                )
                if try_start_ollama():
                    self.console.print(
                        f"  [green]{SYM_CHECK}[/green] "
                        "Ollama started (localhost:11434)"
                    )
                    result.ollama_reachable = True
                    if "local_router" not in result.available_tiers:
                        result.available_tiers.extend(
                            ["local_router", "local_code"]
                        )
                    result.issues = [
                        i for i in result.issues
                        if i.component != "Ollama"
                    ]
                else:
                    self.console.print(
                        f"  [red]{SYM_CROSS}[/red] Could not start "
                        "Ollama. Try running "
                        "[bold]ollama serve[/bold] manually."
                    )

        if result.ollama_reachable and result.models_missing:
            missing_str = ", ".join(result.models_missing)
            self.console.print(
                f"  [yellow]![/yellow] Required local models not "
                f"installed: [bold]{missing_str}[/bold]"
            )
            try:
                answer = self.console.input(
                    "  Pull missing models now? [bold]\\[Y/n][/bold] "
                ).strip().lower()
            except (KeyboardInterrupt, EOFError):
                answer = "n"

            if answer in ("", "y", "yes"):
                from orchestrator.preflight import pull_ollama_model

                pulled: list[str] = []
                for model in result.models_missing:
                    self.console.print(
                        f"  Pulling [cyan]{model}[/cyan]…",
                        style="muted",
                    )
                    if pull_ollama_model(model):
                        self.console.print(
                            f"  [green]{SYM_CHECK}[/green] "
                            f"Pulled {model}"
                        )
                        pulled.append(model)
                    else:
                        self.console.print(
                            f"  [red]{SYM_CROSS}[/red] "
                            f"Failed to pull {model}. "
                            f"Try: [bold]ollama pull {model}[/bold]"
                        )
                result.models_missing = [
                    m for m in result.models_missing
                    if m not in pulled
                ]

        self.console.print()

        if result.available_tiers:
            tiers = ", ".join(result.available_tiers)
            self.console.print(
                f"  [green]{SYM_CHECK}[/green] Ready — "
                f"available tiers: [cyan]{tiers}[/cyan]"
            )
        elif not result.can_run_tasks:
            self.console.print(
                f"  [bold yellow]Warning:[/bold yellow] "
                "No providers available. Slash commands still work "
                "— try [bold]/help[/bold]"
            )

        if result.ollama_reachable:
            try:
                from tui.llm_chat import ChatBrain

                self._brain = ChatBrain(self.repo_path)
                self.console.print(
                    f"  [green]{SYM_CHECK}[/green] "
                    "LLM chat ready (local model)"
                )
            except Exception:
                pass

        self.renderer.separator()

    # -- routing ----------------------------------------------------------

    def _execute(self, query: str) -> tuple[str, list]:
        """Route *query* to the right subsystem and return (response, thoughts)."""
        if self._brain is not None:
            return self._execute_via_brain(query)

        intent = _detect_intent(query)
        handler = _INTENT_HANDLERS.get(intent, _handle_task)
        return handler(self, query)

    def _execute_via_brain(self, query: str) -> tuple[str, list]:
        """Send *query* to the LLM chat brain with a live thinking indicator."""
        from tui.llm_chat import ChatBrain

        brain: ChatBrain = self._brain  # type: ignore[assignment]
        events: list = []

        with ThoughtStream(self.renderer.console) as ts:
            ts.emit(ThoughtPhase.analyzing, "Thinking...")
            try:
                response = asyncio.run(brain.respond(query))
            except Exception as exc:
                error_str = str(exc)
                model = _extract_missing_model(error_str)
                if model:
                    ts.emit(
                        ThoughtPhase.error,
                        f"Model '{model}' not installed",
                    )
                    return self._offer_model_pull_and_retry(
                        query, model, ts.events,
                    )
                ts.emit(ThoughtPhase.error, error_str)
                return (
                    f"LLM error: {exc}\n\n"
                    "Falling back to built-in handler.",
                    ts.events,
                )
            ts.emit(ThoughtPhase.complete, "Done")
            events = ts.events

        return response, events

    def _offer_model_pull_and_retry(
        self,
        query: str,
        model: str,
        events: list,
    ) -> tuple[str, list]:
        """Prompt the user to pull a missing Ollama model, then retry."""
        self.console.print()
        self.console.print(
            f"  [yellow]![/yellow] Ollama model "
            f"[bold]{model}[/bold] is not installed."
        )
        try:
            answer = self.console.input(
                f"  Pull [bold]{model}[/bold] now? "
                "[bold]\\[Y/n][/bold] "
            ).strip().lower()
        except (KeyboardInterrupt, EOFError):
            answer = "n"

        if answer not in ("", "y", "yes"):
            return (
                f"Model '{model}' is not installed. "
                f"Install it with: **ollama pull {model}**\n\n"
                "Falling back to built-in handler.",
                events,
            )

        from orchestrator.preflight import pull_ollama_model

        self.console.print(
            f"  Pulling [cyan]{model}[/cyan]… "
            "(this may take a few minutes)",
            style="muted",
        )
        if pull_ollama_model(model):
            self.console.print(
                f"  [green]{SYM_CHECK}[/green] "
                f"Pulled {model} — retrying…"
            )
            self.renderer.separator()
            return self._execute_via_brain(query)

        self.console.print(
            f"  [red]{SYM_CROSS}[/red] Failed to pull {model}. "
            f"Try manually: [bold]ollama pull {model}[/bold]"
        )
        return (
            f"Could not install model '{model}'.\n\n"
            "Falling back to built-in handler.",
            events,
        )


# -----------------------------------------------------------------------
# Intent handlers — each returns (markdown_response, thought_events)
# -----------------------------------------------------------------------


def _handle_detect(
    session: ChatSession, _query: str,
) -> tuple[str, list]:
    from discovery.profile import generate_profile

    events = []
    with ThoughtStream(session.renderer.console) as ts:
        ts.emit(ThoughtPhase.detecting, "Scanning hardware environment")
        profile = generate_profile()

        ts.emit(
            ThoughtPhase.detecting,
            f"CPU: {profile.cpu_info.model} "
            f"({profile.cpu_info.cores}C/{profile.cpu_info.threads}T)",
        )
        ts.emit(
            ThoughtPhase.detecting,
            f"RAM: {profile.ram_info.total_gb:.1f} GB",
        )
        if profile.gpu_info:
            ts.emit(
                ThoughtPhase.detecting,
                f"GPU: {profile.gpu_info.model} "
                f"({profile.gpu_info.vram_gb:.1f} GB VRAM)",
            )
        ts.emit(
            ThoughtPhase.complete,
            f"Tier: {profile.hardware_tier}",
        )
        events = ts.events

    lines = [
        f"Your machine is classified as **{profile.hardware_tier}** tier.\n",
        "| Property | Value |",
        "|----------|-------|",
        f"| OS | {profile.os_info.os_name} {profile.os_info.os_version} |",
        (
            f"| CPU | {profile.cpu_info.model} "
            f"({profile.cpu_info.cores}C/{profile.cpu_info.threads}T) |"
        ),
        f"| RAM | {profile.ram_info.total_gb:.1f} GB |",
    ]
    if profile.gpu_info:
        lines.append(
            f"| GPU | {profile.gpu_info.model} "
            f"({profile.gpu_info.vram_gb:.1f} GB VRAM) |"
        )
    lines.append(
        f"| Performance | {profile.expected_performance} |"
    )
    lines.append(f"\n{profile.summary}")
    lines.append("\nRun **/recommend** to see model suggestions.")
    return "\n".join(lines), events


def _handle_recommend(
    session: ChatSession, query: str,
) -> tuple[str, list]:
    from discovery.profile import generate_profile
    from recommendation.engine import RecommendationEngine

    intent = "coding"
    if "reason" in query.lower():
        intent = "reasoning"
    elif "general" in query.lower():
        intent = "general"

    events = []
    with ThoughtStream(session.renderer.console) as ts:
        ts.emit(ThoughtPhase.detecting, "Profiling hardware")
        profile = generate_profile()
        ts.emit(
            ThoughtPhase.analyzing,
            f"Filtering catalog (intent: {intent})",
        )
        rec = RecommendationEngine().recommend(profile, intent=intent)
        ts.emit(
            ThoughtPhase.routing,
            f"Tier: {rec.hardware_tier}",
        )
        count = sum(1 for b in [rec.primary, rec.lighter, rec.heavier] if b)
        ts.emit(ThoughtPhase.complete, f"Ranked {count} candidates")
        events = ts.events

    lines = [
        f"Based on your **{rec.hardware_tier}** hardware:\n",
        "| Pick | Model | Size | Runtime | Disk |",
        "|------|-------|------|---------|------|",
    ]
    for label, bundle in [
        ("Primary", rec.primary),
        ("Lighter", rec.lighter),
        ("Heavier", rec.heavier),
    ]:
        if bundle:
            lines.append(
                f"| {label} | {bundle.display_name} "
                f"| {bundle.parameter_count} "
                f"| {bundle.runtime} "
                f"| {bundle.estimated_disk_gb:.1f} GB |"
            )
    lines.append(f"\n{rec.machine_summary}")
    for mid, expl in rec.explanations.items():
        lines.append(f"- **{mid}**: {expl}")
    return "\n".join(lines), events


def _handle_audit(
    session: ChatSession, _query: str,
) -> tuple[str, list]:
    from tools.repo_auditor import RepoAuditor

    events = []
    with ThoughtStream(session.renderer.console) as ts:
        ts.emit(
            ThoughtPhase.analyzing,
            f"Auditing {session.repo_path.name}",
        )
        report = RepoAuditor(session.repo_path).audit()
        ts.emit(
            ThoughtPhase.detecting,
            f"Languages: {', '.join(report.languages)}",
        )
        ts.emit(
            ThoughtPhase.complete,
            f"Found {len(report.marker_files)} markers",
        )
        events = ts.events

    lines = [
        f"Audit of **{session.repo_path.name}**:\n",
        "| Property | Value |",
        "|----------|-------|",
        f"| Languages | {', '.join(report.languages)} |",
        f"| Package mgrs | {', '.join(report.package_managers)} |",
        f"| Test frameworks | {', '.join(report.test_frameworks)} |",
        f"| CI configs | {', '.join(report.ci_configs)} |",
    ]
    return "\n".join(lines), events


def _handle_memory(
    session: ChatSession, _query: str,
) -> tuple[str, list]:
    from memory_skill.reader import MemoryReader

    reader = MemoryReader(_REPO_ROOT)
    arch = reader.read_architecture()

    if not arch:
        return (
            "No memory data found. Run `clawsmith memory sync` "
            "or **/detect** first.",
            [],
        )

    lines = [
        "**Persisted architecture memory:**\n",
        "| Property | Value |",
        "|----------|-------|",
        f"| Tier | {arch.hardware_tier} |",
        f"| OS | {arch.os_name} {arch.os_version} |",
        f"| CPU | {arch.cpu_summary} |",
        f"| RAM | {arch.ram_gb:.1f} GB |",
        f"| GPU | {arch.gpu_summary or 'None'} |",
        f"| Models | {len(arch.installed_models)} |",
        f"| Runtimes | {len(arch.installed_runtimes)} |",
        f"| Repos | {len(arch.repos)} |",
    ]
    return "\n".join(lines), []


def _handle_install(
    session: ChatSession, _query: str,
) -> tuple[str, list]:
    return (
        "Model installation requires confirmation prompts that work "
        "better via the CLI:\n\n"
        "```\nclawsmith install-model\n```\n\n"
        "Or specify a model:\n\n"
        "```\nclawsmith install-model --model-id codellama-34b-q4\n```",
        [],
    )


def _handle_link(
    session: ChatSession, _query: str,
) -> tuple[str, list]:
    return (
        "To link a repository to the workspace graph:\n\n"
        "```\nclawsmith link-repo /path/to/repo --role primary\n```\n\n"
        "This adds the repo to ClawSmith's dependency graph "
        "for cross-repo awareness.",
        [],
    )


def _handle_scope(
    session: ChatSession, _query: str,
) -> tuple[str, list]:
    scopes_dir = session.repo_path / ".clawsmith" / "scopes"
    if not scopes_dir.exists() or not list(scopes_dir.glob("*.json")):
        return (
            "No active scope contracts. Create one with:\n\n"
            "```\nclawsmith scope --task 'your task description'\n```",
            [],
        )

    from scope_engine.engine import ScopeEngine

    engine = ScopeEngine(workspace_root=session.repo_path)
    lines = ["**Active scope contracts:**\n"]
    for f in sorted(scopes_dir.glob("*.json")):
        contract = engine.load_contract(f)
        lines.append(
            f"- `{contract.task_id}` — primary: {contract.primary_repo}, "
            f"repos: {len(contract.repos)}"
        )
    return "\n".join(lines), []


# -----------------------------------------------------------------------
# Conversation handler — responds instantly without the heavy pipeline
# -----------------------------------------------------------------------

_RESPONSE_GREETING = (
    "Hello! I'm **ClawSmith**, your local-first AI orchestration "
    "assistant. Type **/help** for available commands, or describe "
    "a coding task and I'll route it through the pipeline.\n\n"
    "Try something like:\n"
    "- \"Fix the bug in auth.py\"\n"
    "- \"Add unit tests for the API module\"\n"
    "- Or use **/detect** to scan your hardware"
)

_RESPONSE_CAPABILITIES = (
    "Here's what I can do:\n\n"
    "**Slash commands** (type /help for the full list):\n"
    "- **/detect** — scan your hardware and AI tooling\n"
    "- **/recommend** — get local model recommendations\n"
    "- **/agents** — see detected agent CLIs\n"
    "- **/doctor** — run environment health checks\n"
    "- **/memory** — view persistent architecture memory\n"
    "- **/scope** — manage cross-repo scope contracts\n\n"
    "**Task execution** — describe a coding task in natural language:\n"
    "1. I'll audit your repository structure\n"
    "2. Classify the task complexity\n"
    "3. Route to the right model tier (local or cloud)\n"
    "4. Generate an optimized prompt\n"
    "5. Execute via your preferred agent CLI "
    "(Cursor, Claude, Gemini)\n\n"
    "**Examples:**\n"
    "- \"Fix the authentication bug in login.py\"\n"
    "- \"Add dark mode support to the settings page\"\n"
    "- \"Refactor the database layer to use async\"\n"
    "- \"Run tests and fix any failures\""
)

_RESPONSE_THANKS = (
    "You're welcome! Let me know if there's anything else "
    "I can help with."
)

_RESPONSE_GUIDANCE = (
    "I'm not sure what you'd like me to do. Try one of these:\n\n"
    "- **Describe a coding task**: \"Fix the bug in auth.py\"\n"
    "- **Use a slash command**: **/help**, **/detect**, "
    "**/recommend**\n"
    "- **Ask about capabilities**: \"What can you do?\""
)


def _handle_conversation(
    session: ChatSession, query: str,
) -> tuple[str, list]:
    """Handle greetings, capability questions, and other non-task input."""
    q = query.lower().strip()
    words = q.split()
    if not words:
        return _RESPONSE_GUIDANCE, []

    first = words[0].rstrip("!.,?;:")

    # Greeting that might be followed by a real task:
    # "hi, fix the bug in auth.py" → delegate to the pipeline.
    if first in _GREETING_WORDS:
        rest = " ".join(words[1:]).lstrip("!.,?;: ")
        if rest and _looks_like_task(rest):
            return _handle_task(session, query)

    # Capability / meta questions (also covers "hello, what can you do?")
    if any(p in q for p in _CAPABILITY_PHRASES):
        return _RESPONSE_CAPABILITIES, []

    # Pure greeting
    if first in _GREETING_WORDS or any(
        q.startswith(p)
        for p in ("good morning", "good afternoon", "good evening")
    ):
        return _RESPONSE_GREETING, []

    # Thanks / acknowledgement
    if first in _THANKS_WORDS or any(
        w.rstrip("!.,?;:") in _THANKS_WORDS for w in words
    ):
        return _RESPONSE_THANKS, []

    return _RESPONSE_GUIDANCE, []


def _handle_task(
    session: ChatSession, query: str,
) -> tuple[str, list]:
    """Run the full orchestration pipeline for a genuine coding task."""
    from orchestrator.pipeline import OrchestrationPipeline

    events = []
    with ThoughtStream(session.renderer.console) as ts:
        ts.emit(ThoughtPhase.analyzing, "Parsing task intent")
        ts.emit(
            ThoughtPhase.analyzing,
            f"Repository: {session.repo_path.name}",
        )
        ts.emit(ThoughtPhase.routing, "Running orchestration pipeline")
        events = ts.events

        try:
            result = asyncio.run(
                OrchestrationPipeline().run(
                    query,
                    str(session.repo_path),
                    dry_run=False,
                )
            )
        except Exception as exc:
            ts.emit(ThoughtPhase.error, str(exc))
            return f"Pipeline error: {exc}", ts.events

        if result.routing_decision:
            rd = result.routing_decision
            ts.emit(
                ThoughtPhase.routing,
                f"Tier: {rd.selected_tier.value} "
                f"({rd.model_name})",
            )
        if result.execution_result:
            er = result.execution_result
            ts.emit(
                ThoughtPhase.executing,
                f"Exit code: {er.exit_code}",
            )
        status = "completed" if result.success else "failed"
        ts.emit(
            ThoughtPhase.complete if result.success else ThoughtPhase.error,
            f"Pipeline {status} in {result.duration_seconds:.1f}s",
        )
        events = ts.events

    if result.success:
        parts = [
            f"Pipeline **completed** in "
            f"{result.duration_seconds:.1f}s.\n",
        ]
        if result.routing_decision:
            rd = result.routing_decision
            parts.append(
                f"- Tier: **{rd.selected_tier.value}** "
                f"({rd.model_name})"
            )
            parts.append(f"- Confidence: {rd.confidence_score:.0%}")
        if result.execution_result:
            er = result.execution_result
            parts.append(f"- Exit code: {er.exit_code}")
            if er.agent_used:
                parts.append(f"- Agent: {er.agent_used}")
        return "\n".join(parts), events

    return f"Pipeline failed: {result.error_message}", events


_INTENT_HANDLERS: dict[str, object] = {
    "detect": _handle_detect,
    "recommend": _handle_recommend,
    "audit": _handle_audit,
    "memory": _handle_memory,
    "install": _handle_install,
    "link": _handle_link,
    "scope": _handle_scope,
    "conversation": _handle_conversation,
    "task": _handle_task,
}
