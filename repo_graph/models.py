"""Data models for the cross-repo workspace graph."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RepoNode(BaseModel):
    path: str
    name: str
    role: str = ""
    languages: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    manifest_files: list[str] = Field(default_factory=list)
    branch: str = ""
    remote_url: str = ""
    description: str = ""


class DependencyEdge(BaseModel):
    source: str
    target: str
    dependency_type: str
    version_constraint: str = ""
    shared_apis: list[str] = Field(default_factory=list)


class WorkspaceGraph(BaseModel):
    repos: list[RepoNode] = Field(default_factory=list)
    edges: list[DependencyEdge] = Field(default_factory=list)
    root_workspace: str = ""
    build_order: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
