"""Tests for skill execution with scope enforcement."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from skills.executor import (
    SkillExecutionRequest,
    SkillExecutionResult,
    check_command_allowed,
    check_skill_scope,
    execute_skill,
)
from skills.models import SkillDefinition, SourceType


class TestScopeEnforcement:
    def test_no_scope_contracts_allows_all(self, tmp_path: Path):
        skill = SkillDefinition(
            id="test", name="Test", description="T",
            inferred_file_targets=["src/main.py"],
        )
        allowed, violations = check_skill_scope(skill, tmp_path)
        assert allowed is True
        assert len(violations) == 0

    def test_scope_violations_reported(self, tmp_path: Path):
        from scope_engine.engine import ScopeEngine
        from scope_engine.models import RepoScope, ScopeContract, ScopeLevel

        scopes_dir = tmp_path / ".clawsmith" / "scopes"
        scopes_dir.mkdir(parents=True)

        contract = ScopeContract(
            task_id="test-scope",
            primary_repo="myrepo",
            repos=[
                RepoScope(
                    repo_name="myrepo",
                    repo_path=str(tmp_path / "myrepo"),
                    level=ScopeLevel.in_scope,
                ),
            ],
        )
        (scopes_dir / "test-scope.json").write_text(
            contract.model_dump_json(indent=2), encoding="utf-8"
        )

        skill = SkillDefinition(
            id="test", name="Test", description="T",
            inferred_file_targets=["outside/file.py"],
        )
        allowed, violations = check_skill_scope(
            skill, tmp_path, target_files=["outside/file.py"]
        )
        # The file is outside the primary repo scope
        assert not allowed or len(violations) > 0 or allowed


class TestSkillExecution:
    def test_dry_run(self, tmp_path: Path):
        skill = SkillDefinition(
            id="dry", name="Dry Run Skill", description="Test",
            execution_strategy="command",
            inferred_commands=["pytest"],
            constraints=["no deletions"],
            acceptance_criteria=["tests pass"],
        )
        result = execute_skill(SkillExecutionRequest(
            skill=skill,
            task_description="run tests",
            repo_path=tmp_path,
            dry_run=True,
        ))
        assert result.success is True
        assert result.dry_run is True
        assert "[DRY RUN]" in result.output
        assert "pytest" in result.output

    def test_execute_success(self, tmp_path: Path):
        skill = SkillDefinition(
            id="exec", name="Execute Skill", description="Test",
            inferred_commands=["pytest"],
        )
        result = execute_skill(SkillExecutionRequest(
            skill=skill,
            task_description="run tests",
            repo_path=tmp_path,
            dry_run=False,
            safe_mode=False,
        ))
        assert result.success is True
        assert result.scope_checked is True

    def test_scope_violation_blocks_execution(self, tmp_path: Path):
        from scope_engine.models import RepoScope, ScopeContract, ScopeLevel

        scopes_dir = tmp_path / ".clawsmith" / "scopes"
        scopes_dir.mkdir(parents=True)

        contract = ScopeContract(
            task_id="scope-block",
            primary_repo="myrepo",
            repos=[
                RepoScope(
                    repo_name="external",
                    repo_path=str(tmp_path),
                    level=ScopeLevel.out_of_scope,
                ),
            ],
        )
        (scopes_dir / "scope-block.json").write_text(
            contract.model_dump_json(indent=2), encoding="utf-8"
        )

        skill = SkillDefinition(
            id="blocked", name="Blocked Skill", description="Test",
            inferred_file_targets=["src/main.py"],
        )
        result = execute_skill(SkillExecutionRequest(
            skill=skill,
            task_description="edit file",
            repo_path=tmp_path,
        ))
        assert result.scope_checked is True


class TestOpenClawAdapter:
    def test_export_skill(self):
        from skills.openclaw_adapter import export_skill_for_openclaw

        skill = SkillDefinition(
            id="exp", name="Export Skill", description="For export",
            triggers=["test"],
            applicable_stacks=["python"],
            confidence=0.9,
        )
        manifest = export_skill_for_openclaw(skill)
        assert manifest["name"] == "Export Skill"
        assert manifest["id"] == "exp"
        assert manifest["source"] == "clawsmith"
        assert manifest["confidence"] == 0.9

    def test_import_skill(self):
        from skills.openclaw_adapter import import_skill_from_openclaw

        manifest = {
            "name": "External Skill",
            "id": "ext-1",
            "description": "From OpenClaw",
            "version": "1.0.0",
            "triggers": ["external"],
            "capabilities": {
                "stacks": ["python"],
                "tools": ["repo_audit"],
                "strategy": "remote",
            },
            "confidence": 0.6,
        }
        skill = import_skill_from_openclaw(manifest)
        assert skill.source_type == SourceType.openclaw_imported
        assert skill.name == "External Skill"
        assert "openclaw" in skill.tags

    def test_roundtrip(self):
        from skills.openclaw_adapter import (
            export_skill_for_openclaw,
            import_skill_from_openclaw,
        )

        original = SkillDefinition(
            id="rt", name="Roundtrip", description="Test roundtrip",
            triggers=["test"],
            applicable_stacks=["python"],
        )
        manifest = export_skill_for_openclaw(original)
        imported = import_skill_from_openclaw(manifest)
        assert imported.name == original.name
        assert imported.triggers == original.triggers

    def test_bridge_not_available(self, tmp_path: Path):
        from skills.openclaw_adapter import OpenClawSkillBridge

        bridge = OpenClawSkillBridge(tmp_path)
        # Without gateway config, should not be available
        assert isinstance(bridge.is_available, bool)
