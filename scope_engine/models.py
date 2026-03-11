from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class ScopeLevel(StrEnum):
    in_scope = "in_scope"
    conditional = "conditional"
    out_of_scope = "out_of_scope"


class RepoScope(BaseModel):
    repo_name: str
    repo_path: str
    level: ScopeLevel = ScopeLevel.in_scope
    read_only: bool = False
    allow_version_bumps: bool = False
    allow_coordinated_changes: bool = False
    restricted_paths: list[str] = Field(default_factory=list)
    notes: str = ""


class ScopeContract(BaseModel):
    task_id: str = ""
    primary_repo: str = ""
    repos: list[RepoScope] = Field(default_factory=list)
    allow_multi_repo_changes: bool = False
    created_at: str = ""
    notes: str = ""
