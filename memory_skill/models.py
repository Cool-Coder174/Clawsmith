"""Data models for the persistent memory subsystem."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StackNote(BaseModel):
    key: str
    value: str
    updated_at: str = ""


class CodingConvention(BaseModel):
    language: str
    convention: str
    source: str = ""


class InstalledModel(BaseModel):
    model_id: str
    display_name: str
    runtime: str
    path: str
    installed_at: str = ""


class InstalledRuntime(BaseModel):
    name: str
    version: str
    path: str


class MutationPermission(BaseModel):
    scope: str
    allowed: bool
    requires_approval: bool = True
    notes: str = ""


class RepoEntry(BaseModel):
    path: str
    name: str
    role: str = ""
    languages: list[str] = Field(default_factory=list)
    in_scope: bool = True
    read_only: bool = False


class PreferencesData(BaseModel):
    preferred_local_models: list[str] = Field(default_factory=list)
    preferred_remote_models: list[str] = Field(default_factory=list)
    preferred_shells: list[str] = Field(default_factory=list)
    preferred_editors: list[str] = Field(default_factory=list)
    default_model_routing: str = "auto"
    default_task_execution: str = "local"
    coding_conventions: list[CodingConvention] = Field(default_factory=list)
    stack_notes: list[StackNote] = Field(default_factory=list)
    build_commands: dict[str, list[str]] = Field(default_factory=dict)
    test_commands: dict[str, list[str]] = Field(default_factory=dict)
    last_known_working_setups: dict[str, str] = Field(default_factory=dict)


class ArchitectureData(BaseModel):
    hardware_tier: str = ""
    os_name: str = ""
    os_version: str = ""
    cpu_summary: str = ""
    ram_gb: float = 0.0
    gpu_summary: str = ""
    vram_gb: float = 0.0
    installed_models: list[InstalledModel] = Field(default_factory=list)
    installed_runtimes: list[InstalledRuntime] = Field(default_factory=list)
    approved_agent_clis: list[str] = Field(default_factory=list)
    repos: list[RepoEntry] = Field(default_factory=list)
    mutation_permissions: list[MutationPermission] = Field(default_factory=list)


class ToolingProfile(BaseModel):
    developer_tools: dict[str, str] = Field(default_factory=dict)
    ai_tooling: dict[str, str] = Field(default_factory=dict)
    package_managers: dict[str, str] = Field(default_factory=dict)
    inference_runtimes: dict[str, str] = Field(default_factory=dict)
