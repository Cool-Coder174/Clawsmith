"""Slash-command router for the interactive TUI."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from tui.models import ThoughtPhase

if TYPE_CHECKING:
    from tui.session import ChatSession

_REPO_ROOT = Path(__file__).resolve().parent.parent

CommandHandler = Callable[["ChatSession", list[str]], None]


class CommandRouter:
    """Maps ``/name`` commands to handler functions."""

    def __init__(self) -> None:
        self._commands: dict[str, tuple[CommandHandler, str]] = {}
        self._register_builtins()

    def register(
        self,
        name: str,
        handler: CommandHandler,
        help_text: str,
    ) -> None:
        self._commands[name] = (handler, help_text)

    def dispatch(
        self,
        raw_input: str,
        session: ChatSession,
    ) -> bool:
        """Parse and run a ``/command``.  Returns *True* if handled."""
        parts = raw_input.strip().lstrip("/").split(None, 1)
        if not parts:
            return False
        name = parts[0].lower()
        args = parts[1].split() if len(parts) > 1 else []
        handler_pair = self._commands.get(name)
        if handler_pair is None:
            session.renderer.error_message(
                f"Unknown command: /{name}  — type /help"
            )
            return True
        handler_pair[0](session, args)
        return True

    def help_text(self) -> str:
        lines = ["  Available commands:\n"]
        for name, (_, desc) in sorted(self._commands.items()):
            lines.append(f"    /{name:<14s} {desc}")
        return "\n".join(lines)

    # -- built-in commands ------------------------------------------------

    def _register_builtins(self) -> None:
        self.register("help", _cmd_help, "Show available commands")
        self.register("quit", _cmd_quit, "Exit ClawSmith")
        self.register("exit", _cmd_quit, "Exit ClawSmith")
        self.register("clear", _cmd_clear, "Clear screen")
        self.register("detect", _cmd_detect, "Detect hardware & toolchain")
        self.register(
            "recommend", _cmd_recommend, "Recommend local LLMs"
        )
        self.register("status", _cmd_status, "Show session info")
        self.register("memory", _cmd_memory, "Show persisted memory")
        self.register("doctor", _cmd_doctor, "Run preflight checks")
        self.register("scope", _cmd_scope, "View scope contracts")
        self.register("agents", _cmd_agents, "List detected agent CLIs")


# -----------------------------------------------------------------------
# Handler implementations
# -----------------------------------------------------------------------


def _cmd_help(session: ChatSession, _args: list[str]) -> None:
    session.renderer.agent_message(session.commands.help_text())


def _cmd_quit(session: ChatSession, _args: list[str]) -> None:
    session.stop()


def _cmd_clear(session: ChatSession, _args: list[str]) -> None:
    session.renderer.console.clear()
    session.renderer.logo()
    session.renderer.welcome()


def _cmd_detect(session: ChatSession, _args: list[str]) -> None:
    """Run hardware detection with live thinking."""
    from tui.thinking import ThoughtStream

    try:
        with ThoughtStream(session.renderer.console) as ts:
            ts.emit(ThoughtPhase.detecting, "Scanning hardware environment")

            from discovery.profile import generate_profile

            profile = generate_profile()

            ts.emit(
                ThoughtPhase.detecting,
                f"OS: {profile.os_info.os_name} "
                f"{profile.os_info.os_version}",
            )
            ts.emit(
                ThoughtPhase.detecting,
                f"CPU: {profile.cpu_info.model} "
                f"({profile.cpu_info.cores}C/{profile.cpu_info.threads}T)",
            )
            ts.emit(
                ThoughtPhase.detecting,
                f"RAM: {profile.ram_info.total_gb:.1f} GB "
                f"({profile.ram_info.available_gb:.1f} GB free)",
            )

            if profile.gpu_info:
                ts.emit(
                    ThoughtPhase.detecting,
                    f"GPU: {profile.gpu_info.model} "
                    f"({profile.gpu_info.vram_gb:.1f} GB VRAM)",
                )
            else:
                ts.emit(ThoughtPhase.detecting, "GPU: None detected")

            ts.emit(
                ThoughtPhase.complete,
                f"Classified as '{profile.hardware_tier}' tier",
            )

        rows: list[tuple[str, str]] = [
            ("Hardware Tier", str(profile.hardware_tier)),
            (
                "OS",
                f"{profile.os_info.os_name} "
                f"{profile.os_info.os_version} "
                f"({profile.os_info.architecture})",
            ),
            (
                "CPU",
                f"{profile.cpu_info.model} "
                f"({profile.cpu_info.cores}C/"
                f"{profile.cpu_info.threads}T)",
            ),
            (
                "RAM",
                f"{profile.ram_info.total_gb:.1f} GB total, "
                f"{profile.ram_info.available_gb:.1f} GB available",
            ),
        ]
        if profile.gpu_info:
            rows.append((
                "GPU",
                f"{profile.gpu_info.model} "
                f"({profile.gpu_info.vram_gb:.1f} GB VRAM, "
                f"{profile.gpu_info.compute_backend})",
            ))
        else:
            rows.append(("GPU", "None detected"))

        for v in profile.storage_info.volumes:
            rows.append((
                f"Disk {v.device_id}",
                f"{v.free_gb:.1f} GB free / {v.total_gb:.1f} GB",
            ))
        rows.append((
            "Feasible Sizes",
            ", ".join(profile.feasible_model_sizes),
        ))
        rows.append((
            "Backends",
            ", ".join(profile.recommended_backends),
        ))
        rows.append(("Performance", profile.expected_performance))
        if profile.likely_bottlenecks:
            rows.append((
                "Bottlenecks",
                "; ".join(profile.likely_bottlenecks),
            ))

        devtools = [t for t in profile.toolchain.developer_tools if t.found]
        if devtools:
            rows.append((
                "Dev Tools",
                ", ".join(f"{t.name} {t.version or ''}" for t in devtools),
            ))

        ai = [t for t in profile.toolchain.ai_tooling if t.found]
        if ai:
            rows.append((
                "AI Tooling",
                ", ".join(f"{t.name} {t.version or ''}" for t in ai),
            ))

        session.renderer.blank()
        session.renderer.key_value_table("Machine Profile", rows)
        session.renderer.system_message(profile.summary)

    except Exception as exc:
        session.renderer.error_message(f"Detection failed: {exc}")


def _cmd_recommend(session: ChatSession, _args: list[str]) -> None:
    """Recommend models with live thinking."""
    from tui.thinking import ThoughtStream

    intent = _args[0] if _args else "coding"

    try:
        with ThoughtStream(session.renderer.console) as ts:
            ts.emit(ThoughtPhase.detecting, "Profiling hardware")

            from discovery.profile import generate_profile
            from recommendation.engine import RecommendationEngine

            profile = generate_profile()
            ts.emit(
                ThoughtPhase.analyzing,
                f"Filtering catalog (intent: {intent})",
            )
            rec = RecommendationEngine().recommend(profile, intent=intent)
            ts.emit(
                ThoughtPhase.routing,
                f"Hardware tier: {rec.hardware_tier}",
            )
            ts.emit(ThoughtPhase.complete, "Ranked candidates")

        columns = [
            ("Pick", "success"),
            ("Model", "brand"),
            ("Size", ""),
            ("Quant", ""),
            ("Runtime", ""),
            ("Disk", "warning"),
            ("RAM", "warning"),
            ("Context", ""),
        ]
        rows: list[list[str]] = []
        for label, bundle in [
            ("Primary", rec.primary),
            ("Lighter", rec.lighter),
            ("Heavier", rec.heavier),
        ]:
            if bundle:
                rows.append([
                    label,
                    bundle.display_name,
                    bundle.parameter_count,
                    bundle.quantization,
                    bundle.runtime,
                    f"{bundle.estimated_disk_gb:.1f} GB",
                    f"{bundle.estimated_ram_gb:.1f} GB",
                    f"{bundle.context_size:,}",
                ])

        session.renderer.blank()
        session.renderer.ranked_table(
            f"Recommended Models (intent={intent})",
            columns,
            rows,
        )
        session.renderer.blank()
        session.renderer.system_message(rec.machine_summary)

        for mid, explanation in rec.explanations.items():
            session.renderer.system_message(f"  {mid}: {explanation}")

    except Exception as exc:
        session.renderer.error_message(f"Recommendation failed: {exc}")


def _cmd_status(session: ChatSession, _args: list[str]) -> None:
    rows: list[tuple[str, str]] = [
        ("Repository", str(session.repo_path)),
        ("Messages", str(len(session.history))),
        ("Version", "0.1.0"),
    ]
    session.renderer.key_value_table("Session", rows)


def _cmd_memory(session: ChatSession, _args: list[str]) -> None:
    try:
        from memory_skill.reader import MemoryReader

        reader = MemoryReader(_REPO_ROOT)
        arch = reader.read_architecture()
        if arch:
            rows: list[tuple[str, str]] = [
                ("Hardware Tier", arch.hardware_tier),
                ("OS", f"{arch.os_name} {arch.os_version}"),
                ("CPU", arch.cpu_summary),
                ("RAM", f"{arch.ram_gb:.1f} GB"),
                ("GPU", arch.gpu_summary or "None"),
                ("Models", str(len(arch.installed_models))),
                ("Runtimes", str(len(arch.installed_runtimes))),
                ("Repos", str(len(arch.repos))),
            ]
            session.renderer.key_value_table("Architecture Memory", rows)
        else:
            session.renderer.system_message(
                "No memory data. Run /detect first or `clawsmith memory sync`."
            )
    except Exception as exc:
        session.renderer.error_message(f"Memory read failed: {exc}")


def _cmd_doctor(session: ChatSession, _args: list[str]) -> None:
    from tui.thinking import ThoughtStream

    try:
        from orchestrator.doctor import run_doctor

        with ThoughtStream(session.renderer.console) as ts:
            ts.emit(ThoughtPhase.analyzing, "Running preflight checks")
            ok = run_doctor()
            phase = ThoughtPhase.complete if ok else ThoughtPhase.error
            msg = "All checks passed" if ok else "Some checks failed"
            ts.emit(phase, msg)
    except Exception as exc:
        session.renderer.error_message(f"Doctor failed: {exc}")


def _cmd_scope(session: ChatSession, _args: list[str]) -> None:
    try:
        scopes_dir = session.repo_path / ".clawsmith" / "scopes"
        if not scopes_dir.exists() or not list(scopes_dir.glob("*.json")):
            session.renderer.system_message(
                "No active scope contracts. "
                "Use `clawsmith scope --task '...'` to create one."
            )
            return

        from scope_engine.engine import ScopeEngine

        engine = ScopeEngine(workspace_root=session.repo_path)
        for f in sorted(scopes_dir.glob("*.json")):
            contract = engine.load_contract(f)
            session.renderer.system_message(
                f"  {contract.task_id}  "
                f"primary={contract.primary_repo}  "
                f"repos={len(contract.repos)}"
            )
    except Exception as exc:
        session.renderer.error_message(f"Scope read failed: {exc}")


def _cmd_agents(session: ChatSession, _args: list[str]) -> None:
    try:
        from agents.registry import get_agent_registry

        registry = get_agent_registry(auto_detect=True)
        matrix = registry.get_capability_matrix()

        columns = [
            ("Agent", "brand"),
            ("Available", ""),
            ("Version", ""),
            ("Capabilities", "muted"),
        ]
        rows: list[list[str]] = []
        for aid, info in matrix.items():
            avail = "Yes" if info["available"] else "No"
            ver = info["version"] or "-"
            caps = ", ".join(info["capabilities"][:4])
            rows.append([aid, avail, ver, caps])

        session.renderer.ranked_table("Detected Agents", columns, rows)
    except Exception as exc:
        session.renderer.error_message(f"Agent scan failed: {exc}")
