"""Visual identity, color scheme, and logo for the ClawSmith TUI."""

from __future__ import annotations

import sys

from rich.theme import Theme


def _can_unicode() -> bool:
    """Return *True* if the stdout encoding can handle box-drawing chars."""
    enc = getattr(sys.stdout, "encoding", "") or ""
    return enc.lower().replace("-", "") in {
        "utf8", "utf16", "utf32", "utf_8", "utf_16", "utf_32",
    }

VERSION = "0.1.0"
AGENT_NAME = "ClawSmith"

# ---------------------------------------------------------------------------
# Logo variants — selected at runtime based on terminal width
# ---------------------------------------------------------------------------

LOGO_FULL = (
    "   _____ _                "
    "_____           _ _   _   \n"
    "  / ____| |              "
    "/ ____|         (_) | | |  \n"
    " | |    | | __ ___      _"
    "| (___  _ __ ___  _| |_| |__ \n"
    " | |    | |/ _` \\ \\ /\\ / /"
    "\\___ \\| '_ ` _ \\| | __| '_ \\\n"
    " | |____| | (_| |\\ V  V /"
    " ____) | | | | | | | |_| | | |\n"
    "  \\_____|_|\\__,_| \\_/\\_/ "
    "|_____/|_| |_| |_|_|\\__|_| |_|"
)

LOGO_COMPACT = (
    "  ╔═╗╦  ╔═╗╦ ╦╔═╗╔╦╗╦╔╦╗╦ ╦\n"
    "  ║  ║  ╠═╣║║║╚═╗║║║║ ║ ╠═╣\n"
    "  ╚═╝╩═╝╩ ╩╚╩╝╚═╝╩ ╩╩ ╩ ╩ ╩"
)

LOGO_MINI = " ▸ ClawSmith"

TAGLINE = "Local-first AI orchestration for coding agents"

# ---------------------------------------------------------------------------
# Rich theme
# ---------------------------------------------------------------------------

CLAWSMITH_THEME = Theme(
    {
        "brand": "bold cyan",
        "brand.sub": "dim cyan",
        "user.name": "bold green",
        "user.text": "white",
        "agent.name": "bold cyan",
        "agent.text": "white",
        "phase.analyzing": "dim cyan",
        "phase.detecting": "dim cyan",
        "phase.routing": "dim yellow",
        "phase.planning": "dim green",
        "phase.executing": "bold yellow",
        "phase.tool_call": "bright_magenta",
        "phase.complete": "bold green",
        "phase.error": "bold red",
        # Agent lifecycle phases
        "phase.deployed": "bold cyan",
        "phase.decomposing": "bold bright_cyan",
        "phase.queued": "dim cyan",
        "phase.verifying": "bold magenta",
        "phase.retrying": "bold yellow",
        "phase.verify_build": "magenta",
        "phase.verify_compile": "magenta",
        "phase.verify_fix": "yellow",
        "phase.verify_conflicts": "yellow",
        "phase.failed": "bold red",
        "success": "bold green",
        "error": "bold red",
        "warning": "yellow",
        "muted": "dim",
        "separator": "dim cyan",
        "prompt.symbol": "bold bright_white",
        "hint": "dim italic",
        # Status bar
        "status.phase": "bold cyan",
        "status.active": "bold green",
        "status.pending": "dim",
        "status.done": "green",
    }
)

# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------

_U = _can_unicode()

SYM_USER = "*" if not _U else "●"
SYM_AGENT = "*" if not _U else "●"
SYM_PROMPT = ">"
SYM_BRANCH = "|-" if not _U else "├─"
SYM_BRANCH_END = "`-" if not _U else "└─"
SYM_PIPE = "| " if not _U else "│ "
SYM_BULLET = "-" if not _U else "•"
SYM_CHECK = "+" if not _U else "✓"
SYM_CROSS = "x" if not _U else "✗"
SYM_ARROW = ">" if not _U else "▸"
SYM_SEPARATOR = "-" if not _U else "─"

# ---------------------------------------------------------------------------
# Phase display metadata
# ---------------------------------------------------------------------------

PHASE_LABELS: dict[str, str] = {
    "analyzing": "Analyzing",
    "detecting": "Detecting",
    "routing": "Routing",
    "planning": "Planning",
    "executing": "Executing",
    "tool_call": "Tool",
    "complete": "Done",
    "error": "Error",
    # Agent lifecycle
    "deployed": "Agent Deployed",
    "decomposing": "Decomposing",
    "queued": "Queued",
    "verifying": "Verifying",
    "retrying": "Retrying",
    "verify_build": "Build",
    "verify_compile": "Compile Check",
    "verify_fix": "Fix Errors",
    "verify_conflicts": "Resolve Conflicts",
    "failed": "Failed",
}

PHASE_ICONS: dict[str, str] = {
    "analyzing": "~" if not _U else "◐",
    "detecting": "~" if not _U else "◑",
    "routing": "~" if not _U else "◒",
    "planning": "~" if not _U else "◓",
    "executing": ">" if not _U else "▸",
    "tool_call": "*" if not _U else "⚡",
    "complete": "+" if not _U else "✓",
    "error": "x" if not _U else "✗",
    # Agent lifecycle
    "deployed": ">" if not _U else "🚀",
    "decomposing": "~" if not _U else "🔬",
    "queued": "~" if not _U else "📋",
    "verifying": "~" if not _U else "🔍",
    "retrying": "!" if not _U else "🔄",
    "verify_build": "~" if not _U else "🔨",
    "verify_compile": "~" if not _U else "⚙️",
    "verify_fix": "!" if not _U else "🔧",
    "verify_conflicts": "!" if not _U else "🔀",
    "failed": "x" if not _U else "✗",
}

# Lifecycle progress bar segments for the status strip (single-pipeline)
LIFECYCLE_PHASES: list[tuple[str, str]] = [
    ("deployed", "Deploy"),
    ("planning", "Plan"),
    ("executing", "Execute"),
    ("verifying", "Verify"),
    ("complete", "Complete"),
]

# YOLO mode lifecycle strip (multi-phase)
YOLO_LIFECYCLE_PHASES: list[tuple[str, str]] = [
    ("deployed", "Deploy"),
    ("decomposing", "Decompose"),
    ("queued", "Queue"),
    ("executing", "Execute"),
    ("verifying", "Verify"),
    ("complete", "Complete"),
]
