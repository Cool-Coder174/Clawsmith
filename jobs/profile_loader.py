"""Load agent profiles from YAML and convert them to JobSpec instances."""

from __future__ import annotations

import logging
from pathlib import Path
from string import Template
from uuid import uuid4

import yaml

from orchestrator.schemas import AgentProfile, JobSpec

_REPO_ROOT = Path(__file__).parent.parent
_DEFAULT_PROFILES_DIR = _REPO_ROOT / "config" / "agent_profiles"

logger = logging.getLogger(__name__)


class ProfileLoader:
    """Discovers, validates, and converts YAML agent profiles."""

    def __init__(self, profiles_dir: Path | None = None) -> None:
        self._profiles_dir = profiles_dir or _DEFAULT_PROFILES_DIR

    def load_all(self) -> list[AgentProfile]:
        """Return all valid ``AgentProfile`` objects from the profiles directory."""
        profiles: list[AgentProfile] = []
        if not self._profiles_dir.is_dir():
            logger.warning("Profiles directory does not exist: %s", self._profiles_dir)
            return profiles

        for yaml_path in sorted(self._profiles_dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                profile = AgentProfile.model_validate(data)
                profiles.append(profile)
            except Exception:
                logger.warning("Failed to load profile from %s", yaml_path, exc_info=True)

        return profiles

    def load_by_name(self, name: str) -> AgentProfile:
        """Return the profile matching *name*, or raise ``ValueError``."""
        profiles = self.load_all()
        for profile in profiles:
            if profile.name == name:
                return profile
        available = [p.name for p in profiles]
        raise ValueError(
            f"Profile {name!r} not found. Available profiles: {available}"
        )

    def to_job_spec(
        self,
        profile: AgentProfile,
        variable_overrides: dict[str, str] | None = None,
    ) -> JobSpec:
        """Convert an ``AgentProfile`` into a ``JobSpec``.

        Template variables from the profile are merged with *variable_overrides*
        (overrides win), then ``string.Template.safe_substitute`` is applied to
        the prompt text stored in ``profile.variables.get("CURSOR_PROMPT", "")``.
        """
        merged_vars = {**profile.variables}
        if variable_overrides:
            merged_vars.update(variable_overrides)

        prompt_text = merged_vars.get("CURSOR_PROMPT", merged_vars.get("AGENT_PROMPT", ""))
        prompt_rendered = Template(prompt_text).safe_substitute(merged_vars)

        return JobSpec(
            id=uuid4().hex[:12],
            task_type=profile.task_type,
            objective=merged_vars.get("OBJECTIVE", profile.description),
            working_directory=profile.working_directory,
            build_commands=list(profile.build_commands),
            test_commands=list(profile.test_commands),
            prompt=prompt_rendered,
            agent_target=profile.agent_target,
            provider_preference=profile.provider_preference,
            model_preference=profile.model_preference,
            timeout_seconds=profile.timeout_seconds,
            dry_run=profile.dry_run,
            retries=profile.retries,
            output_format=profile.output_format,
            approval_mode=profile.approval_mode,
        )
