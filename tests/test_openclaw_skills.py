"""Tests for OpenClaw skill interoperability.

Covers:
- Config toggles (enabled, import, execution, approval)
- Typed adapter (import / export)
- Bridge behaviour with toggles on and off
- Executor gating for external skills
- Graceful no-ops when config is absent or disabled
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from config.config_loader import OpenClawConfig, reset_config
from skills.executor import (
    SkillExecutionRequest,
    SkillExecutionResult,
    _check_external_execution_allowed,
    _check_external_write_approval,
    execute_skill,
)
from skills.models import SkillDefinition, SourceType
from skills.openclaw_adapter import (
    OpenClawSkillBridge,
    _OpenClawToggles,
    _read_toggles,
    export_skill_for_openclaw,
    import_skill_from_openclaw,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_external_skill(**overrides) -> SkillDefinition:
    """Create a minimal external (openclaw_imported) skill."""
    defaults = dict(
        id="ext-1",
        name="External Lint",
        description="Lint from OpenClaw",
        source_type=SourceType.openclaw_imported,
        triggers=["lint"],
        applicable_stacks=["python"],
        confidence=0.7,
        tags=["openclaw", "imported", "external"],
        requires_approval=True,
        inferred_commands=["ruff check ."],
        inferred_file_targets=["src/main.py"],
    )
    defaults.update(overrides)
    return SkillDefinition(**defaults)


def _make_local_skill(**overrides) -> SkillDefinition:
    """Create a minimal local skill."""
    defaults = dict(
        id="local-1",
        name="Local Test",
        description="Run pytest",
        source_type=SourceType.generated,
        triggers=["test"],
        confidence=0.9,
    )
    defaults.update(overrides)
    return SkillDefinition(**defaults)


def _make_config_yaml(tmp_path: Path, **openclaw_overrides) -> Path:
    """Write a minimal settings.yaml with the given openclaw overrides."""
    oc = {
        "skill_name": "Test",
        "mcp_endpoint": "http://localhost:8765/sse",
        "webhook_secret": "",
        "gateway_url": "https://openclaw.test",
        "enabled": True,
        "allow_skill_import": True,
        "allow_external_execution": True,
        "require_approval_for_external_writes": True,
    }
    oc.update(openclaw_overrides)

    data = {
        "models": {
            "local_router":   {"provider": "ollama", "model_name": "ollama/mistral", "max_tokens": 1024, "temperature": 0.2},
            "local_code":     {"provider": "ollama", "model_name": "ollama/codellama", "max_tokens": 4096, "temperature": 0.1},
            "premium":        {"provider": "openai", "model_name": "openai/gpt-4o", "max_tokens": 8192, "temperature": 0.2},
            "prompt_polisher": {"provider": "openai", "model_name": "openai/gpt-4o-mini", "max_tokens": 2048, "temperature": 0.3},
        },
        "routing": {"low_complexity_threshold": 0.35, "high_complexity_threshold": 0.70, "ambiguity_bump_threshold": 0.60},
        "execution": {"default_timeout": 300, "max_retries": 2, "artifacts_dir": "artifacts", "logs_dir": "logs", "allowed_commands": ["ruff", "pytest", "python"]},
        "mcp_server": {"port": 8765},
        "openclaw": oc,
    }

    import yaml
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.dump(data), encoding="utf-8")
    return path


# ===================================================================
# Config toggles
# ===================================================================


class TestOpenClawConfigToggles:
    """The four new fields exist on OpenClawConfig with safe defaults."""

    def test_defaults_are_restrictive(self):
        cfg = OpenClawConfig()
        assert cfg.enabled is False
        assert cfg.allow_skill_import is False
        assert cfg.allow_external_execution is False
        assert cfg.require_approval_for_external_writes is True

    def test_toggles_can_be_enabled(self):
        cfg = OpenClawConfig(
            enabled=True,
            allow_skill_import=True,
            allow_external_execution=True,
            require_approval_for_external_writes=False,
        )
        assert cfg.enabled is True
        assert cfg.allow_skill_import is True
        assert cfg.allow_external_execution is True
        assert cfg.require_approval_for_external_writes is False

    def test_read_toggles_returns_defaults_when_config_unavailable(self):
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": "/nonexistent/settings.yaml"}):
            reset_config()
            toggles = _read_toggles()
        assert toggles.enabled is False
        assert toggles.allow_skill_import is False
        reset_config()

    def test_read_toggles_from_yaml(self, tmp_path: Path):
        path = _make_config_yaml(
            tmp_path,
            enabled=True,
            allow_skill_import=True,
            allow_external_execution=False,
        )
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            toggles = _read_toggles()
        assert toggles.enabled is True
        assert toggles.allow_skill_import is True
        assert toggles.allow_external_execution is False
        reset_config()


# ===================================================================
# Typed adapter — import / export
# ===================================================================


class TestImportExport:
    def test_export_produces_valid_manifest(self):
        skill = _make_local_skill()
        manifest = export_skill_for_openclaw(skill)
        assert manifest["source"] == "clawsmith"
        assert manifest["name"] == "Local Test"
        assert manifest["id"] == "local-1"
        assert "capabilities" in manifest

    def test_import_marks_as_external(self):
        manifest = {
            "name": "Remote Lint",
            "id": "rl-1",
            "description": "From gateway",
            "triggers": ["lint"],
            "capabilities": {"stacks": ["python"], "tools": ["ruff"], "strategy": "remote"},
            "confidence": 0.6,
            "source": "gateway-abc",
            "origin_url": "https://openclaw.example.com/skills/rl-1",
        }
        skill = import_skill_from_openclaw(manifest, require_approval=True)
        assert skill.source_type == SourceType.openclaw_imported
        assert skill.is_external is True
        assert skill.requires_approval is True
        assert "openclaw" in skill.tags
        assert "external" in skill.tags
        assert skill.origin_url == "https://openclaw.example.com/skills/rl-1"
        assert "gateway-abc" in skill.explainability

    def test_import_stamps_approval_flag_from_arg(self):
        skill_yes = import_skill_from_openclaw({"name": "A"}, require_approval=True)
        skill_no = import_skill_from_openclaw({"name": "B"}, require_approval=False)
        assert skill_yes.requires_approval is True
        assert skill_no.requires_approval is False

    def test_roundtrip_preserves_core_fields(self):
        original = _make_local_skill()
        manifest = export_skill_for_openclaw(original)
        imported = import_skill_from_openclaw(manifest)
        assert imported.name == original.name
        assert imported.triggers == original.triggers

    def test_import_with_missing_fields_does_not_crash(self):
        skill = import_skill_from_openclaw({})
        assert skill.name == "Unknown Skill"
        assert skill.is_external is True


# ===================================================================
# Bridge — config-gated behaviour
# ===================================================================


class TestOpenClawSkillBridge:
    def test_bridge_not_available_when_disabled(self, tmp_path: Path):
        path = _make_config_yaml(tmp_path, enabled=False)
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            bridge = OpenClawSkillBridge(tmp_path)
            assert bridge.is_available is False
            assert bridge.import_allowed is False
            assert bridge.external_execution_allowed is False
        reset_config()

    def test_bridge_available_when_enabled_with_gateway(self, tmp_path: Path):
        path = _make_config_yaml(tmp_path, enabled=True, gateway_url="https://gw.test")
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            bridge = OpenClawSkillBridge(tmp_path)
            assert bridge.is_available is True
        reset_config()

    def test_bridge_not_available_when_enabled_but_no_gateway(self, tmp_path: Path):
        path = _make_config_yaml(tmp_path, enabled=True, gateway_url="")
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            bridge = OpenClawSkillBridge(tmp_path)
            assert bridge.is_available is False
        reset_config()

    def test_import_blocked_when_allow_skill_import_false(self, tmp_path: Path):
        path = _make_config_yaml(tmp_path, enabled=True, allow_skill_import=False)
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            bridge = OpenClawSkillBridge(tmp_path)
            result = bridge.import_skills([{"name": "X"}])
            assert result == []
        reset_config()

    def test_import_succeeds_when_allowed(self, tmp_path: Path):
        path = _make_config_yaml(
            tmp_path,
            enabled=True,
            allow_skill_import=True,
            allow_external_execution=True,
        )
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            bridge = OpenClawSkillBridge(tmp_path)
            result = bridge.import_skills([
                {"name": "Import1", "id": "i1", "triggers": ["test"]},
                {"name": "Import2", "id": "i2"},
            ])
            assert len(result) == 2
            assert all(s.is_external for s in result)
            assert all(s.requires_approval for s in result)
        reset_config()

    def test_imported_skills_disabled_when_external_exec_off(self, tmp_path: Path):
        path = _make_config_yaml(
            tmp_path,
            enabled=True,
            allow_skill_import=True,
            allow_external_execution=False,
        )
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            bridge = OpenClawSkillBridge(tmp_path)
            result = bridge.import_skills([{"name": "NoExec"}])
            assert len(result) == 1
            assert result[0].enabled is False
        reset_config()

    def test_export_works_regardless_of_toggles(self, tmp_path: Path):
        path = _make_config_yaml(tmp_path, enabled=False)
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            bridge = OpenClawSkillBridge(tmp_path)
            manifests = bridge.export_skills([_make_local_skill()])
            assert len(manifests) == 1
        reset_config()

    def test_sync_from_gateway_noop_when_import_off(self, tmp_path: Path):
        path = _make_config_yaml(tmp_path, enabled=True, allow_skill_import=False)
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            bridge = OpenClawSkillBridge(tmp_path)
            result = bridge.sync_from_gateway()
            assert result == []
        reset_config()

    def test_register_noop_when_disabled(self, tmp_path: Path):
        path = _make_config_yaml(tmp_path, enabled=False)
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            bridge = OpenClawSkillBridge(tmp_path)
            ok = bridge.register_skills_with_gateway([_make_local_skill()])
            assert ok is False
        reset_config()

    def test_get_status_returns_all_fields(self, tmp_path: Path):
        path = _make_config_yaml(
            tmp_path,
            enabled=True,
            allow_skill_import=True,
            allow_external_execution=False,
            require_approval_for_external_writes=True,
        )
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            bridge = OpenClawSkillBridge(tmp_path)
            status = bridge.get_status()
            assert status["enabled"] is True
            assert status["allow_skill_import"] is True
            assert status["allow_external_execution"] is False
            assert status["require_approval_for_external_writes"] is True
            assert status["is_available"] is True
        reset_config()


# ===================================================================
# Executor — external skill gating
# ===================================================================


class TestExecutorExternalGating:
    """The executor must block external skills unless config permits."""

    def test_local_skill_unaffected_by_openclaw_toggles(self, tmp_path: Path):
        skill = _make_local_skill()
        result = execute_skill(SkillExecutionRequest(
            skill=skill,
            task_description="run tests",
            repo_path=tmp_path,
            dry_run=True,
        ))
        assert result.success is True

    def test_external_skill_blocked_when_exec_disabled(self, tmp_path: Path):
        path = _make_config_yaml(tmp_path, enabled=True, allow_external_execution=False)
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            skill = _make_external_skill()
            result = execute_skill(SkillExecutionRequest(
                skill=skill,
                task_description="lint",
                repo_path=tmp_path,
            ))
            assert result.success is False
            assert "allow_external_execution" in result.error
            assert result.blocked_reason != ""
        reset_config()

    def test_external_skill_blocked_when_openclaw_disabled(self, tmp_path: Path):
        path = _make_config_yaml(tmp_path, enabled=False, allow_external_execution=True)
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            skill = _make_external_skill()
            result = execute_skill(SkillExecutionRequest(
                skill=skill,
                task_description="lint",
                repo_path=tmp_path,
            ))
            assert result.success is False
            assert "enabled" in result.error.lower() or "enabled" in result.blocked_reason
        reset_config()

    def test_external_skill_allowed_when_exec_enabled(self, tmp_path: Path):
        path = _make_config_yaml(
            tmp_path,
            enabled=True,
            allow_external_execution=True,
            require_approval_for_external_writes=False,
        )
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            skill = _make_external_skill(requires_approval=False)
            result = execute_skill(SkillExecutionRequest(
                skill=skill,
                task_description="lint",
                repo_path=tmp_path,
                dry_run=True,
            ))
            assert result.success is True
            assert "[DRY RUN]" in result.output
        reset_config()

    def test_external_write_blocked_without_approval(self, tmp_path: Path):
        path = _make_config_yaml(
            tmp_path,
            enabled=True,
            allow_external_execution=True,
            require_approval_for_external_writes=True,
        )
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            skill = _make_external_skill(requires_approval=True)
            result = execute_skill(SkillExecutionRequest(
                skill=skill,
                task_description="lint",
                repo_path=tmp_path,
                dry_run=False,
                safe_mode=False,
            ))
            assert result.success is False
            assert "approval" in result.error.lower()
        reset_config()

    def test_external_write_allowed_with_approval_callback(self, tmp_path: Path):
        path = _make_config_yaml(
            tmp_path,
            enabled=True,
            allow_external_execution=True,
            require_approval_for_external_writes=True,
        )
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            skill = _make_external_skill(requires_approval=True)
            result = execute_skill(SkillExecutionRequest(
                skill=skill,
                task_description="lint",
                repo_path=tmp_path,
                dry_run=False,
                safe_mode=False,
                approval_callback=lambda _skill: True,
            ))
            assert result.success is True
        reset_config()

    def test_external_write_rejected_by_callback(self, tmp_path: Path):
        path = _make_config_yaml(
            tmp_path,
            enabled=True,
            allow_external_execution=True,
            require_approval_for_external_writes=True,
        )
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            skill = _make_external_skill(requires_approval=True)
            result = execute_skill(SkillExecutionRequest(
                skill=skill,
                task_description="lint",
                repo_path=tmp_path,
                dry_run=False,
                safe_mode=False,
                approval_callback=lambda _skill: False,
            ))
            assert result.success is False
            assert "rejected" in result.error.lower() or "rejected" in result.blocked_reason
        reset_config()

    def test_external_write_allowed_when_approval_not_required(self, tmp_path: Path):
        path = _make_config_yaml(
            tmp_path,
            enabled=True,
            allow_external_execution=True,
            require_approval_for_external_writes=False,
        )
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            skill = _make_external_skill(requires_approval=False)
            result = execute_skill(SkillExecutionRequest(
                skill=skill,
                task_description="lint",
                repo_path=tmp_path,
                dry_run=False,
                safe_mode=False,
            ))
            assert result.success is True
        reset_config()

    def test_dry_run_bypasses_approval_check(self, tmp_path: Path):
        path = _make_config_yaml(
            tmp_path,
            enabled=True,
            allow_external_execution=True,
            require_approval_for_external_writes=True,
        )
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": str(path)}):
            reset_config()
            skill = _make_external_skill(requires_approval=True)
            result = execute_skill(SkillExecutionRequest(
                skill=skill,
                task_description="lint",
                repo_path=tmp_path,
                dry_run=True,
            ))
            assert result.success is True
            assert "[DRY RUN]" in result.output
            assert "External: True" in result.output
            assert "Requires approval: True" in result.output
        reset_config()


# ===================================================================
# Graceful no-ops
# ===================================================================


class TestGracefulNoOps:
    """Absent config → everything degrades to safe no-ops."""

    def test_bridge_defaults_with_no_config(self, tmp_path: Path):
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": "/nonexistent.yaml"}):
            reset_config()
            bridge = OpenClawSkillBridge(tmp_path)
            assert bridge.is_available is False
            assert bridge.import_allowed is False
            assert bridge.sync_from_gateway() == []
            assert bridge.register_skills_with_gateway([_make_local_skill()]) is False
            status = bridge.get_status()
            assert status["enabled"] is False
        reset_config()

    def test_executor_allows_local_skill_with_broken_config(self, tmp_path: Path):
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": "/nonexistent.yaml"}):
            reset_config()
            skill = _make_local_skill()
            result = execute_skill(SkillExecutionRequest(
                skill=skill,
                task_description="test",
                repo_path=tmp_path,
                dry_run=True,
            ))
            assert result.success is True
        reset_config()

    def test_executor_blocks_external_skill_with_broken_config(self, tmp_path: Path):
        reset_config()
        with patch.dict(os.environ, {"CLAWSMITH_CONFIG_PATH": "/nonexistent.yaml"}):
            reset_config()
            skill = _make_external_skill()
            result = execute_skill(SkillExecutionRequest(
                skill=skill,
                task_description="lint",
                repo_path=tmp_path,
            ))
            assert result.success is False
        reset_config()


# ===================================================================
# SkillDefinition model additions
# ===================================================================


class TestSkillDefinitionExtensions:
    def test_is_external_for_imported_skill(self):
        skill = SkillDefinition(
            id="x", name="X", description="X",
            source_type=SourceType.openclaw_imported,
        )
        assert skill.is_external is True

    def test_is_external_for_local_skill(self):
        for st in (SourceType.manual, SourceType.generated, SourceType.dependency_derived, SourceType.repo_derived):
            skill = SkillDefinition(id="y", name="Y", description="Y", source_type=st)
            assert skill.is_external is False

    def test_origin_url_and_requires_approval_defaults(self):
        skill = SkillDefinition(id="z", name="Z", description="Z")
        assert skill.origin_url == ""
        assert skill.requires_approval is False

    def test_fields_roundtrip_json(self):
        skill = SkillDefinition(
            id="rt", name="RT", description="RT",
            source_type=SourceType.openclaw_imported,
            origin_url="https://oc.example.com/skill/rt",
            requires_approval=True,
        )
        restored = SkillDefinition.model_validate_json(skill.model_dump_json())
        assert restored.origin_url == skill.origin_url
        assert restored.requires_approval is True
        assert restored.is_external is True
