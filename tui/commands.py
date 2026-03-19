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
        self.register("yolo", _cmd_yolo, "YOLO mode — autonomous multi-phase execution")
        self.register("skills", _cmd_skills, "List / regen / explain skills")
        self.register("context", _cmd_context, "Show current session context")
        self.register("plan", _cmd_plan, "Show current execution plan")
        self.register("remember", _cmd_remember, "Store an always-remember memory")
        self.register("openclaw", _cmd_openclaw, "OpenClaw integration status")
        self.register("spec", _cmd_spec, "Generate structured spec from goal")
        self.register("verify", _cmd_verify, "Verify diff against a spec")
        self.register("specs", _cmd_specs, "List all available specs")


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

    status = getattr(session, "last_agent_status", None)
    if status:
        rows.append(("Agent Phase", status.get("phase", "unknown")))
        if status.get("verify_stage"):
            rows.append(("Verify Stage", status["verify_stage"]))
        rows.append(("Pipeline Steps", str(status.get("step_count", 0))))
        rows.append(("Last Step", status.get("latest_step", "")))
        rows.append(("Elapsed", f"{status.get('elapsed_seconds', 0):.1f}s"))
        rows.append(("Terminal", "Yes" if status.get("is_terminal") else "No"))
    else:
        rows.append(("Agent Status", "No pipeline run yet"))

    session.renderer.key_value_table("Session & Agent Status", rows)


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


def _cmd_yolo(session: ChatSession, args: list[str]) -> None:
    """Run YOLO mode — autonomous multi-phase execution."""
    import asyncio

    from tui.thinking import ThoughtStream

    goal = " ".join(args).strip() if args else ""
    if not goal:
        session.renderer.error_message(
            "Usage: /yolo <goal>\n"
            "Example: /yolo Add user authentication with JWT"
        )
        return

    from orchestrator.agent_status import StatusTracker
    from orchestrator.yolo import YoloEngine

    tracker = StatusTracker()

    with ThoughtStream(session.renderer.console, tracker=tracker) as ts:
        try:
            result = asyncio.run(
                YoloEngine().execute(
                    goal,
                    str(session.repo_path),
                    status=tracker,
                )
            )
        except Exception as exc:
            ts.emit(ThoughtPhase.error, str(exc))
            session.renderer.error_message(f"YOLO failed: {exc}")
            return

    session.last_agent_status = result.agent_status or tracker.summary()

    if result.success:
        parts = [
            f"YOLO run **completed** in {result.duration_seconds:.1f}s.\n",
            f"- Phases: **{result.completed_phases}/{result.total_phases}** completed",
        ]
        if result.agent_status:
            phase = result.agent_status.get("phase", "unknown")
            parts.append(f"- Final status: **{phase}**")
        session.renderer.agent_message("\n".join(parts))
    else:
        parts = [
            f"YOLO run **failed** after {result.duration_seconds:.1f}s.\n",
            f"- Completed: {result.completed_phases}/{result.total_phases}",
            f"- Failed: {result.failed_phases}",
        ]
        if result.error_message:
            parts.append(f"- Error: {result.error_message}")
        session.renderer.agent_message("\n".join(parts))

    if result.phase_results:
        columns = [
            ("#", "dim"),
            ("Phase", "brand"),
            ("Status", ""),
            ("Attempts", ""),
        ]
        rows: list[list[str]] = []
        for pr in result.phase_results:
            rows.append([
                str(pr.phase_index + 1),
                pr.title,
                pr.status.value,
                str(pr.attempts),
            ])
        session.renderer.ranked_table("Phase Summary", columns, rows)


# -----------------------------------------------------------------------
# Skill, context, plan, remember, openclaw commands
# -----------------------------------------------------------------------


