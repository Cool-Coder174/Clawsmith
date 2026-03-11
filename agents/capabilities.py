"""Machine-readable capability registry for agent CLIs."""

from __future__ import annotations

from enum import StrEnum


class AgentCapability(StrEnum):
    interactive_chat = "interactive_chat"
    headless_prompt = "headless_prompt"
    structured_output = "structured_output"
    model_switching = "model_switching"
    mcp_client = "mcp_client"
    acp_client = "acp_client"
    shell_execution = "shell_execution"
    file_editing = "file_editing"
    sandbox_mode = "sandbox_mode"
    approval_mode = "approval_mode"
    resume_session = "resume_session"
    json_output = "json_output"
