"""Live thinking stream — shows agent reasoning and lifecycle status in real time."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from rich.console import Group
from rich.live import Live
from rich.spinner import Spinner
from rich.text import Text

from tui.models import ThoughtEvent, ThoughtPhase
from tui.theme import (
    LIFECYCLE_PHASES,
    PHASE_ICONS,
    PHASE_LABELS,
    SYM_ARROW,
    SYM_BRANCH,
    SYM_BRANCH_END,
    SYM_CHECK,
    _U,
)

if TYPE_CHECKING:
    from rich.console import Console

    from orchestrator.agent_status import AgentPhase, StatusEvent, StatusTracker

# Map AgentPhase values to their position in the lifecycle strip
_PHASE_STRIP_INDEX: dict[str, int] = {
    phase_key: i for i, (phase_key, _) in enumerate(LIFECYCLE_PHASES)
}


def _render_lifecycle_strip(
    active_phase: str | None = None,
    is_failed: bool = False,
) -> Text:
    """Build a horizontal progress strip:  [Deploy] → [Plan] → [Execute] → [Verify] → [Complete]"""
    parts: list[tuple[str, str]] = []
    active_idx = _PHASE_STRIP_INDEX.get(active_phase or "", -1)

    for i, (phase_key, label) in enumerate(LIFECYCLE_PHASES):
        if i > 0:
            parts.append((" → " if _U else " > ", "muted"))

        if is_failed and phase_key == active_phase:
            parts.append((f"[{label}]", "phase.failed"))
        elif i < active_idx:
            check = "✓" if _U else "+"
            parts.append((f"{check} {label}", "phase.complete"))
        elif i == active_idx:
            parts.append((f"● {label}", f"phase.{phase_key}"))
        else:
            parts.append((f"○ {label}", "muted"))

    line = Text("  ")
    for content, style in parts:
        line.append(content, style=style)
    return line


class ThoughtStream:
    """Context manager that renders a live thinking tree with lifecycle status.

    Usage::

        with ThoughtStream(console) as ts:
            ts.emit("analyzing", "Reading repository layout")
            ts.emit("routing", "Selected local_code tier")
        # final static tree is printed on exit

    With lifecycle tracking::

        tracker = StatusTracker()
        with ThoughtStream(console, tracker=tracker) as ts:
            # pipeline emits events via tracker, ThoughtStream renders them
            ...
    """

    def __init__(
        self,
        console: Console,
        tracker: StatusTracker | None = None,
    ) -> None:
        self._console = console
        self._events: list[ThoughtEvent] = []
        self._live: Live | None = None
        self._start = time.time()
        self._tracker = tracker
        self._status_events: list[StatusEvent] = []

        if tracker is not None:
            tracker.on_status(self._on_tracker_event)

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

    # -- tracker callback -------------------------------------------------

    def _on_tracker_event(self, event: StatusEvent) -> None:
        """Bridge StatusTracker events into the ThoughtStream display."""
        self._status_events.append(event)

        phase_map: dict[str, ThoughtPhase] = {
            "deployed": ThoughtPhase.deployed,
            "decomposing": ThoughtPhase.decomposing,
            "planning": ThoughtPhase.planning,
            "queued": ThoughtPhase.queued,
            "executing": ThoughtPhase.executing,
            "verifying": ThoughtPhase.verifying,
            "retrying": ThoughtPhase.retrying,
            "complete": ThoughtPhase.complete,
            "failed": ThoughtPhase.failed,
        }

        verify_phase_map: dict[str, ThoughtPhase] = {
            "build": ThoughtPhase.verify_build,
            "compile_check": ThoughtPhase.verify_compile,
            "fix_errors": ThoughtPhase.verify_fix,
            "fix_conflicts": ThoughtPhase.verify_conflicts,
        }

        thought_phase = phase_map.get(event.phase.value, ThoughtPhase.analyzing)
        if event.verify_stage and event.verify_stage.value in verify_phase_map:
            thought_phase = verify_phase_map[event.verify_stage.value]

        thought = ThoughtEvent(
            phase=thought_phase,
            step=event.step,
            detail=event.detail,
        )
        self._events.append(thought)

        if self._live is not None:
            self._live.update(self._render_live())

    # -- rendering --------------------------------------------------------

    def _get_active_phase(self) -> tuple[str | None, bool]:
        """Determine the current lifecycle phase from tracker or events."""
        if self._tracker is not None:
            return self._tracker.phase.value, self._tracker.phase.value == "failed"

        if not self._events:
            return None, False
        last = self._events[-1]
        return last.phase.value, last.phase.value in ("error", "failed")

    def _render_live(self) -> Group:
        """Build the live tree with lifecycle strip + spinner on the latest step."""
        parts: list[Text | Spinner] = []

        active_phase, is_failed = self._get_active_phase()
        if active_phase and active_phase != "pending":
            parts.append(Text(""))
            parts.append(_render_lifecycle_strip(active_phase, is_failed))
            parts.append(Text(""))

        visible = self._events[-8:] if len(self._events) > 8 else self._events
        if len(self._events) > 8:
            parts.append(Text(f"  ... {len(self._events) - 8} earlier steps", style="muted"))

        for i, ev in enumerate(visible):
            is_last = i == len(visible) - 1
            prefix = SYM_BRANCH_END if is_last else SYM_BRANCH
            style = f"phase.{ev.phase.value}"

            if is_last:
                icon = PHASE_ICONS.get(ev.phase.value, "◐" if _U else "~")
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

        if not self._events:
            spinner_text = Text.assemble(
                ("  ", ""),
                ("◐ " if _U else "~ ", "phase.analyzing"),
                ("Thinking...", "phase.analyzing"),
            )
            parts.append(spinner_text)

        return Group(*parts)

    def _render_static(self) -> Group:
        """Build the final static tree after thinking completes."""
        parts: list[Text] = []

        active_phase, is_failed = self._get_active_phase()
        if active_phase and active_phase != "pending":
            parts.append(Text(""))
            parts.append(_render_lifecycle_strip(active_phase, is_failed))
            parts.append(Text(""))

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
