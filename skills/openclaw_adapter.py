"""OpenClaw skill adapter — typed, config-gated bridge for skill exchange.

OpenClaw is treated as an **optional** external skill/tool ecosystem.
Every public entry point checks the relevant config toggle before doing
any work and degrades to a silent no-op when OpenClaw is unconfigured or
disabled.

Config toggles (all under ``openclaw`` in settings.yaml):

``enabled``
    Master toggle.  When *False* the entire bridge is inert.
``allow_skill_import``
    Permit importing skills from the OpenClaw gateway into the local
    registry.
``allow_external_execution``
    Permit executing imported (external) skills locally.
``require_approval_for_external_writes``
    When an external skill wants to write files, require explicit
    approval before proceeding.  Checked at execution time by the
    skill executor, but the flag is stamped onto each imported skill
    by this adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.logging_setup import get_logger

from .models import SkillDefinition, SourceType

log = get_logger("skills.openclaw_adapter")


# ---------------------------------------------------------------------------
# Lightweight config reader — never raises
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _OpenClawToggles:
    """Snapshot of the four config booleans that govern this adapter."""

    enabled: bool = False
    gateway_url: str = ""
    allow_skill_import: bool = False
    allow_external_execution: bool = False
    require_approval_for_external_writes: bool = True


def _read_toggles() -> _OpenClawToggles:
    """Read toggles from the config singleton.  Never raises."""
    try:
        from config.config_loader import get_config

        oc = get_config().openclaw
        return _OpenClawToggles(
            enabled=oc.enabled,
            gateway_url=oc.gateway_url,
            allow_skill_import=oc.allow_skill_import,
            allow_external_execution=oc.allow_external_execution,
            require_approval_for_external_writes=oc.require_approval_for_external_writes,
        )
    except Exception:
        return _OpenClawToggles()


# ---------------------------------------------------------------------------
# Export:  ClawSmith skill  →  OpenClaw manifest dict
# ---------------------------------------------------------------------------

def export_skill_for_openclaw(skill: SkillDefinition) -> dict[str, Any]:
    """Convert a ClawSmith skill into an OpenClaw-compatible manifest entry.

    This is a pure data transform — it does **not** check config toggles
    because exporting is just serialisation.  The caller decides whether to
    actually send the manifest anywhere.
    """
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


# ---------------------------------------------------------------------------
# Import:  OpenClaw manifest dict  →  ClawSmith SkillDefinition
# ---------------------------------------------------------------------------

def import_skill_from_openclaw(
    manifest: dict[str, Any],
    *,
    require_approval: bool = True,
) -> SkillDefinition:
    """Convert an OpenClaw skill manifest into a typed ``SkillDefinition``.

    The returned skill is always marked as ``openclaw_imported`` so that
    downstream code (the executor, the resolver, the UI) can distinguish
    it from local skills.

    ``requires_approval`` is stamped from the config toggle
    ``require_approval_for_external_writes`` — the executor enforces it.
    """
    capabilities = manifest.get("capabilities", {})
    origin = manifest.get("source", "openclaw")
    origin_url = manifest.get("origin_url", "")

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
        explainability=f"Imported from OpenClaw ({origin})",
        tags=["openclaw", "imported", "external"],
        origin_url=origin_url,
        requires_approval=require_approval,
    )


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class OpenClawSkillBridge:
    """Bidirectional, config-gated bridge for skill exchange with OpenClaw.

    Every public method returns a safe default (empty list, ``False``, etc.)
    when the relevant config toggle is off or when OpenClaw is unreachable.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self._toggles: _OpenClawToggles | None = None

    # -- toggle helpers -----------------------------------------------------

    @property
    def toggles(self) -> _OpenClawToggles:
        if self._toggles is None:
            self._toggles = _read_toggles()
        return self._toggles

    @property
    def is_available(self) -> bool:
        """True only when OpenClaw is both *enabled* and has a gateway URL."""
        return self.toggles.enabled and bool(self.toggles.gateway_url)

    @property
    def import_allowed(self) -> bool:
        return self.is_available and self.toggles.allow_skill_import

    @property
    def external_execution_allowed(self) -> bool:
        return self.is_available and self.toggles.allow_external_execution

    @property
    def approval_required_for_writes(self) -> bool:
        return self.toggles.require_approval_for_external_writes

    def get_status(self) -> dict[str, Any]:
        """Return a dict summarising the current integration state.

        Safe to call unconditionally — never raises.
        """
        t = self.toggles
        return {
            "enabled": t.enabled,
            "gateway_url": t.gateway_url or "(not set)",
            "is_available": self.is_available,
            "allow_skill_import": t.allow_skill_import,
            "allow_external_execution": t.allow_external_execution,
            "require_approval_for_external_writes": t.require_approval_for_external_writes,
        }

    # -- export -------------------------------------------------------------

    def export_skills(self, skills: list[SkillDefinition]) -> list[dict[str, Any]]:
        """Export enabled ClawSmith skills as OpenClaw manifest entries.

        Works even when OpenClaw is disabled — the caller decides whether
        to transmit the result.
        """
        return [export_skill_for_openclaw(s) for s in skills if s.enabled]

    # -- import -------------------------------------------------------------

    def import_skills(
        self,
        manifests: list[dict[str, Any]],
    ) -> list[SkillDefinition]:
        """Import skill manifests into ClawSmith ``SkillDefinition`` objects.

        Returns an empty list when ``allow_skill_import`` is off.
        """
        if not self.import_allowed:
            log.debug(
                "OpenClaw skill import skipped (enabled=%s, allow_skill_import=%s)",
                self.toggles.enabled,
                self.toggles.allow_skill_import,
            )
            return []

        imported: list[SkillDefinition] = []
        for m in manifests:
            try:
                skill = import_skill_from_openclaw(
                    m,
                    require_approval=self.approval_required_for_writes,
                )
                if not self.external_execution_allowed:
                    skill.enabled = False
                imported.append(skill)
                log.info("Imported OpenClaw skill: %s (approval=%s)", skill.name, skill.requires_approval)
            except Exception as exc:
                log.warning("Failed to import OpenClaw skill: %s", exc)
        return imported

    # -- gateway interaction ------------------------------------------------

    def sync_from_gateway(self) -> list[SkillDefinition]:
        """Fetch available skills from the OpenClaw gateway and import them.

        Returns an empty list when:
        - OpenClaw is not enabled
        - ``allow_skill_import`` is False
        - the gateway is unreachable
        """
        if not self.import_allowed:
            log.debug("OpenClaw skill sync skipped — import not allowed")
            return []

        try:
            import asyncio

            from providers.openclaw_client import get_client

            async def _fetch() -> dict[str, Any]:
                client = get_client()
                try:
                    return await client.get_gateway_info()
                finally:
                    await client.close()

            info = asyncio.run(_fetch())
            skills_data = info.get("skills", [])
            if isinstance(skills_data, list):
                return self.import_skills(skills_data)
        except Exception as exc:
            log.debug("OpenClaw skill sync failed (non-fatal): %s", exc)

        return []

    def register_skills_with_gateway(
        self,
        skills: list[SkillDefinition],
    ) -> bool:
        """Push local skills to the OpenClaw gateway.

        Returns ``False`` (no-op) when OpenClaw is disabled.
        """
        if not self.is_available:
            log.debug("OpenClaw registration skipped — not available")
            return False

        try:
            import asyncio

            from providers.openclaw_adapter import OpenClawAdapter

            adapter = OpenClawAdapter()

            async def _register() -> bool:
                return await adapter.register_with_gateway()

            return asyncio.run(_register())
        except Exception as exc:
            log.warning("OpenClaw registration failed: %s", exc)
            return False
