"""Main interactive chat session for the ClawSmith TUI."""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.console import Console

from tui.commands import CommandRouter
from tui.models import ChatMessage, MessageRole, ThoughtPhase
from tui.renderer import Renderer
from tui.theme import CLAWSMITH_THEME
from tui.thinking import ThoughtStream

_REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Simple keyword-based intent routing (no LLM needed)
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


def _detect_intent(query: str) -> str:
    """Return the best-matching intent key, or 'task' as fallback."""
    q = query.lower().strip()
    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(kw in q for kw in keywords):
            return intent
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

    # -- main loop --------------------------------------------------------

    def run(self) -> None:
        """Start the interactive session."""
        self.renderer.logo()
        self.renderer.welcome()

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

    # -- routing ----------------------------------------------------------

    def _execute(self, query: str) -> tuple[str, list]:
        """Route *query* to the right subsystem and return (response, thoughts)."""
        intent = _detect_intent(query)
        handler = _INTENT_HANDLERS.get(intent, _handle_task)
        return handler(self, query)


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


def _handle_task(
    session: ChatSession, query: str,
) -> tuple[str, list]:
    """Fallback: run the full orchestration pipeline."""
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
    "task": _handle_task,
}
