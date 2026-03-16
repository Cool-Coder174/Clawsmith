"""OpenClaw skill adapter — imports/exports skills between ClawSmith and OpenClaw."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.logging_setup import get_logger

from .models import SkillDefinition, SourceType

log = get_logger("skills.openclaw_adapter")


def _openclaw_available() -> bool:
    """Check if OpenClaw is configured and reachable."""
    try:
        from config.config_loader import get_config

        cfg = get_config()
        return bool(cfg.openclaw.gateway_url)
    except Exception:
        return False


def export_skill_for_openclaw(skill: SkillDefinition) -> dict[str, Any]:
    """Convert a ClawSmith skill into an OpenClaw-compatible manifest entry."""
    return {
        "name": skill.name,
        "id": skill.id,
        "description": skill.description,
        "version": skill.version,
        "triggers": skill.triggers,
        "capabilities": {
            "stacks": skill.applicable_stacks,
            "tools": skill.preferred_tools,
            "strategy": skill.execution_strategy,
        },
        "constraints": skill.constraints,
        "acceptance_criteria": skill.acceptance_criteria,
        "source": "clawsmith",
        "confidence": skill.confidence,
    }


def import_skill_from_openclaw(manifest: dict[str, Any]) -> SkillDefinition:
    """Convert an OpenClaw skill manifest into a ClawSmith SkillDefinition."""
    capabilities = manifest.get("capabilities", {})
    return SkillDefinition(
        id=manifest.get("id", manifest.get("name", "unknown").replace(" ", "_").lower()),
        name=manifest.get("name", "Unknown Skill"),
        description=manifest.get("description", ""),
        version=manifest.get("version", "1.0.0"),
        source_type=SourceType.openclaw_imported,
        triggers=manifest.get("triggers", []),
        applicable_stacks=capabilities.get("stacks", []),
        preferred_tools=capabilities.get("tools", []),
        execution_strategy=capabilities.get("strategy", "remote"),
        constraints=manifest.get("constraints", []),
        acceptance_criteria=manifest.get("acceptance_criteria", []),
        confidence=manifest.get("confidence", 0.5),
        explainability=f"Imported from OpenClaw: {manifest.get('source', 'unknown')}",
        tags=["openclaw", "imported"],
    )


class OpenClawSkillBridge:
    """Bidirectional bridge for skill exchange with OpenClaw."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self._available: bool | None = None

    @property
    def is_available(self) -> bool:
        if self._available is None:
            self._available = _openclaw_available()
        return self._available

    def export_skills(self, skills: list[SkillDefinition]) -> list[dict[str, Any]]:
        """Export ClawSmith skills as OpenClaw manifest entries."""
        return [export_skill_for_openclaw(s) for s in skills if s.enabled]

    def import_skills(self, manifests: list[dict[str, Any]]) -> list[SkillDefinition]:
        """Import OpenClaw skill manifests as ClawSmith skills."""
        imported = []
        for m in manifests:
            try:
                skill = import_skill_from_openclaw(m)
                imported.append(skill)
                log.info("Imported OpenClaw skill: %s", skill.name)
            except Exception as exc:
                log.warning("Failed to import OpenClaw skill: %s", exc)
        return imported

    def sync_from_gateway(self) -> list[SkillDefinition]:
        """Fetch available skills from OpenClaw gateway and import them.

        Returns empty list if OpenClaw is not configured.
        """
        if not self.is_available:
            log.debug("OpenClaw not available, skipping skill sync")
            return []

        try:
            import asyncio

            from providers.openclaw_client import get_client

            async def _fetch() -> dict[str, Any]:
                client = get_client()
                try:
                    info = await client.get_gateway_info()
                    return info
                finally:
                    await client.close()

            info = asyncio.run(_fetch())
            skills_data = info.get("skills", [])
            if isinstance(skills_data, list):
                return self.import_skills(skills_data)
        except Exception as exc:
            log.debug("OpenClaw skill sync failed (non-fatal): %s", exc)

        return []

    def register_skills_with_gateway(self, skills: list[SkillDefinition]) -> bool:
        """Register ClawSmith skills with the OpenClaw gateway."""
        if not self.is_available:
            return False

        try:
            import asyncio

            from providers.openclaw_adapter import OpenClawAdapter

            adapter = OpenClawAdapter()
            manifests = self.export_skills(skills)

            async def _register() -> bool:
                return await adapter.register_with_gateway()

            return asyncio.run(_register())
        except Exception as exc:
            log.warning("OpenClaw registration failed: %s", exc)
            return False
