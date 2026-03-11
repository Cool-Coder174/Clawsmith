from __future__ import annotations

from pathlib import Path

import pytest

from config.config_loader import reset_config
from orchestrator.schemas import ContextPacket, JobSpec, TaskType


@pytest.fixture(autouse=True)
def reset_cfg():
    """Clear the config singleton before every test to avoid bleed-through."""
    reset_config()


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    """Create a minimal repository layout in a temp directory."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'sample'\nversion = '0.1.0'\n\n"
        "[tool.pytest.ini_options]\ntestpaths = ['tests']\n\n"
        "[tool.ruff]\nline-length = 88\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        '{"name": "sample", "scripts": {"test": "jest", "build": "tsc"}, '
        '"devDependencies": {"jest": "^29.0.0", "typescript": "^5.0.0"}}',
        encoding="utf-8",
    )
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "def test_placeholder():\n    assert True\n", encoding="utf-8"
    )

    (tmp_path / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def sample_job_spec(tmp_repo: Path) -> JobSpec:
    return JobSpec(
        task_type=TaskType.bugfix,
        objective="Fix the login bug",
        working_directory=str(tmp_repo),
        build_commands=["pytest"],
        test_commands=[],
        prompt="Fix it",
        dry_run=True,
    )


@pytest.fixture()
def sample_config_yaml(tmp_path: Path) -> Path:
    content = """\
models:
  local_router:
    provider: ollama
    model_name: ollama/mistral
    max_tokens: 1024
    temperature: 0.2
  local_code:
    provider: ollama
    model_name: ollama/codellama
    max_tokens: 4096
    temperature: 0.1
  premium:
    provider: openai
    model_name: openai/gpt-4o
    max_tokens: 8192
    temperature: 0.2
  prompt_polisher:
    provider: openai
    model_name: openai/gpt-4o-mini
    max_tokens: 2048
    temperature: 0.3

routing:
  low_complexity_threshold: 0.35
  high_complexity_threshold: 0.70
  ambiguity_bump_threshold: 0.60

execution:
  default_timeout: 300
  max_retries: 2
  artifacts_dir: artifacts
  logs_dir: logs
  allowed_commands:
    - cursor
    - python
    - pip
    - pytest
    - ruff
    - mypy

mcp_server:
  port: 8765

openclaw:
  skill_name: ClawSmith
  mcp_endpoint: "http://127.0.0.1:8765/sse"
  webhook_secret: ""

agents:
  default_agent: null
  auto_detect: false
"""
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(content, encoding="utf-8")
    return yaml_path


@pytest.fixture()
def sample_context_packet() -> ContextPacket:
    return ContextPacket(
        task_summary="Fix the login bug",
        relevant_files={"src/main.py": "def main(): pass"},
        architecture_summary="Languages: .py (1 file)",
        build_test_commands=["pytest"],
        constraints=["Token budget: 8000"],
        token_estimate=5000,
    )