def _cmd_skills(session: ChatSession, args: list[str]) -> None:
    """List, regenerate, or explain skills."""
    from orchestrator.chat_runtime import ChatRuntime

    runtime = _get_session_runtime(session)

    sub = args[0].lower() if args else "list"

    if sub == "regen":
        session.renderer.system_message("Regenerating skills from repo...")
        count = runtime.regenerate_skills()
        session.renderer.system_message(f"Generated {count} skill(s).")
        return

    if sub == "why" and len(args) > 1:
        task = " ".join(args[1:])
        result = runtime.select_skills_for(task)
        if result["scored"]:
            columns = [
                ("Skill", "brand"),
                ("Score", "success"),
                ("Reason", "muted"),
            ]
            rows = [
                [s["name"], f"{s['score']:.2f}", s["reason"]]
                for s in result["scored"][:10]
            ]
            session.renderer.ranked_table("Skill Scoring", columns, rows)
        session.renderer.system_message(result.get("explanation", ""))
        return

    skills = runtime.list_skills()
    if not skills:
        session.renderer.system_message(
            "No skills loaded. Run /skills regen to generate from repo."
        )
        return

    columns = [
        ("Name", "brand"),
        ("Source", ""),
        ("Enabled", "success"),
        ("Confidence", ""),
        ("Triggers", "muted"),
    ]
    rows = [
        [
            s["name"],
            s["source_type"],
            "Yes" if s["enabled"] else "No",
            f"{s['confidence']:.0%}",
            ", ".join(s["triggers"][:3]),
        ]
        for s in skills
    ]
    session.renderer.ranked_table("Loaded Skills", columns, rows)


def _cmd_context(session: ChatSession, _args: list[str]) -> None:
    """Show current session context including skills and memory."""
    runtime = _get_session_runtime(session)
    explain = runtime.state.get_explainability_summary()

    rows: list[tuple[str, str]] = [
        ("Repository", str(runtime.state.repo_path)),
        ("Turn Count", str(explain.get("turn_count", 0))),
        ("Skills Loaded", str(explain.get("skills_loaded", 0))),
        ("Dry Run", str(explain.get("dry_run", False))),
        ("Safe Mode", str(explain.get("safe_mode", True))),
        ("Model", explain.get("model", "auto")),
        ("Stacks", ", ".join(runtime.state.repo_stacks) or "unknown"),
    ]

    skill_sel = explain.get("skill_selection")
    if skill_sel:
        rows.append(("Selected Skills", ", ".join(skill_sel.get("selected", []))))
        rows.append(("Skill Explanation", skill_sel.get("explanation", "")))

    mem = explain.get("memory_retrieval")
    if mem:
        rows.append(("Memory Entries", f"{mem.get('entries', 0)}/{mem.get('total_candidates', 0)}"))
        rows.append(("Memory Explanation", mem.get("explanation", "")))

    session.renderer.key_value_table("Session Context", rows)


def _cmd_plan(session: ChatSession, _args: list[str]) -> None:
    """Show current execution plan."""
    runtime = _get_session_runtime(session)
    plan = runtime.state.current_plan
    if not plan:
        session.renderer.system_message("No active plan. Submit a task to generate one.")
        return
    import json
    session.renderer.system_message(json.dumps(plan, indent=2))


