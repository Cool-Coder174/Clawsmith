"""Memory sync — reconciles on-disk memory with the current project state.

Runs during ``clawsmith memory sync`` to merge newly detected information
(hardware changes, new dependencies) into the persistent memory store.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.logging_setup import get_logger

from .models import (
    ArchitectureData,
    InstalledModel,
    InstalledRuntime,
    PreferencesData,
    RepoEntry,
    ToolingProfile,
)
from .reader import MemoryReader
from .writer import MemoryWriter

log = get_logger("memory.sync")


class MemorySync:
    """Orchestrates syncing discovery results into persisted memory files."""

    def __init__(self, workspace_root: Path) -> None:
        self.root = workspace_root
        self.writer = MemoryWriter(workspace_root)
        self.reader = MemoryReader(workspace_root)

    # ------------------------------------------------------------------
    # Sync from a MachineProfile (or any duck-typed equivalent)
    # ------------------------------------------------------------------

    def sync_from_profile(self, profile: Any) -> None:
        """Update architecture.md, architecture.json, and tooling-profile.json
        from a MachineProfile-like object.

        The *profile* is expected to expose attributes compatible with
        ``ArchitectureData`` and ``ToolingProfile`` fields.  Missing
        attributes are silently skipped so the caller can pass partially
        populated objects.
        """
        arch = _extract_architecture(profile)
        self.writer.write_architecture(arch)
        self._write_sidecar("architecture.json", arch.model_dump())

        tooling = _extract_tooling(profile)
        self.writer.write_tooling_profile(tooling)

        log.info("Synced architecture + tooling from profile")

    # ------------------------------------------------------------------
    # Sync from repo graph
    # ------------------------------------------------------------------

    def sync_from_repo_graph(
        self,
        repos: list[RepoEntry],
        edges: list[dict],
    ) -> None:
        self.writer.write_repo_graph(repos, edges)
        log.info("Synced repo graph (%d repos, %d edges)", len(repos), len(edges))

    # ------------------------------------------------------------------
    # Sync preferences
    # ------------------------------------------------------------------

    def sync_preferences(self, prefs: PreferencesData) -> None:
        self.writer.write_preferences(prefs)
        self._write_sidecar("preferences.json", prefs.model_dump())
        log.info("Synced preferences")

    # ------------------------------------------------------------------
    # Full sync
    # ------------------------------------------------------------------

    def full_sync(
        self,
        profile: Any | None = None,
        repos: list[RepoEntry] | None = None,
        edges: list[dict] | None = None,
        prefs: PreferencesData | None = None,
    ) -> list[Path]:
        """Run a full sync of all memory files. Returns list of files written."""
        written: list[Path] = []

        if profile is not None:
            arch = _extract_architecture(profile)
            written.append(self.writer.write_architecture(arch))
            written.append(self._write_sidecar("architecture.json", arch.model_dump()))

            tooling = _extract_tooling(profile)
            written.append(self.writer.write_tooling_profile(tooling))

        if repos is not None:
            written.append(
                self.writer.write_repo_graph(repos, edges or [])
            )

        if prefs is not None:
            written.append(self.writer.write_preferences(prefs))
            written.append(
                self._write_sidecar("preferences.json", prefs.model_dump())
            )

        summary = _build_summary(
            arch=_extract_architecture(profile) if profile else None,
            prefs=prefs,
        )
        if summary:
            written.append(self.writer.write_memory_md(summary))

        log.info("Full sync complete — wrote %d files", len(written))
        return written

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _write_sidecar(self, name: str, data: dict) -> Path:
        self.writer.ensure_dirs()
        path = self.writer.clawsmith_dir / name
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        log.debug("Wrote sidecar -> %s", path)
        return path


# ======================================================================
# Extraction helpers — pull structured data from a MachineProfile-like
# object without importing the (potentially not-yet-created) type.
# ======================================================================


def _getattr_safe(obj: Any, name: str, default: Any = "") -> Any:
    return getattr(obj, name, default) if obj is not None else default


def _extract_architecture(profile: Any) -> ArchitectureData:
    installed_models: list[InstalledModel] = []
    for m in _getattr_safe(profile, "installed_models", []):
        if isinstance(m, InstalledModel):
            installed_models.append(m)
        elif isinstance(m, dict):
            installed_models.append(InstalledModel.model_validate(m))

    installed_runtimes: list[InstalledRuntime] = []
    for r in _getattr_safe(profile, "installed_runtimes", []):
        if isinstance(r, InstalledRuntime):
            installed_runtimes.append(r)
        elif isinstance(r, dict):
            installed_runtimes.append(InstalledRuntime.model_validate(r))

    repos: list[RepoEntry] = []
    for rp in _getattr_safe(profile, "repos", []):
        if isinstance(rp, RepoEntry):
            repos.append(rp)
        elif isinstance(rp, dict):
            repos.append(RepoEntry.model_validate(rp))

    return ArchitectureData(
        hardware_tier=str(_getattr_safe(profile, "hardware_tier", "")),
        os_name=str(_getattr_safe(profile, "os_name", "")),
        os_version=str(_getattr_safe(profile, "os_version", "")),
        cpu_summary=str(_getattr_safe(profile, "cpu_summary", "")),
        ram_gb=float(_getattr_safe(profile, "ram_gb", 0.0)),
        gpu_summary=str(_getattr_safe(profile, "gpu_summary", "")),
        vram_gb=float(_getattr_safe(profile, "vram_gb", 0.0)),
        installed_models=installed_models,
        installed_runtimes=installed_runtimes,
        approved_agent_clis=list(_getattr_safe(profile, "approved_agent_clis", [])),
        repos=repos,
        mutation_permissions=[],
    )


def _extract_tooling(profile: Any) -> ToolingProfile:
    return ToolingProfile(
        developer_tools=dict(_getattr_safe(profile, "developer_tools", {})),
        ai_tooling=dict(_getattr_safe(profile, "ai_tooling", {})),
        package_managers=dict(_getattr_safe(profile, "package_managers", {})),
        inference_runtimes=dict(_getattr_safe(profile, "inference_runtimes", {})),
    )


def _build_summary(
    arch: ArchitectureData | None = None,
    prefs: PreferencesData | None = None,
) -> str:
    parts: list[str] = []
    if arch:
        parts.append(f"- **Hardware tier:** {arch.hardware_tier}")
        parts.append(f"- **OS:** {arch.os_name} {arch.os_version}".rstrip())
        if arch.cpu_summary:
            parts.append(f"- **CPU:** {arch.cpu_summary}")
        if arch.gpu_summary:
            parts.append(f"- **GPU:** {arch.gpu_summary}")
        if arch.installed_models:
            names = ", ".join(m.display_name for m in arch.installed_models)
            parts.append(f"- **Models:** {names}")
    if prefs:
        if prefs.preferred_local_models:
            parts.append(
                f"- **Preferred local models:** {', '.join(prefs.preferred_local_models)}"
            )
        if prefs.default_model_routing != "auto":
            parts.append(f"- **Model routing:** {prefs.default_model_routing}")
    return "\n".join(parts)
