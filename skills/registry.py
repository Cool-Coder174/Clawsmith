"""Skill registry — loads, stores, enables/disables, and queries skills."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from orchestrator.logging_setup import get_logger

from .models import SkillDefinition, SourceType

log = get_logger("skills.registry")


class SkillRegistry:
    """In-memory registry of all available skills, backed by durable storage."""

    def __init__(self, storage_root: Path | None = None) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        self._storage_root = (storage_root or Path(".clawsmith/skills")).resolve()

    @property
    def storage_root(self) -> Path:
        return self._storage_root

    def register(self, skill: SkillDefinition, *, persist: bool = True) -> None:
        """Register a skill and optionally persist it to disk."""
        self._skills[skill.id] = skill
        if persist:
            self._persist_skill(skill)
        log.info("Registered skill: %s (%s)", skill.name, skill.id)

    def unregister(self, skill_id: str) -> bool:
        """Remove a skill from the registry (does not delete from disk)."""
        if skill_id in self._skills:
            del self._skills[skill_id]
            return True
        return False

    def get(self, skill_id: str) -> SkillDefinition | None:
        return self._skills.get(skill_id)

    def list_all(self) -> list[SkillDefinition]:
        return list(self._skills.values())

    def list_enabled(self) -> list[SkillDefinition]:
        return [s for s in self._skills.values() if s.enabled]

    def list_by_source(self, source_type: SourceType) -> list[SkillDefinition]:
        return [s for s in self._skills.values() if s.source_type == source_type]

    def enable(self, skill_id: str) -> bool:
        skill = self._skills.get(skill_id)
        if skill:
            skill.enabled = True
            skill.updated_at = datetime.now(UTC).isoformat()
            self._persist_skill(skill)
            return True
        return False

    def disable(self, skill_id: str) -> bool:
        skill = self._skills.get(skill_id)
        if skill:
            skill.enabled = False
            skill.updated_at = datetime.now(UTC).isoformat()
            self._persist_skill(skill)
            return True
        return False

    def load_from_disk(self) -> int:
        """Load all persisted skills from the storage directory. Returns count loaded."""
        import json

        count = 0
        for subdir in ("manual", "generated", "imported"):
            skill_dir = self._storage_root / subdir
            if not skill_dir.exists():
                continue
            for path in skill_dir.glob("*.json"):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                    skill = SkillDefinition.model_validate(data)
                    self._skills[skill.id] = skill
                    count += 1
                except Exception as exc:
                    log.warning("Failed to load skill from %s: %s", path, exc)
        log.info("Loaded %d skills from %s", count, self._storage_root)
        return count

    def _persist_skill(self, skill: SkillDefinition) -> Path:
        """Write skill definition to the appropriate subdirectory."""
        if skill.source_type in (SourceType.manual,):
            subdir = "manual"
        elif skill.source_type in (
            SourceType.generated,
            SourceType.dependency_derived,
            SourceType.repo_derived,
        ):
            subdir = "generated"
        elif skill.source_type == SourceType.openclaw_imported:
            subdir = "imported"
        else:
            subdir = "manual"

        skill_dir = self._storage_root / subdir
        skill_dir.mkdir(parents=True, exist_ok=True)
        path = skill_dir / f"{skill.id}.json"
        path.write_text(skill.model_dump_json(indent=2), encoding="utf-8")
        return path
