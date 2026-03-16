"""Typed models for the first-class skill subsystem."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SourceType(str, Enum):
    manual = "manual"
    generated = "generated"
    dependency_derived = "dependency_derived"
    repo_derived = "repo_derived"
    openclaw_imported = "openclaw_imported"


class SkillDefinition(BaseModel):
    """Core schema for a ClawSmith skill."""

    id: str
    name: str
    description: str
    version: str = "1.0.0"
    source_type: SourceType = SourceType.manual
    triggers: list[str] = Field(default_factory=list)
    applicable_stacks: list[str] = Field(default_factory=list)
    required_context: list[str] = Field(default_factory=list)
    preferred_tools: list[str] = Field(default_factory=list)
    allowed_scope: list[str] = Field(default_factory=list)
    execution_strategy: str = "llm_guided"
    constraints: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    confidence: float = 1.0
    enabled: bool = True
    explainability: str = ""
    tags: list[str] = Field(default_factory=list)
    inferred_commands: list[str] = Field(default_factory=list)
    inferred_file_targets: list[str] = Field(default_factory=list)
    generation_evidence: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())


class SkillScore(BaseModel):
    """Scoring result for a skill against a particular task."""

    skill_id: str
    skill_name: str
    score: float = 0.0
    relevance_reason: str = ""
    trigger_matches: list[str] = Field(default_factory=list)
    stack_matches: list[str] = Field(default_factory=list)
    keyword_matches: list[str] = Field(default_factory=list)


class SkillSelectionResult(BaseModel):
    """Explains which skills were selected and why."""

    task_description: str
    scored_skills: list[SkillScore] = Field(default_factory=list)
    selected_skills: list[str] = Field(default_factory=list)
    explanation: str = ""
