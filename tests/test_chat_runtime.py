"""Tests for the chat runtime and session state."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.chat_runtime import ChatRuntime
from orchestrator.session_state import SessionState
from skills.models import SkillDefinition


# ---------------------------------------------------------------------------
# SessionState
# ---------------------------------------------------------------------------


class TestSessionState:
    def test_initial_state(self):
        state = SessionState()
        assert state.turn_count == 0
        assert state.dry_run is False
        assert state.safe_mode is True
        assert state.interactive is True
        assert len(state.history) == 0
        assert len(state.loaded_skills) == 0

    def test_add_messages(self):
        state = SessionState()
        state.add_user_message("Hello")
        assert state.turn_count == 1
        assert len(state.history) == 1
        assert state.history[0]["role"] == "user"

        state.add_agent_message("Hi there")
        assert len(state.history) == 2
        assert state.history[1]["role"] == "assistant"

    def test_explainability_summary(self):
        state = SessionState()
        state.add_user_message("test")
        state.loaded_skills = [
            SkillDefinition(id="s1", name="S1", description="Test"),
        ]
        summary = state.get_explainability_summary()
        assert summary["turn_count"] == 1
        assert summary["skills_loaded"] == 1
        assert summary["safe_mode"] is True

    def test_custom_repo_path(self, tmp_path: Path):
        state = SessionState(repo_path=tmp_path, workspace_root=tmp_path)
        assert state.repo_path == tmp_path


# ---------------------------------------------------------------------------
# ChatRuntime
# ---------------------------------------------------------------------------


class TestChatRuntime:
    def test_initialize(self, tmp_path: Path):
        runtime = ChatRuntime(repo_path=tmp_path)
        runtime.initialize()
        assert runtime.state.repo_path == tmp_path

    def test_initialize_idempotent(self, tmp_path: Path):
        runtime = ChatRuntime(repo_path=tmp_path)
        runtime.initialize()
        runtime.initialize()

    def test_list_skills_empty(self, tmp_path: Path):
        runtime = ChatRuntime(repo_path=tmp_path)
        skills = runtime.list_skills()
        assert isinstance(skills, list)

    def test_regenerate_skills(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\n'
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            encoding="utf-8",
        )
        runtime = ChatRuntime(repo_path=tmp_path)
        count = runtime.regenerate_skills()
        assert count >= 1
        skills = runtime.list_skills()
        assert len(skills) >= 1

    def test_select_skills_for_task(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\n'
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            encoding="utf-8",
        )
        runtime = ChatRuntime(repo_path=tmp_path)
        runtime.regenerate_skills()
        result = runtime.select_skills_for("fix the failing tests")
        assert "selected" in result
        assert "scored" in result
        assert "explanation" in result

    def test_process_task(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\n'
            '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            encoding="utf-8",
        )
        runtime = ChatRuntime(repo_path=tmp_path, dry_run=True)
        runtime.regenerate_skills()
        result = runtime.process_task("run the pytest tests")
        assert "success" in result
        assert "explain" in result

    def test_remember_and_list(self, tmp_path: Path):
        runtime = ChatRuntime(repo_path=tmp_path)
        entry_id = runtime.remember("Important fact", tags=["test"])
        assert entry_id
        entries = runtime.list_memories()
        assert len(entries) == 1

    def test_retrieve_memories(self, tmp_path: Path):
        runtime = ChatRuntime(repo_path=tmp_path)
        runtime.remember("Always run tests before pushing", tags=["testing"])
        result = runtime.retrieve_memories_for("how to run tests")
        assert isinstance(result, list)

    def test_dry_run_mode(self, tmp_path: Path):
        runtime = ChatRuntime(repo_path=tmp_path, dry_run=True)
        assert runtime.state.dry_run is True

    def test_safe_mode(self, tmp_path: Path):
        runtime = ChatRuntime(repo_path=tmp_path, safe_mode=True)
        assert runtime.state.safe_mode is True

    def test_non_interactive_mode(self, tmp_path: Path):
        runtime = ChatRuntime(repo_path=tmp_path, interactive=False)
        assert runtime.state.interactive is False


# ---------------------------------------------------------------------------
# Backward compatibility — verify existing CLI-relevant paths don't break
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    def test_cli_module_loads(self):
        from orchestrator.cli import cli
        assert cli is not None

    def test_pipeline_module_loads(self):
        from orchestrator.pipeline import OrchestrationPipeline
        assert OrchestrationPipeline is not None

    def test_yolo_module_loads(self):
        from orchestrator.yolo import YoloEngine
        assert YoloEngine is not None

    def test_tui_session_loads(self):
        from tui.session import ChatSession
        assert ChatSession is not None

    def test_tui_commands_loads(self):
        from tui.commands import CommandRouter
        router = CommandRouter()
        # Verify new commands are registered alongside old ones
        help_text = router.help_text()
        assert "/help" in help_text
        assert "/skills" in help_text
        assert "/context" in help_text
        assert "/remember" in help_text
        assert "/openclaw" in help_text
        assert "/detect" in help_text
        assert "/yolo" in help_text

    def test_config_loader_loads(self):
        from config.config_loader import ClawsmithConfig
        assert ClawsmithConfig is not None

    def test_memory_reader_loads(self):
        from memory_skill.reader import MemoryReader
        assert MemoryReader is not None

    def test_scope_engine_loads(self):
        from scope_engine.engine import ScopeEngine
        assert ScopeEngine is not None

    def test_agent_registry_loads(self):
        from agents.registry import AgentRegistry
        assert AgentRegistry is not None
