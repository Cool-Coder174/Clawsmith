"""Explicit session state for the chat-first runtime."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from memory_skill.retriever import MemoryEntry, RetrievalResult
from skills.models import SkillDefinition, SkillSelectionResult


@dataclass
class SessionState:
    """Holds the full, explicit state of a chat runtime session.

    All state is stored here rather than scattered across objects.
    This makes the session testable, inspectable, and serializable.
    """

    repo_path: Path = field(default_factory=lambda: Path(".").resolve())
    workspace_root: Path = field(default_factory=lambda: Path(".").resolve())

    history: list[dict[str, str]] = field(default_factory=list)

    loaded_skills: list[SkillDefinition] = field(default_factory=list)
    skill_selection: SkillSelectionResult | None = None
    repo_stacks: list[str] = field(default_factory=list)

    retrieved_memories: RetrievalResult | None = None

    current_plan: dict[str, Any] | None = None
    last_routing_decision: dict[str, Any] | None = None
    last_agent_status: dict[str, Any] | None = None
    last_execution_result: dict[str, Any] | None = None

    dry_run: bool = False
    safe_mode: bool = True
    interactive: bool = True

    model_name: str = ""
    agent_id: str = ""

    turn_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def add_user_message(self, content: str) -> None:
        self.history.append({"role": "user", "content": content})
        self.turn_count += 1

    def add_agent_message(self, content: str) -> None:
        self.history.append({"role": "assistant", "content": content})

    def get_explainability_summary(self) -> dict[str, Any]:
        """Return a summary of decisions made in the current session."""
        summary: dict[str, Any] = {
            "turn_count": self.turn_count,
            "skills_loaded": len(self.loaded_skills),
            "dry_run": self.dry_run,
            "safe_mode": self.safe_mode,
            "model": self.model_name,
            "agent": self.agent_id,
        }
        if self.skill_selection:
            summary["skill_selection"] = {
                "selected": self.skill_selection.selected_skills,
                "explanation": self.skill_selection.explanation,
            }
        if self.retrieved_memories:
            summary["memory_retrieval"] = {
                "entries": len(self.retrieved_memories.entries),
                "total_candidates": self.retrieved_memories.total_candidates,
                "explanation": self.retrieved_memories.explanation,
            }
        if self.last_routing_decision:
            summary["routing"] = self.last_routing_decision
        return summary