def _cmd_remember(session: ChatSession, args: list[str]) -> None:
    """Store, list, promote, suppress, or decay always-remember memory.

    Sub-commands:
        /remember                  — list entries
        /remember <text>           — store a new entry
        /remember promote <text>   — promote an outcome
        /remember suppress <id>    — suppress an entry
        /remember unsuppress <id>  — un-suppress an entry
        /remember decay            — auto-suppress noisy entries
        /remember why <task>       — explain retrieval for a task
    """
    runtime = _get_session_runtime(session)

    if not args:
        entries = runtime.list_memories()
        if not entries:
            session.renderer.system_message(
                "No always-remember entries. Usage: /remember <text to remember>"
            )
            return
        columns = [
            ("ID", "dim"),
            ("Category", "brand"),
            ("Content", ""),
            ("Score", "success"),
            ("Hits", ""),
            ("Accepts", ""),
            ("Tags", "muted"),
        ]
        rows = [
            [
                e.get("id", "")[:8],
                e.get("category", "note"),
                e.get("content", "")[:45],
                f"{e.get('usefulness_score', 0.0):.1f}",
                str(e.get("hit_count", 0)),
                str(e.get("accept_count", 0)),
                ", ".join(e.get("tags", [])[:3]),
            ]
            for e in entries
        ]
        session.renderer.ranked_table("Always Remember", columns, rows)
        return

    sub = args[0].lower()

    if sub == "promote" and len(args) > 1:
        content = " ".join(args[1:])
        eid = runtime.promote_outcome(content, category="user_promoted", tags=["promoted"])
        session.renderer.system_message(f"Promoted to memory (id={eid}): {content}")
        return

    if sub == "suppress" and len(args) > 1:
        eid = args[1]
        ok = runtime.suppress_memory(eid)
        if ok:
            session.renderer.system_message(f"Suppressed memory {eid}")
        else:
            session.renderer.error_message(f"Memory {eid} not found")
        return

    if sub == "unsuppress" and len(args) > 1:
        eid = args[1]
        ok = runtime.unsuppress_memory(eid)
        if ok:
            session.renderer.system_message(f"Unsuppressed memory {eid}")
        else:
            session.renderer.error_message(f"Memory {eid} not found")
        return

    if sub == "decay":
        suppressed = runtime.decay_memories()
        if suppressed:
            session.renderer.system_message(
                f"Auto-suppressed {len(suppressed)} noisy entries: {', '.join(suppressed)}"
            )
        else:
            session.renderer.system_message("No entries met the decay threshold.")
        return

    if sub == "why" and len(args) > 1:
        task = " ".join(args[1:])
        mems = runtime.retrieve_memories_for(task)
        if not mems:
            session.renderer.system_message("No memories retrieved for that task.")
            return
        columns = [
            ("Category", "brand"),
            ("Content", ""),
            ("Score", "success"),
            ("Why", "muted"),
        ]
        rows = [
            [
                m["category"],
                m["content"][:40],
                f"{m['relevance']:.3f}",
                m.get("explanation", ""),
            ]
            for m in mems[:10]
        ]
        session.renderer.ranked_table("Memory Retrieval Explanation", columns, rows)
        return

    content = " ".join(args)
    entry_id = runtime.remember(content, category="user_note", tags=["user"])
    session.renderer.system_message(f"Remembered (id={entry_id}): {content}")


def _cmd_specs(session: ChatSession, _args: list[str]) -> None:
    """List all available specs."""
    try:
        from tui.spec_commands import list_specs
        
        specs = list_specs(session.repo_path)
        
        if not specs:
            session.renderer.system_message(
                "No specs found. Run **/spec <goal>** to generate one.\n\n"
                "Example: `/spec add user authentication with JWT`"
            )
            return
        
        columns = [
            ("ID", "dim"),
            ("Goal", ""),
            ("Tier", ""),
            ("Files", ""),
            ("Phases", ""),
        ]
        rows = [
            [
                s["id"][:12],
                s["goal"][:40],
                s["tier"],
                str(s["file_count"]),
                str(s["phase_count"]),
            ]
            for s in specs
        ]
        session.renderer.ranked_table(f"Specs ({len(specs)})", columns, rows)
    except Exception as exc:
        session.renderer.error_message(f"Failed to list specs: {exc}")


def _cmd_spec(session: ChatSession, args: list[str]) -> None:
    """Generate a structured spec from a goal description.

    Usage:
        /spec <goal>              — generate a full spec
        /spec --epic <goal>       — generate a phased epic spec
        /spec --quick <goal>      — generate a quick spec
    """
    import asyncio
    from tui.spec_commands import format_spec_summary, generate_spec_from_goal
    from orchestrator.spec_generator import SpecTier

    if not args:
        session.renderer.system_message(
            "Usage: **/spec <goal>**\n\n"
            "Options:\n"
            "  `--epic` — multi-phase complex feature\n"
            "  `--quick` — simple single-file change\n\n"
            "Examples:\n"
            "  `/spec add OAuth2 login`\n"
            "  `/spec --epic build a real-time chat system`"
        )
        return

    # Parse tier flags
    tier = None
    goal_args = []
    for arg in args:
        if arg == "--epic":
            tier = SpecTier.epic
        elif arg == "--quick":
            tier = SpecTier.quick
        else:
            goal_args.append(arg)

    goal = " ".join(goal_args)

    from tui.thinking import ThoughtStream

    tracker = None
    with ThoughtStream(session.renderer.console) as ts:
        ts.emit(ThoughtPhase.analyzing, "Auditing repo and gathering context")
        try:
            spec, spec_path = asyncio.run(
                generate_spec_from_goal(goal, session.repo_path, tier)
            )
            ts.emit(ThoughtPhase.complete, f"Generated {spec.id}")
        except Exception as exc:
            ts.emit(ThoughtPhase.error, str(exc))
            session.renderer.error_message(f"Spec generation failed: {exc}")
            return

    summary = format_spec_summary(spec)
    footer = (
        f"\n---\n"
        f"📁 Saved to `.clawsmith/specs/{spec.id}.md`\n"
        f"Run **/verify {spec.id}** after implementing to check your work."
    )
    session.renderer.agent_message(summary + footer)


