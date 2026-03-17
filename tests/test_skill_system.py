"""Tests for the skill subsystem — schema, registry, resolver, generator, executor."""

from __future__ import annotations

from pathlib import Path

import pytest

from skills.models import SkillDefinition, SkillScore, SkillSelectionResult, SourceType
from skills.registry import SkillRegistry
from skills.resolver import resolve_skills, score_skill


# ---------------------------------------------------------------------------
# SkillDefinition schema
# ---------------------------------------------------------------------------


class TestSkillDefinition:
    def test_create_minimal(self):
        skill = SkillDefinition(id="test-1", name="Test Skill", description="A test.")
        assert skill.id == "test-1"
        assert skill.enabled is True
        assert skill.source_type == SourceType.manual
        assert skill.confidence == 1.0

    def test_create_with_all_fields(self):
        skill = SkillDefinition(
            id="full-1",
            name="Full Skill",
            description="Full description",
            version="2.0.0",
            source_type=SourceType.generated,
            triggers=["test", "debug"],
            applicable_stacks=["python", "fastapi"],
            required_context=["pyproject.toml"],
            preferred_tools=["repo_audit"],
            allowed_scope=["src/"],
            execution_strategy="command",
            constraints=["no deletions"],
            acceptance_criteria=["tests pass"],
            confidence=0.8,
            enabled=False,
            explainability="generated from pyproject.toml",
            tags=["testing"],
            inferred_commands=["pytest"],
            inferred_file_targets=["tests/"],
            generation_evidence=["pyproject.toml found"],
        )
        assert skill.version == "2.0.0"
        assert skill.source_type == SourceType.generated
        assert not skill.enabled
        assert len(skill.triggers) == 2

    def test_serialization_roundtrip(self):
        skill = SkillDefinition(
            id="rt-1", name="Roundtrip", description="Test roundtrip",
            triggers=["build"],
        )
        json_str = skill.model_dump_json()
        restored = SkillDefinition.model_validate_json(json_str)
        assert restored.id == skill.id
        assert restored.name == skill.name
        assert restored.triggers == skill.triggers


# ---------------------------------------------------------------------------
# SkillRegistry
# ---------------------------------------------------------------------------


class TestSkillRegistry:
    def test_register_and_get(self, tmp_path: Path):
        reg = SkillRegistry(storage_root=tmp_path / "skills")
        skill = SkillDefinition(id="s1", name="Skill One", description="First skill")
        reg.register(skill)
        assert reg.get("s1") is not None
        assert reg.get("s1").name == "Skill One"

    def test_list_all(self, tmp_path: Path):
        reg = SkillRegistry(storage_root=tmp_path / "skills")
        reg.register(SkillDefinition(id="a", name="A", description="A"))
        reg.register(SkillDefinition(id="b", name="B", description="B"))
        assert len(reg.list_all()) == 2

    def test_enable_disable(self, tmp_path: Path):
        reg = SkillRegistry(storage_root=tmp_path / "skills")
        skill = SkillDefinition(id="tog", name="Toggle", description="Toggleable")
        reg.register(skill)
        assert reg.get("tog").enabled is True
        reg.disable("tog")
        assert reg.get("tog").enabled is False
        reg.enable("tog")
        assert reg.get("tog").enabled is True

    def test_list_by_source(self, tmp_path: Path):
        reg = SkillRegistry(storage_root=tmp_path / "skills")
        reg.register(SkillDefinition(
            id="m", name="Manual", description="M", source_type=SourceType.manual
        ))
        reg.register(SkillDefinition(
            id="g", name="Generated", description="G", source_type=SourceType.generated
        ))
        manual = reg.list_by_source(SourceType.manual)
        assert len(manual) == 1
        assert manual[0].id == "m"

    def test_persist_and_reload(self, tmp_path: Path):
        storage = tmp_path / "skills"
        reg = SkillRegistry(storage_root=storage)
        reg.register(SkillDefinition(
            id="p1", name="Persist", description="Persisted skill",
            source_type=SourceType.generated,
        ))

        reg2 = SkillRegistry(storage_root=storage)
        count = reg2.load_from_disk()
        assert count == 1
        assert reg2.get("p1") is not None
        assert reg2.get("p1").name == "Persist"

    def test_unregister(self, tmp_path: Path):
        reg = SkillRegistry(storage_root=tmp_path / "skills")
        reg.register(SkillDefinition(id="rm", name="Remove", description="R"))
        assert reg.unregister("rm") is True
        assert reg.get("rm") is None
        assert reg.unregister("nonexistent") is False


# ---------------------------------------------------------------------------
# Skill scoring and resolution
# ---------------------------------------------------------------------------


