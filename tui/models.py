"""Data models for the TUI session."""

from __future__ import annotations

import time
from enum import StrEnum

from pydantic import BaseModel, Field


class MessageRole(StrEnum):
    user = "user"
    agent = "agent"
    system = "system"


class ThoughtPhase(StrEnum):
    analyzing = "analyzing"
    detecting = "detecting"
    routing = "routing"
    planning = "planning"
    executing = "executing"
    tool_call = "tool_call"
    complete = "complete"
    error = "error"


class ThoughtEvent(BaseModel):
    """A single step in the agent's visible reasoning chain."""

    phase: ThoughtPhase
    step: str
    detail: str = ""
    timestamp: float = Field(default_factory=time.time)


class ChatMessage(BaseModel):
    """One turn in the conversation."""

    role: MessageRole
    content: str
    timestamp: float = Field(default_factory=time.time)
    thoughts: list[ThoughtEvent] = Field(default_factory=list)
