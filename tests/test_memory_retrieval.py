"""Tests for memory retrieval, ranking, and always-remember."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from memory_skill.always_remember import AlwaysRemember
from memory_skill.retriever import MemoryEntry, MemoryRetriever, RetrievalResult


# ---------------------------------------------------------------------------
# AlwaysRemember
# ---------------------------------------------------------------------------


class TestAlwaysRemember:
    def test_remember_and_list(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        entry_id = ar.remember("Use pytest -x for fast failures", category="workflow")
        entries = ar.list_entries()

        assert len(entries) == 1
        assert entries[0]["id"] == entry_id
        assert entries[0]["category"] == "workflow"
        assert "pytest" in entries[0]["content"]

    def test_remember_multiple(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        ar.remember("Fact one", tags=["important"])
        ar.remember("Fact two", tags=["secondary"])

        entries = ar.list_entries()
        assert len(entries) == 2

    def test_forget(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.remember("Temporary note")
        assert ar.forget(eid) is True
        assert len(ar.list_entries()) == 0
        assert ar.forget("nonexistent") is False

    def test_search(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        ar.remember("Always run mypy before commit", tags=["typing"])
        ar.remember("Use ruff for linting", tags=["linting"])

        results = ar.search("mypy")
        assert len(results) == 1
        assert "mypy" in results[0]["content"]

        results = ar.search("linting")
        assert len(results) == 1

    def test_persistence(self, tmp_path: Path):
        ar1 = AlwaysRemember(tmp_path)
        ar1.remember("Persisted note", category="test")

        ar2 = AlwaysRemember(tmp_path)
        entries = ar2.list_entries()
        assert len(entries) == 1
        assert entries[0]["content"] == "Persisted note"

    def test_storage_dir(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        ar.remember("Something")
        expected = tmp_path / ".clawsmith" / "always_remember"
        assert expected.exists()
        assert len(list(expected.glob("*.json"))) == 1


# ---------------------------------------------------------------------------
# MemoryRetriever
# ---------------------------------------------------------------------------


class TestMemoryRetriever:
    def test_retrieve_empty(self, tmp_path: Path):
        retriever = MemoryRetriever(tmp_path)
        result = retriever.retrieve("fix the auth bug")
        assert isinstance(result, RetrievalResult)
        assert result.task_description == "fix the auth bug"

    def test_retrieve_with_always_remember(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        ar.remember("Use pytest -x for fast test failures", tags=["testing"])
        ar.remember("Database connection string is in .env", tags=["config"])

        retriever = MemoryRetriever(tmp_path)
        result = retriever.retrieve("fix the failing tests")

        assert result.total_candidates >= 2
        testing_entries = [e for e in result.entries if "test" in e.content.lower()]
        assert len(testing_entries) > 0

    def test_ranking_prefers_relevant_entries(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        ar.remember("FastAPI uses Pydantic for validation", tags=["fastapi"])
        ar.remember("Always run tests before merging", tags=["testing", "workflow"])
        ar.remember("Docker compose is used for local dev", tags=["docker"])

        retriever = MemoryRetriever(tmp_path)
        result = retriever.retrieve("fix the pytest test failures")

        assert len(result.entries) > 0
        top = result.entries[0]
        assert top.relevance > 0

    def test_retrieve_with_repo_memory(self, tmp_path: Path):
        clawsmith_dir = tmp_path / "clawsmith"
        clawsmith_dir.mkdir(parents=True)
        (clawsmith_dir / "architecture.json").write_text(json.dumps({
            "hardware_tier": "workstation",
            "os_name": "Linux",
            "os_version": "6.1",
            "cpu_summary": "AMD Ryzen 9",
            "ram_gb": 64.0,
            "gpu_summary": "NVIDIA RTX 4090",
            "vram_gb": 24.0,
            "installed_models": [],
            "installed_runtimes": [],
            "approved_agent_clis": [],
            "repos": [],
            "mutation_permissions": [],
        }), encoding="utf-8")

        retriever = MemoryRetriever(tmp_path)
        result = retriever.retrieve("hardware")

        arch_entries = [e for e in result.entries if e.category == "architecture"]
        assert len(arch_entries) > 0

    def test_cross_repo_flag(self, tmp_path: Path):
        retriever = MemoryRetriever(tmp_path)
        result_with = retriever.retrieve("test", include_cross_repo=True)
        result_without = retriever.retrieve("test", include_cross_repo=False)
        assert isinstance(result_with, RetrievalResult)
        assert isinstance(result_without, RetrievalResult)

    def test_max_entries_respected(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        for i in range(20):
            ar.remember(f"Memory entry {i}", tags=[f"tag{i}"])

        retriever = MemoryRetriever(tmp_path)
        result = retriever.retrieve("memory", max_entries=5)
        assert len(result.entries) <= 5
        assert result.total_candidates >= 20

    def test_retrieval_explainability(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        ar.remember("pytest configuration notes", tags=["testing"])

        retriever = MemoryRetriever(tmp_path)
        result = retriever.retrieve("testing setup")
        assert result.explanation != ""