class TestSkillResolver:
    @pytest.fixture()
    def sample_skills(self) -> list[SkillDefinition]:
        return [
            SkillDefinition(
                id="pytest-skill",
                name="Test Triage (pytest)",
                description="Run pytest and fix failures.",
                triggers=["test", "pytest", "failing test"],
                applicable_stacks=["python", "pytest"],
                confidence=0.9,
                tags=["testing"],
            ),
            SkillDefinition(
                id="lint-skill",
                name="Lint Fix (ruff)",
                description="Run ruff to fix linting.",
                triggers=["lint", "ruff", "code style"],
                applicable_stacks=["python", "ruff"],
                confidence=0.9,
                tags=["linting"],
            ),
            SkillDefinition(
                id="docker-skill",
                name="Docker Debug",
                description="Debug Docker builds.",
                triggers=["docker", "container"],
                applicable_stacks=["docker"],
                confidence=0.75,
                tags=["docker"],
            ),
        ]

    def test_score_with_trigger_match(self, sample_skills):
        score = score_skill(sample_skills[0], "fix the failing test in auth module")
        assert score.score > 0
        assert len(score.trigger_matches) > 0
        assert "failing test" in score.trigger_matches or "test" in score.trigger_matches

    def test_score_with_stack_match(self, sample_skills):
        score = score_skill(
            sample_skills[0], "run checks",
            repo_stacks=["python", "pytest"]
        )
        assert score.score > 0
        assert len(score.stack_matches) > 0

    def test_score_no_match(self, sample_skills):
        score = score_skill(sample_skills[2], "refactor the database schema")
        assert score.score < 0.3

    def test_resolve_selects_top_skills(self, sample_skills):
        result = resolve_skills(sample_skills, "fix the pytest failures", repo_stacks=["python", "pytest"])
        assert isinstance(result, SkillSelectionResult)
        assert len(result.selected_skills) > 0
        assert "pytest-skill" in result.selected_skills

    def test_resolve_with_no_match(self, sample_skills):
        result = resolve_skills(sample_skills, "deploy to kubernetes cluster")
        # May or may not match — but should not crash
        assert isinstance(result, SkillSelectionResult)

    def test_disabled_skills_excluded(self, sample_skills):
        sample_skills[0].enabled = False
        result = resolve_skills(sample_skills, "fix the failing test")
        assert "pytest-skill" not in result.selected_skills

    def test_explainability(self, sample_skills):
        result = resolve_skills(sample_skills, "run lint checks", repo_stacks=["python"])
        assert result.explanation != ""
        for scored in result.scored_skills:
            assert scored.relevance_reason is not None


# ---------------------------------------------------------------------------
# Skill generator
# ---------------------------------------------------------------------------


class TestSkillGenerator:
    def test_generate_from_python_repo(self, tmp_path: Path):
        from skills.generator import SkillGenerator

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "myapp"\n\n'
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n\n'
            '[tool.ruff]\nline-length = 88\n',
            encoding="utf-8",
        )

        gen = SkillGenerator(tmp_path)
        skills = gen.generate()
        names = [s.name for s in skills]

        assert "Test Triage (pytest)" in names
        assert "Lint Fix (ruff)" in names
        assert "Python Build Validation" in names

    def test_generate_from_node_repo(self, tmp_path: Path):
        from skills.generator import SkillGenerator

        (tmp_path / "package.json").write_text(
            '{"name": "app", "scripts": {"test": "jest", "build": "tsc"}, '
            '"devDependencies": {"jest": "^29", "typescript": "^5"}}',
            encoding="utf-8",
        )

        gen = SkillGenerator(tmp_path)
        skills = gen.generate()
        names = [s.name for s in skills]

        assert "Test Triage (Node)" in names
        assert "Build Validation (Node)" in names

    def test_generate_from_docker_repo(self, tmp_path: Path):
        from skills.generator import SkillGenerator

        (tmp_path / "Dockerfile").write_text("FROM python:3.11\n", encoding="utf-8")

        gen = SkillGenerator(tmp_path)
        skills = gen.generate()
        names = [s.name for s in skills]
        assert "Docker Debug" in names

    def test_generate_from_empty_repo(self, tmp_path: Path):
        from skills.generator import SkillGenerator

        gen = SkillGenerator(tmp_path)
        skills = gen.generate()
        assert isinstance(skills, list)

    def test_generated_skills_have_evidence(self, tmp_path: Path):
        from skills.generator import SkillGenerator

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "app"\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            encoding="utf-8",
        )

        gen = SkillGenerator(tmp_path)
        skills = gen.generate()
        for skill in skills:
            assert len(skill.generation_evidence) > 0
            assert skill.confidence > 0

    def test_generate_ci_skills(self, tmp_path: Path):
        from skills.generator import SkillGenerator

        wf_dir = tmp_path / ".github" / "workflows"
        wf_dir.mkdir(parents=True)
        (wf_dir / "ci.yml").write_text("name: CI\n", encoding="utf-8")

        gen = SkillGenerator(tmp_path)
        skills = gen.generate()
        names = [s.name for s in skills]
        assert "CI Pipeline Debug" in names

    def test_generate_current_repo(self):
        """Generate skills on the actual ClawSmith repo — must produce real results."""
        from skills.generator import SkillGenerator

        gen = SkillGenerator(Path("."))
        skills = gen.generate()
        assert len(skills) >= 3
        names = [s.name for s in skills]
        assert "Test Triage (pytest)" in names