def _cmd_verify(session: ChatSession, args: list[str]) -> None:
    """Verify the current diff against a spec.

    Usage:
        /verify                  — list all specs
        /verify <spec-id>        — verify a specific spec
    """
    import asyncio
    from tui.spec_commands import format_verification_report, verify_spec

    spec_id = args[0] if args else None

    if not spec_id:
        # List specs
        from tui.spec_commands import list_specs
        specs = list_specs(session.repo_path)
        if not specs:
            session.renderer.system_message(
                "No specs found. Generate one with **/spec <goal>**."
            )
            return
        columns = [
            ("ID", "dim"),
            ("Goal", ""),
            ("Tier", ""),
            ("Files", ""),
        ]
        rows = [
            [s["id"][:12], s["goal"][:45], s["tier"], str(s["file_count"])]
            for s in specs
        ]
        session.renderer.ranked_table(f"Available Specs ({len(specs)})", columns, rows)
        session.renderer.system_message(
            "\nRun **/verify <spec-id>** to verify a spec against your current diff."
        )
        return

    from tui.thinking import ThoughtStream

    with ThoughtStream(session.renderer.console) as ts:
        ts.emit(ThoughtPhase.analyzing, "Scanning working tree diff")
        try:
            report = asyncio.run(verify_spec(spec_id, session.repo_path))
            ts.emit(ThoughtPhase.complete, f"Verified: {report.score:.0%}")
        except FileNotFoundError:
            ts.emit(ThoughtPhase.error, f"Spec '{spec_id}' not found")
            session.renderer.error_message(
                f"Spec '{spec_id}' not found. Run **/specs** to see available specs."
            )
            return
        except Exception as exc:
            ts.emit(ThoughtPhase.error, str(exc))
            session.renderer.error_message(f"Verification failed: {exc}")
            return

    session.renderer.agent_message(format_verification_report(report))


def _cmd_openclaw(session: ChatSession, _args: list[str]) -> None:
    """Show OpenClaw integration status."""
    try:
        from skills.openclaw_adapter import OpenClawSkillBridge

        bridge = OpenClawSkillBridge(session.repo_path)
        status = bridge.get_status()

        rows: list[tuple[str, str]] = [
            ("Enabled", "Yes" if status["enabled"] else "No"),
            ("Gateway URL", status["gateway_url"]),
            ("Is Available", "Yes" if status["is_available"] else "No"),
            ("Allow Skill Import", "Yes" if status["allow_skill_import"] else "No"),
            ("Allow External Execution", "Yes" if status["allow_external_execution"] else "No"),
            ("Require Approval for Writes", "Yes" if status["require_approval_for_external_writes"] else "No"),
        ]

        try:
            from config.config_loader import get_config

            cfg = get_config()
            rows.append(("Skill Name", cfg.openclaw.skill_name))
            rows.append(("Auto Register", str(cfg.openclaw.auto_register)))
        except Exception:
            pass

        session.renderer.key_value_table("OpenClaw Status", rows)
    except Exception as exc:
        session.renderer.error_message(f"OpenClaw status check failed: {exc}")


