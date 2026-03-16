"""Skill executor — runs skills with scope, mutation, and external-skill guardrails."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.logging_setup import get_logger

from .models import SkillDefinition, SourceType

log = get_logger("skills.executor")


@dataclass
class SkillExecutionRequest:
    """Request to execute a skill for a task."""

    skill: SkillDefinition
    task_description: str
    repo_path: Path
    dry_run: bool = False
    safe_mode: bool = True
    approval_callback: Any | None = None


@dataclass
class SkillExecutionResult:
    """Result of executing a skill."""

    skill_id: str
    success: bool = False
    output: str = ""
    files_modified: list[str] = field(default_factory=list)
    commands_run: list[str] = field(default_factory=list)
    scope_checked: bool = False
    scope_violations: list[str] = field(default_factory=list)
    dry_run: bool = False
    error: str = ""
    blocked_reason: str = ""


# ---------------------------------------------------------------------------
# Guardrail helpers
# ---------------------------------------------------------------------------

def check_skill_scope(
    skill: SkillDefinition,
    repo_path: Path,
    target_files: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """Verify that a skill's file targets are within scope.

    Returns (allowed, list_of_violations).
    """
    violations: list[str] = []

    try:
        from scope_engine.engine import ScopeEngine

        scopes_dir = repo_path / ".clawsmith" / "scopes"
        if not scopes_dir.exists():
            return True, []

        engine = ScopeEngine(workspace_root=repo_path)
        contracts = list(scopes_dir.glob("*.json"))
        if not contracts:
            return True, []

        contract = engine.load_contract(contracts[-1])

        files_to_check = target_files or skill.inferred_file_targets
        for fpath in files_to_check:
            full_path = str((repo_path / fpath).resolve())
            allowed, reason = engine.check_file_in_scope(contract, full_path)
            if not allowed:
                violations.append(f"{fpath}: {reason}")

    except Exception as exc:
        log.warning("Scope check failed (allowing): %s", exc)
        return True, []

    return len(violations) == 0, violations


def check_command_allowed(command: str) -> bool:
    """Check if a command is in the allowed list from config."""
    try:
        from config.config_loader import get_config

        cfg = get_config()
        allowed = cfg.execution.allowed_commands
        cmd_base = command.split()[0] if command.strip() else ""
        return cmd_base in allowed
    except Exception:
        return False


def _check_external_execution_allowed() -> tuple[bool, str]:
    """Return (allowed, reason) for executing an external (imported) skill."""
    try:
        from config.config_loader import get_config

        oc = get_config().openclaw
        if not oc.enabled:
            return False, "openclaw.enabled is false"
        if not oc.allow_external_execution:
            return False, "openclaw.allow_external_execution is false"
        return True, ""
    except Exception:
        return False, "could not load openclaw config"


def _check_external_write_approval(
    skill: SkillDefinition,
    request: SkillExecutionRequest,
) -> tuple[bool, str]:
    """If the skill wants to write and approval is required, verify it.

    Returns (approved, reason).
    """
    if not skill.requires_approval:
        return True, ""

    has_writes = bool(skill.inferred_file_targets) or bool(skill.inferred_commands)
    if not has_writes:
        return True, ""

    if request.dry_run:
        return True, "dry_run — no actual writes"

    if request.approval_callback is not None:
        try:
            approved = request.approval_callback(skill)
            if approved:
                return True, "approved by callback"
            return False, "rejected by approval callback"
        except Exception as exc:
            return False, f"approval callback error: {exc}"

    return False, (
        "external skill requires approval for writes but no approval_callback "
        "was provided (set require_approval_for_external_writes: false to skip)"
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def execute_skill(request: SkillExecutionRequest) -> SkillExecutionResult:
    """Execute a skill with full guardrail enforcement.

    Guard order:
    1. External-skill toggle check (``openclaw.allow_external_execution``)
    2. External-write approval check (``openclaw.require_approval_for_external_writes``)
    3. Scope contract check
    4. Dry-run short-circuit
    5. Safe-mode command allowlist check
    """
    skill = request.skill
    result = SkillExecutionResult(
        skill_id=skill.id,
        dry_run=request.dry_run,
    )

    # --- 1. External skill toggle ------------------------------------------
    if skill.is_external:
        allowed, reason = _check_external_execution_allowed()
        if not allowed:
            result.success = False
            result.blocked_reason = reason
            result.error = (
                f"External skill '{skill.name}' blocked: {reason}. "
                "Enable openclaw.allow_external_execution in settings.yaml."
            )
            log.info("External skill %s blocked: %s", skill.id, reason)
            return result

    # --- 2. External write approval ----------------------------------------
    if skill.is_external:
        approved, reason = _check_external_write_approval(skill, request)
        if not approved:
            result.success = False
            result.blocked_reason = reason
            result.error = (
                f"External skill '{skill.name}' needs write approval: {reason}"
            )
            log.info("External skill %s write not approved: %s", skill.id, reason)
            return result

    # --- 3. Scope check ----------------------------------------------------
    allowed, violations = check_skill_scope(skill, request.repo_path)
    result.scope_checked = True
    result.scope_violations = violations

    if not allowed:
        result.success = False
        result.error = (
            f"Scope violation: {len(violations)} file(s) out of scope. "
            + "; ".join(violations[:3])
        )
        log.warning("Skill %s blocked by scope: %s", skill.id, result.error)
        return result

    # --- 4. Dry run --------------------------------------------------------
    if request.dry_run:
        result.success = True
        result.output = (
            f"[DRY RUN] Skill '{skill.name}' would execute:\n"
            f"  Strategy: {skill.execution_strategy}\n"
            f"  Commands: {', '.join(skill.inferred_commands) or 'none'}\n"
            f"  File targets: {', '.join(skill.inferred_file_targets) or 'inferred at runtime'}\n"
            f"  Constraints: {', '.join(skill.constraints) or 'none'}\n"
            f"  Acceptance criteria: {', '.join(skill.acceptance_criteria) or 'none'}\n"
            f"  External: {skill.is_external}\n"
            f"  Requires approval: {skill.requires_approval}"
        )
        return result

    # --- 5. Safe-mode command allowlist ------------------------------------
    if request.safe_mode:
        for cmd in skill.inferred_commands:
            if not check_command_allowed(cmd):
                result.success = False
                result.error = f"Command '{cmd}' not in allowed_commands list"
                return result

    result.success = True
    result.output = (
        f"Skill '{skill.name}' context prepared for execution.\n"
        f"Strategy: {skill.execution_strategy}\n"
        f"Constraints: {', '.join(skill.constraints) or 'none'}\n"
        f"Acceptance: {', '.join(skill.acceptance_criteria) or 'none'}"
    )
    result.commands_run = skill.inferred_commands
    return result
