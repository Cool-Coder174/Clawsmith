from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class MutationType(StrEnum):
    model_routing = "model_routing"
    system_prompt = "system_prompt"
    skill_selection = "skill_selection"
    wrapper_script = "wrapper_script"
    adapter_config = "adapter_config"
    repo_defaults = "repo_defaults"
    context_packing = "context_packing"
    task_routing = "task_routing"
    tool_template = "tool_template"
    plugin_manifest = "plugin_manifest"
    model_preset = "model_preset"


class MutationStatus(StrEnum):
    proposed = "proposed"
    staged = "staged"
    validated = "validated"
    approved = "approved"
    applied = "applied"
    rejected = "rejected"
    rolled_back = "rolled_back"
    failed = "failed"


class MutationProposal(BaseModel):
    id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex[:12])
    mutation_type: MutationType
    requested_by: str = "openclaw"
    reason: str = ""
    target_scope: str = ""
    affected_files: list[str] = Field(default_factory=list)
    change_summary: str = ""
    diff_preview: str = ""
    before_snapshot: dict[str, str] = Field(default_factory=dict)
    after_snapshot: dict[str, str] = Field(default_factory=dict)
    status: MutationStatus = MutationStatus.proposed
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    validated_at: str | None = None
    approved_at: str | None = None
    applied_at: str | None = None
    rolled_back_at: str | None = None
    validation_result: str | None = None
    rollback_instructions: str = ""
    error: str | None = None


class MutationPolicy(BaseModel):
    self_mutation_enabled: bool = False
    allowed_types: list[MutationType] = Field(
        default_factory=lambda: list(MutationType)
    )
    restricted_types: list[MutationType] = Field(default_factory=list)
    require_approval: bool = True
    require_validation: bool = True
    require_staging: bool = True
    max_affected_files: int = 20
    restricted_paths: list[str] = Field(
        default_factory=lambda: [
            "*.exe",
            "*.dll",
            "*.so",
            "*.dylib",
            ".env",
            "credentials.*",
            ".git/*",
            "~/*",
        ]
    )
    notes: str = ""


class AuditEntry(BaseModel):
    proposal_id: str
    action: str
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())
    actor: str = ""
    details: str = ""
