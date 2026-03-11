from __future__ import annotations

import json
from pathlib import Path

from config.config_loader import load_config
from jobs.bat_generator import BatGenerator
from orchestrator.schemas import JobSpec, TaskType


def _setup(monkeypatch, tmp_path, sample_config_yaml):
    """Point generated dir and repo root at temp locations and load config."""
    import jobs.bat_generator as mod

    monkeypatch.setattr(mod, "_GENERATED_DIR", tmp_path / "generated")
    monkeypatch.setattr(mod, "_REPO_ROOT", tmp_path)
    load_config(sample_config_yaml)


def test_generates_bat_file(sample_job_spec, tmp_path, monkeypatch, sample_config_yaml):
    _setup(monkeypatch, tmp_path, sample_config_yaml)
    bat_path = BatGenerator().generate(sample_job_spec)
    assert bat_path.exists()
    assert bat_path.suffix == ".bat"


def test_bat_contains_working_directory(sample_job_spec, tmp_path, monkeypatch, sample_config_yaml):
    _setup(monkeypatch, tmp_path, sample_config_yaml)
    bat_path = BatGenerator().generate(sample_job_spec)
    content = bat_path.read_text(encoding="utf-8")
    assert sample_job_spec.working_directory in content


def test_bat_contains_build_commands(tmp_path, monkeypatch, tmp_repo, sample_config_yaml):
    _setup(monkeypatch, tmp_path, sample_config_yaml)
    job = JobSpec(
        task_type=TaskType.bugfix,
        objective="test",
        working_directory=str(tmp_repo),
        build_commands=["pytest"],
        test_commands=[],
        prompt="test",
        dry_run=True,
    )
    bat_path = BatGenerator().generate(job)
    content = bat_path.read_text(encoding="utf-8")
    assert "pytest" in content


def test_bat_contains_log_redirection(sample_job_spec, tmp_path, monkeypatch, sample_config_yaml):
    _setup(monkeypatch, tmp_path, sample_config_yaml)
    bat_path = BatGenerator().generate(sample_job_spec)
    content = bat_path.read_text(encoding="utf-8")
    assert "stdout.log" in content
    assert "stderr.log" in content


def test_bat_contains_echo_off(sample_job_spec, tmp_path, monkeypatch, sample_config_yaml):
    _setup(monkeypatch, tmp_path, sample_config_yaml)
    bat_path = BatGenerator().generate(sample_job_spec)
    content = bat_path.read_text(encoding="utf-8")
    assert content.startswith("@echo off")


def test_metadata_json_written(sample_job_spec, tmp_path, monkeypatch, sample_config_yaml):
    _setup(monkeypatch, tmp_path, sample_config_yaml)
    BatGenerator().generate(sample_job_spec)
    artifact_dir = tmp_path / "artifacts" / sample_job_spec.id
    meta_path = artifact_dir / "metadata.json"
    assert meta_path.exists()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert meta["job"]["id"] == sample_job_spec.id
