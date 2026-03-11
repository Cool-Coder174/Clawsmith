from __future__ import annotations

import json
from pathlib import Path

from orchestrator.logging_setup import get_logger

from .models import ArchitectureData, PreferencesData, ToolingProfile

log = get_logger("memory.reader")


def _read_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to read %s: %s", path, exc)
        return None


class MemoryReader:
    """Reads persisted ClawSmith memory files back into data objects."""

    def __init__(self, workspace_root: Path) -> None:
        self.root = workspace_root
        self.clawsmith_dir = workspace_root / "clawsmith"
        self.memory_dir = workspace_root / "memory"

    # ------------------------------------------------------------------
    # Structured data (round-trip through JSON sidecars)
    # ------------------------------------------------------------------

    def read_architecture(self) -> ArchitectureData | None:
        """Read architecture data from the JSON sidecar, falling back to None."""
        path = self.clawsmith_dir / "architecture.json"
        data = _read_json(path)
        if data is None:
            log.debug("No architecture data found at %s", path)
            return None
        try:
            return ArchitectureData.model_validate(data)
        except Exception as exc:
            log.warning("Invalid architecture data in %s: %s", path, exc)
            return None

    def read_preferences(self) -> PreferencesData | None:
        path = self.clawsmith_dir / "preferences.json"
        data = _read_json(path)
        if data is None:
            log.debug("No preferences data found at %s", path)
            return None
        try:
            return PreferencesData.model_validate(data)
        except Exception as exc:
            log.warning("Invalid preferences data in %s: %s", path, exc)
            return None

    def read_tooling_profile(self) -> ToolingProfile | None:
        path = self.clawsmith_dir / "tooling-profile.json"
        data = _read_json(path)
        if data is None:
            return None
        try:
            return ToolingProfile.model_validate(data)
        except Exception as exc:
            log.warning("Invalid tooling profile in %s: %s", path, exc)
            return None

    def read_repo_graph(self) -> dict | None:
        return _read_json(self.clawsmith_dir / "repo-graph.json")

    def read_scope_rules(self) -> dict | None:
        return _read_json(self.clawsmith_dir / "scope-rules.json")

    # ------------------------------------------------------------------
    # Top-level MEMORY.md
    # ------------------------------------------------------------------

    def read_memory_md(self) -> str | None:
        path = self.root / "MEMORY.md"
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError as exc:
            log.warning("Failed to read MEMORY.md: %s", exc)
            return None

    # ------------------------------------------------------------------
    # Convenience: write-side JSON sidecars for round-trip fidelity
    # ------------------------------------------------------------------

    def _write_json_sidecar(self, name: str, data: dict) -> Path:
        """Write a JSON sidecar alongside the Markdown file for lossless reads."""
        self.clawsmith_dir.mkdir(parents=True, exist_ok=True)
        path = self.clawsmith_dir / name
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path
