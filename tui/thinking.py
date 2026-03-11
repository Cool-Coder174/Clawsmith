"""Live thinking stream — shows agent reasoning in real time."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.console import Group
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from tui.models import ThoughtEvent, ThoughtPhase
from tui.theme import (
    PHASE_ICONS,
    PHASE_LABELS,
    SYM_BRANCH,
    SYM_BRANCH_END,
)

if TYPE_CHECKING:
    from rich.console import Console


class ThoughtStream:
    """Context manager that renders a live thinking tree.

    Usage::

        with ThoughtStream(console) as ts:
            ts.emit("analyzing", "Reading repository layout")
            ts.emit("routing", "Selected local_code tier")
        # final static tree is printed on exit
    """

    def __init__(self, console: Console) -> None:
        self._console = console
        self._events: list[ThoughtEvent] = []
        self._live: Live | None = None
        self._start = time.time()

    # -- context manager --------------------------------------------------

    def __enter__(self) -> ThoughtStream:
        self._live = Live(
            self._render_live(),
            console=self._console,
            refresh_per_second=10,
            transient=True,
        )
        self._live.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        if self._live is not None:
            self._live.__exit__(exc_type, exc_val, exc_tb)
        self._console.print(self._render_static())

    # -- public API -------------------------------------------------------

    def emit(
        self,
        phase: ThoughtPhase | str,
        step: str,
        detail: str = "",
    ) -> ThoughtEvent:
        """Add a thinking event and refresh the live display."""
        if isinstance(phase, str):
            phase = ThoughtPhase(phase)
        event = ThoughtEvent(phase=phase, step=step, detail=detail)
        self._events.append(event)
        if self._live is not None:
            self._live.update(self._render_live())
        return event

    @property
    def events(self) -> list[ThoughtEvent]:
        return list(self._events)

    @property
    def elapsed(self) -> float:
        return time.time() - self._start

    # -- rendering --------------------------------------------------------

    def _render_live(self) -> Group:
        """Build the live tree with a spinner on the latest step."""
        parts: list[Text | Spinner] = []

        for i, ev in enumerate(self._events):
            is_last = i == len(self._events) - 1
            prefix = SYM_BRANCH_END if is_last else SYM_BRANCH
            style = f"phase.{ev.phase.value}"

            if is_last:
                icon = PHASE_ICONS.get(ev.phase.value, "◐")
                line = Text.assemble(
                    ("  ", ""),
                    (f"{prefix} ", style),
                    (f"{icon} ", style),
                    (ev.step, style),
                )
                if ev.detail:
                    line.append(f"  {ev.detail}", style="muted")
                parts.append(line)
            else:
                line = Text.assemble(
                    ("  ", ""),
                    (f"{prefix} ", "muted"),
                    (ev.step, "muted"),
                )
                parts.append(line)

        if not parts:
            spinner_text = Text.assemble(
                ("  ", ""),
                ("◐ ", "phase.analyzing"),
                ("Thinking...", "phase.analyzing"),
            )
            parts.append(spinner_text)

        return Group(*parts)

    def _render_static(self) -> Group:
        """Build the final static tree after thinking completes."""
        parts: list[Text] = []
        for i, ev in enumerate(self._events):
            is_last = i == len(self._events) - 1
            prefix = SYM_BRANCH_END if is_last else SYM_BRANCH
            style = f"phase.{ev.phase.value}"
            phase_label = PHASE_LABELS.get(ev.phase.value, "")

            line = Text.assemble(
                ("  ", ""),
                (f"{prefix} ", "muted"),
                (f"{phase_label}: " if phase_label else "", "muted"),
                (ev.step, style),
            )
            if ev.detail:
                line.append(f"  ({ev.detail})", style="muted")
            parts.append(line)

        elapsed = Text.assemble(
            ("  ", ""),
            (f"  {self.elapsed:.1f}s", "muted"),
        )
        parts.append(elapsed)
        return Group(*parts)
