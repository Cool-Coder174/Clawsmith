"""Rich-based rendering engine for the ClawSmith TUI."""

from __future__ import annotations

from rich.console import Console
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from tui.theme import (
    _U,
    AGENT_NAME,
    CLAWSMITH_THEME,
    LOGO_COMPACT,
    LOGO_FULL,
    LOGO_MINI,
    SYM_AGENT,
    SYM_PROMPT,
    SYM_SEPARATOR,
    SYM_USER,
    TAGLINE,
    VERSION,
)


def _looks_like_markdown(text: str) -> bool:
    """Return True if *text* likely contains Markdown formatting."""
    if "```" in text or "**" in text or "| " in text:
        return True
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith(("# ", "## ", "### ", "- ", "* ", "> ")):
            return True
        if stripped and stripped[0].isdigit() and ". " in stripped[:5]:
            return True
    return False


class Renderer:
    """All visual output for the TUI session."""

    def __init__(self, console: Console | None = None) -> None:
        self.console = console or Console(theme=CLAWSMITH_THEME)

    # -- branding ---------------------------------------------------------

    def logo(self) -> None:
        """Print the startup logo sized to the terminal."""
        width = self.console.width
        self.console.print()

        if width >= 62:
            logo_text = Text(LOGO_FULL, style="brand")
            self.console.print(logo_text)
        elif width >= 35 and _U:
            logo_text = Text(LOGO_COMPACT, style="brand")
            self.console.print(logo_text)
        else:
            self.console.print(LOGO_MINI, style="brand")

    def welcome(self) -> None:
        """Print the welcome blurb below the logo."""
        self.console.print(f"  {TAGLINE}", style="muted")
        self.console.print(
            f"  v{VERSION}  |  Type [bold]/help[/bold] for commands",
            style="muted",
        )
        self.separator()

    def farewell(self) -> None:
        self.console.print()
        self.separator()
        self.console.print(
            f"  {AGENT_NAME} session ended. Goodbye!",
            style="muted",
        )
        self.console.print()

    # -- layout -----------------------------------------------------------

    def separator(self) -> None:
        width = min(self.console.width, 72)
        self.console.print(SYM_SEPARATOR * width, style="separator")

    def turn_separator(self) -> None:
        """Subtle divider between chat turns — lighter than the structural separator."""
        width = min(self.console.width - 4, 44)
        self.console.print(f"  {SYM_SEPARATOR * width}", style="dim")

    def blank(self) -> None:
        self.console.print()

    # -- messages ---------------------------------------------------------

    def user_message(self, text: str) -> None:
        """Render a user turn."""
        self.console.print()
        label = Text.assemble(
            ("  ", ""),
            (f"{SYM_USER} ", "user.name"),
            ("You", "user.name"),
        )
        self.console.print(label)
        for line in text.splitlines():
            self.console.print(f"    {line}", style="user.text")

    def agent_message(self, text: str) -> None:
        """Render an agent response.

        If *text* contains markdown formatting, render via Rich Markdown;
        otherwise print plain styled text.  Markdown output is left-padded
        to align with the 4-space indent used for plain text.
        """
        self.console.print()
        label = Text.assemble(
            ("  ", ""),
            (f"{SYM_AGENT} ", "agent.name"),
            (AGENT_NAME, "agent.name"),
        )
        self.console.print(label)

        if _looks_like_markdown(text):
            md = Markdown(text, code_theme="monokai")
            self.console.print(
                Padding(md, (0, 0, 0, 4)),
                width=min(self.console.width, 92),
            )
        else:
            for line in text.splitlines():
                self.console.print(f"    {line}", style="agent.text")

    def system_message(self, text: str) -> None:
        self.console.print(f"  {text}", style="muted")

    def error_message(self, text: str) -> None:
        self.console.print(f"  {text}", style="error")

    def success_message(self, text: str) -> None:
        self.console.print(f"  {text}", style="success")

    # -- tables -----------------------------------------------------------

    def key_value_table(
        self,
        title: str,
        rows: list[tuple[str, str]],
    ) -> None:
        """Print a two-column key-value table."""
        table = Table(
            title=title,
            show_lines=True,
            title_style="brand",
            border_style="dim",
            padding=(0, 1),
        )
        table.add_column("Property", style="brand", min_width=14)
        table.add_column("Value")
        for key, val in rows:
            table.add_row(key, val)
        self.console.print(table)

    def ranked_table(
        self,
        title: str,
        columns: list[tuple[str, str]],
        rows: list[list[str]],
    ) -> None:
        """Print a multi-column ranked table.

        *columns* is ``[(header, style), ...]``.
        """
        table = Table(
            title=title,
            show_lines=True,
            title_style="brand",
            border_style="dim",
            padding=(0, 1),
        )
        for header, style in columns:
            table.add_column(header, style=style)
        for row in rows:
            table.add_row(*row)
        self.console.print(table)

    # -- panels -----------------------------------------------------------

    def info_panel(self, body: str, title: str = "") -> None:
        self.console.print(
            Panel(
                body,
                title=title or AGENT_NAME,
                border_style="brand",
                expand=False,
                padding=(1, 2),
            )
        )

    # -- prompt -----------------------------------------------------------

    def prompt(self) -> str:
        """Show the input prompt and return user text."""
        self.console.print()
        return self.console.input(
            Text.assemble(
                (f"{SYM_PROMPT} ", "prompt.symbol"),
            )
        )