def _cmd_spec(session: ChatSession, args: list[str]) -> None:
    """Generate a structured spec from a goal.
    
    Usage:
        /spec <goal description>
        /spec --epic <complex goal>
        /spec --quick <simple goal>
    """
    import asyncio
    from orchestrator.spec_generator import SpecTier

    if not args:
        session.renderer.system_message(
            "Usage: /spec <goal description>\n\n"
            "Options:\n"
            "  --epic  — multi-phase complex feature\n"
            "  --quick — simple single-file change\n\n"
            "Example: /spec add OAuth2 login with Google and GitHub"
        )
        return

    # Determine tier
    tier = None
    if "--epic" in args:
        tier = SpecTier.epic
        args = [a for a in args if a != "--epic"]
    elif "--quick" in args:
        tier = SpecTier.quick
        args = [a for a in args if a != "--quick"]

    goal = " ".join(args)
    if not goal:
        session.renderer.error_message("Usage: /spec <goal description>")
        return

    from tui.thinking import ThoughtStream
    from tui.spec_commands import format_spec_summary, generate_spec_from_goal

    with ThoughtStream(session.renderer.console) as ts:
        ts.emit(ThoughtPhase.analyzing, "Auditing repo and gathering context")
        try:
            spec, spec_path = asyncio.run(
                generate_spec_from_goal(goal, session.repo_path, tier)
            )
            ts.emit(ThoughtPhase.complete, f"Generated {spec.id}")
        except Exception as exc:
            ts.emit(ThoughtPhase.error, str(exc))
            session.renderer.error_message(f"Spec generation failed: {exc}")
            return

    summary = format_spec_summary(spec)
    footer = (
        f"\n---\n"
        f"📁 Saved to `.clawsmith/specs/{spec.id}.md`\n"
        f"Run **/verify {spec.id}** after implementing to check your work."
    )

    session.renderer.agent_message(summary + footer)


def _cmd_verify(session: ChatSession, args: list[str]) -> None:
    """Verify implementation against a spec.
    
    Usage:
        /verify <spec-id>  — verify a specific spec
        /verify            — list available specs
    """
    import asyncio
    from tui.thinking import ThoughtStream
    from tui.spec_commands import format_verification_report, list_specs, verify_spec

    spec_id = args[0] if args else None

    with ThoughtStream(session.renderer.console) as ts:
        ts.emit(ThoughtPhase.analyzing, "Scanning working tree diff")

        try:
            if not spec_id:
                specs = list_specs(session.repo_path)
                if not specs:
                    ts.emit(ThoughtPhase.complete, "No specs found")
                    session.renderer.system_message(
                        "No specs found. Run **/spec <goal>** to generate one."
                    )
                    return

                lines = ["**Available specs:**\n"]
                for s in specs[:5]:
                    lines.append(
                        f"- `{s['id']}` — {s['goal'][:50]} "
                        f"(tier: {s['tier']}, {s['file_count']} files)"
                    )
                lines.append("\nRun **/verify <spec-id>** to verify a specific spec.")
                session.renderer.agent_message("\n".join(lines))
                return

            ts.emit(ThoughtPhase.analyzing, f"Verifying spec {spec_id}")
            report = asyncio.run(verify_spec(spec_id, session.repo_path))
            ts.emit(ThoughtPhase.complete, f"Verified: {report.score:.0%}")

        except FileNotFoundError as e:
            ts.emit(ThoughtPhase.error, str(e))
            session.renderer.error_message(f"Spec not found: {spec_id}")
            return
        except Exception as exc:
            ts.emit(ThoughtPhase.error, str(exc))
            session.renderer.error_message(f"Verification error: {exc}")
            return

    session.renderer.agent_message(format_verification_report(report))


def _cmd_specs(session: ChatSession, _args: list[str]) -> None:
    """List all available specs."""
    from tui.spec_commands import list_specs

    specs = list_specs(session.repo_path)

    if not specs:
        session.renderer.system_message(
            "No specs found. Run **/spec <goal>** to generate one."
        )
        return

    columns = [
        ("ID", "brand"),
        ("Goal", ""),
        ("Tier", ""),
        ("Files", ""),
        ("Phases", ""),
    ]
    rows = [
        [
            s["id"][:8],
            s["goal"][:40],
            s["tier"],
            str(s["file_count"]),
            str(s["phase_count"]),
        ]
        for s in specs
    ]
    session.renderer.ranked_table(f"{len(specs)} Spec(s)", columns, rows)


def _get_session_runtime(session: ChatSession) -> "ChatRuntime":
    """Get or create the ChatRuntime attached to this session."""
    from orchestrator.chat_runtime import ChatRuntime

    if not hasattr(session, "_runtime") or session._runtime is None:
        session._runtime = ChatRuntime(
            repo_path=session.repo_path,
            interactive=True,
        )
        session._runtime.initialize()
    return session._runtime
