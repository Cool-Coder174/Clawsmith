"""Tests for the typed memory subsystem — entry model, always-remember,
retrieval, ranking, promotion, suppression, and decay.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from memory_skill.always_remember import AlwaysRemember
from memory_skill.retriever import (
    MemoryEntry,
    MemoryRetriever,
    RetrievalResult,
    _acceptance_factor,
    _recency_factor,
    rank_entries,
)


# ===================================================================
# MemoryEntry (typed model)
# ===================================================================


class TestMemoryEntry:
    def test_defaults(self):
        e = MemoryEntry(content="hello")
        assert e.source == ""
        assert e.category == ""
        assert e.repo == ""
        assert e.workspace == ""
        assert e.dependency_stack == []
        assert e.workflow_type == ""
        assert e.task_category == ""
        assert e.hit_count == 0
        assert e.accept_count == 0
        assert e.usefulness_score == 0.0
        assert e.suppressed is False
        assert e.relevance == 0.0

    def test_all_dimensions(self):
        e = MemoryEntry(
            content="Run pytest -x for fast failures",
            source="always_remember",
            category="workflow",
            repo="/home/user/myrepo",
            workspace="/home/user",
            dependency_stack=["python", "pytest"],
            workflow_type="test",
            task_category="bugfix",
            created_at="2026-03-16T00:00:00+00:00",
            last_accessed_at="2026-03-16T01:00:00+00:00",
            hit_count=10,
            accept_count=8,
            usefulness_score=0.9,
            tags=["testing", "python"],
        )
        assert e.dependency_stack == ["python", "pytest"]
        assert e.workflow_type == "test"
        assert e.task_category == "bugfix"
        assert e.usefulness_score == 0.9

    def test_json_roundtrip(self):
        e = MemoryEntry(
            content="test", source="repo", dependency_stack=["python"],
            hit_count=5, accept_count=3, suppressed=True,
        )
        restored = MemoryEntry.model_validate_json(e.model_dump_json())
        assert restored.dependency_stack == ["python"]
        assert restored.hit_count == 5
        assert restored.suppressed is True


# ===================================================================
# AlwaysRemember — typed storage
# ===================================================================


class TestAlwaysRemember:
    def test_remember_stores_all_dimensions(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.remember(
            "Use pytest -x", category="workflow",
            tags=["testing"],
            dependency_stack=["python", "pytest"],
            workflow_type="test",
            task_category="debug",
        )
        entry = ar.get(eid)
        assert entry is not None
        assert entry["dependency_stack"] == ["python", "pytest"]
        assert entry["workflow_type"] == "test"
        assert entry["task_category"] == "debug"
        assert entry["hit_count"] == 0
        assert entry["accept_count"] == 0
        assert entry["suppressed"] is False
        assert entry["created_at"] != ""

    def test_remember_and_list(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        ar.remember("Fact one", tags=["a"])
        ar.remember("Fact two", tags=["b"])
        assert len(ar.list_entries()) == 2

    def test_forget(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.remember("Temporary")
        assert ar.forget(eid) is True
        assert ar.list_entries() == []
        assert ar.forget("nonexistent") is False

    def test_search(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        ar.remember("mypy is slow", tags=["typing"])
        ar.remember("ruff is fast", tags=["linting"])
        assert len(ar.search("mypy")) == 1
        assert len(ar.search("linting")) == 1

    def test_persistence(self, tmp_path: Path):
        AlwaysRemember(tmp_path).remember("Persisted", category="test")
        entries = AlwaysRemember(tmp_path).list_entries()
        assert len(entries) == 1
        assert entries[0]["content"] == "Persisted"


# ===================================================================
# Suppress / unsuppress
# ===================================================================


class TestSuppression:
    def test_suppress_hides_from_list(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.remember("Noisy entry")
        ar.suppress(eid)

        assert len(ar.list_entries()) == 0
        assert len(ar.list_entries(include_suppressed=True)) == 1

    def test_unsuppress_restores(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.remember("Suppressed then restored")
        ar.suppress(eid)
        assert len(ar.list_entries()) == 0
        ar.unsuppress(eid)
        assert len(ar.list_entries()) == 1

    def test_suppress_nonexistent_returns_false(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        assert ar.suppress("missing") is False

    def test_suppressed_entries_filtered_by_retriever(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.remember("Suppressed memory", tags=["testing"])
        ar.suppress(eid)
        ar.remember("Visible memory", tags=["testing"])

        retriever = MemoryRetriever(tmp_path)
        result = retriever.retrieve("testing")
        contents = [e.content for e in result.entries]
        assert "Visible memory" in contents
        assert "Suppressed memory" not in contents
        assert result.suppressed_count >= 1


# ===================================================================
# Promotion — accepted outcomes → durable memory
# ===================================================================


class TestPromotion:
    def test_promote_creates_entry(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.promote_outcome(
            "Fix: add --no-cache flag to docker build",
            category="accepted_outcome",
            tags=["docker"],
            dependency_stack=["docker"],
            workflow_type="build",
            usefulness_score=0.8,
        )
        entry = ar.get(eid)
        assert entry is not None
        assert entry["accept_count"] == 1
        assert entry["hit_count"] == 1
        assert entry["usefulness_score"] == 0.8
        assert entry["workflow_type"] == "build"

    def test_promote_increments_existing(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid1 = ar.promote_outcome("Fix: add --no-cache", category="accepted_outcome")
        eid2 = ar.promote_outcome("Fix: add --no-cache", category="accepted_outcome")
        assert eid1 == eid2
        entry = ar.get(eid1)
        assert entry["accept_count"] == 2
        assert entry["hit_count"] == 2
        assert entry["usefulness_score"] >= 0.8

    def test_promote_unsuppresses(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.promote_outcome("Some fact")
        ar.suppress(eid)
        assert ar.get(eid)["suppressed"] is True
        ar.promote_outcome("Some fact")
        assert ar.get(eid)["suppressed"] is False


# ===================================================================
# Acceptance tracking
# ===================================================================


class TestAcceptanceTracking:
    def test_record_hit(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.remember("Test memory")
        ar.record_hit(eid)
        ar.record_hit(eid)
        entry = ar.get(eid)
        assert entry["hit_count"] == 2

    def test_record_accept(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.remember("Test memory")
        ar.record_hit(eid)
        ar.record_accept(eid)
        entry = ar.get(eid)
        assert entry["accept_count"] == 1
        assert entry["usefulness_score"] > 0

    def test_usefulness_caps_at_one(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.remember("Test memory")
        for _ in range(20):
            ar.record_accept(eid)
        entry = ar.get(eid)
        assert entry["usefulness_score"] <= 1.0


# ===================================================================
# Decay — auto-suppress low-value entries
# ===================================================================


class TestDecay:
    def test_decay_suppresses_noisy_entries(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.remember("Noisy entry")
        entry = ar.get(eid)
        entry["hit_count"] = 10
        entry["accept_count"] = 0
        ar._write(eid, entry)

        suppressed = ar.decay(min_hits=5, max_reject_ratio=0.8)
        assert eid in suppressed
        assert ar.get(eid)["suppressed"] is True

    def test_decay_spares_useful_entries(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.remember("Useful entry")
        entry = ar.get(eid)
        entry["hit_count"] = 10
        entry["accept_count"] = 8
        ar._write(eid, entry)

        suppressed = ar.decay(min_hits=5)
        assert eid not in suppressed

    def test_decay_spares_low_hit_entries(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.remember("New entry")
        entry = ar.get(eid)
        entry["hit_count"] = 2
        entry["accept_count"] = 0
        ar._write(eid, entry)

        suppressed = ar.decay(min_hits=5)
        assert eid not in suppressed


# ===================================================================
# Ranker — multi-dimensional scoring
# ===================================================================


class TestRanker:
    def test_token_overlap_scores(self):
        entries = [
            MemoryEntry(content="Use pytest -x for fast test failures", source="always_remember"),
            MemoryEntry(content="Docker compose is great", source="always_remember"),
        ]
        ranked = rank_entries(entries, "fix the pytest test failures")
        assert ranked[0].content.startswith("Use pytest")
        assert ranked[0].relevance > ranked[1].relevance

    def test_dependency_stack_match(self):
        entries = [
            MemoryEntry(content="Python config", source="repo", dependency_stack=["python"]),
            MemoryEntry(content="Go config", source="repo", dependency_stack=["go"]),
        ]
        ranked = rank_entries(entries, "fix something", task_stacks=["python"])
        assert ranked[0].dependency_stack == ["python"]

    def test_workflow_type_match(self):
        entries = [
            MemoryEntry(content="Build config", source="repo", workflow_type="build"),
            MemoryEntry(content="Test config", source="repo", workflow_type="test"),
        ]
        ranked = rank_entries(entries, "fix the build", task_workflow="build")
        assert ranked[0].workflow_type == "build"

    def test_task_category_match(self):
        entries = [
            MemoryEntry(content="Bugfix pattern", source="repo", task_category="bugfix"),
            MemoryEntry(content="Refactor pattern", source="repo", task_category="refactor"),
        ]
        ranked = rank_entries(entries, "fix the bug", task_category="bugfix")
        assert ranked[0].task_category == "bugfix"

    def test_repo_proximity_boost(self):
        entries = [
            MemoryEntry(content="Note A", source="repo", repo="/repo/a"),
            MemoryEntry(content="Note B", source="cross_repo", repo="/repo/b"),
        ]
        ranked = rank_entries(entries, "some task", repo_path="/repo/a")
        assert ranked[0].repo == "/repo/a"

    def test_acceptance_boost(self):
        entries = [
            MemoryEntry(content="Useful fact", source="always_remember", usefulness_score=0.9),
            MemoryEntry(content="Unused fact", source="always_remember", usefulness_score=0.0),
        ]
        ranked = rank_entries(entries, "generic task")
        assert ranked[0].usefulness_score > ranked[1].usefulness_score

    def test_suppressed_entries_excluded(self):
        entries = [
            MemoryEntry(content="Visible", source="repo"),
            MemoryEntry(content="Suppressed", source="repo", suppressed=True),
        ]
        ranked = rank_entries(entries, "anything")
        assert len(ranked) == 1
        assert ranked[0].content == "Visible"

    def test_recency_factor_recent_is_high(self):
        now = datetime.now(UTC).isoformat()
        assert _recency_factor(now) > 0.9

    def test_recency_factor_old_is_low(self):
        old = (datetime.now(UTC) - timedelta(days=180)).isoformat()
        assert _recency_factor(old) < 0.1

    def test_recency_factor_empty_is_zero(self):
        assert _recency_factor("") == 0.0

    def test_acceptance_factor_with_score(self):
        e = MemoryEntry(content="x", usefulness_score=0.7)
        assert _acceptance_factor(e) == 0.7

    def test_acceptance_factor_from_ratio(self):
        e = MemoryEntry(content="x", hit_count=10, accept_count=8)
        assert _acceptance_factor(e) > 0.5

    def test_acceptance_factor_zero(self):
        e = MemoryEntry(content="x")
        assert _acceptance_factor(e) == 0.0

    def test_explainability_populated(self):
        entries = [MemoryEntry(content="python testing", source="always_remember", tags=["testing"])]
        ranked = rank_entries(entries, "fix the testing issue")
        assert ranked[0].explanation != ""
        assert "tokens" in ranked[0].explanation or "tags" in ranked[0].explanation


# ===================================================================
# Retriever end-to-end
# ===================================================================


class TestMemoryRetriever:
    def test_retrieve_empty(self, tmp_path: Path):
        result = MemoryRetriever(tmp_path).retrieve("fix the auth bug")
        assert isinstance(result, RetrievalResult)
        assert result.total_candidates == 0

    def test_retrieve_with_always_remember(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        ar.remember("Use pytest -x for fast test failures", tags=["testing"],
                     dependency_stack=["python", "pytest"], workflow_type="test")
        ar.remember("Database connection string is in .env", tags=["config"])

        result = MemoryRetriever(tmp_path).retrieve(
            "fix the failing tests", task_stacks=["python", "pytest"], task_workflow="test",
        )
        assert result.total_candidates >= 2
        top = result.entries[0]
        assert "test" in top.content.lower()

    def test_ranking_with_typed_dimensions(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        ar.remember("FastAPI uses Pydantic for validation",
                     tags=["fastapi"], dependency_stack=["python", "fastapi"])
        ar.remember("Always run tests before merging",
                     tags=["testing", "workflow"], workflow_type="test")
        ar.remember("Docker compose is used for local dev",
                     tags=["docker"], dependency_stack=["docker"])

        result = MemoryRetriever(tmp_path).retrieve(
            "fix the pytest test failures",
            task_stacks=["python", "pytest"],
            task_workflow="test",
        )
        assert len(result.entries) > 0
        assert result.entries[0].relevance > 0

    def test_repo_memory_loaded(self, tmp_path: Path):
        clawsmith_dir = tmp_path / "clawsmith"
        clawsmith_dir.mkdir(parents=True)
        (clawsmith_dir / "architecture.json").write_text(json.dumps({
            "hardware_tier": "workstation", "os_name": "Linux", "os_version": "6.1",
            "cpu_summary": "AMD Ryzen 9", "ram_gb": 64.0, "gpu_summary": "RTX 4090",
            "vram_gb": 24.0, "installed_models": [], "installed_runtimes": [],
            "approved_agent_clis": [], "repos": [], "mutation_permissions": [],
        }), encoding="utf-8")

        result = MemoryRetriever(tmp_path).retrieve("hardware")
        arch = [e for e in result.entries if e.category == "architecture"]
        assert len(arch) > 0

    def test_max_entries_respected(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        for i in range(20):
            ar.remember(f"Entry {i}", tags=[f"tag{i}"])
        result = MemoryRetriever(tmp_path).retrieve("memory", max_entries=5)
        assert len(result.entries) <= 5
        assert result.total_candidates >= 20

    def test_suppressed_count_reported(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        eid = ar.remember("Noisy")
        ar.suppress(eid)
        ar.remember("Clean")
        result = MemoryRetriever(tmp_path).retrieve("something")
        assert result.suppressed_count >= 1

    def test_retrieval_explainability(self, tmp_path: Path):
        ar = AlwaysRemember(tmp_path)
        ar.remember("pytest config notes", tags=["testing"])
        result = MemoryRetriever(tmp_path).retrieve("testing setup")
        assert result.explanation != ""
        assert "Retrieved" in result.explanation


# ===================================================================
# ChatRuntime integration
# ===================================================================


class TestChatRuntimeMemory:
    def test_promote_outcome(self, tmp_path: Path):
        from orchestrator.chat_runtime import ChatRuntime

        rt = ChatRuntime(repo_path=tmp_path)
        eid = rt.promote_outcome("Fix: add timeout to HTTP calls", task_category="bugfix")
        entries = rt.list_memories()
        assert any(e["id"] == eid for e in entries)
        match = [e for e in entries if e["id"] == eid][0]
        assert match["accept_count"] >= 1
        assert match["usefulness_score"] > 0

    def test_suppress_and_unsuppress(self, tmp_path: Path):
        from orchestrator.chat_runtime import ChatRuntime

        rt = ChatRuntime(repo_path=tmp_path)
        eid = rt.remember("Noisy fact")
        assert rt.suppress_memory(eid) is True
        assert len(rt.list_memories()) == 0
        assert rt.unsuppress_memory(eid) is True
        assert len(rt.list_memories()) == 1

    def test_decay(self, tmp_path: Path):
        from orchestrator.chat_runtime import ChatRuntime

        ar = AlwaysRemember(tmp_path)
        eid = ar.remember("Spammy")
        entry = ar.get(eid)
        entry["hit_count"] = 20
        entry["accept_count"] = 0
        ar._write(eid, entry)

        rt = ChatRuntime(repo_path=tmp_path)
        suppressed = rt.decay_memories()
        assert eid in suppressed

    def test_retrieve_memories_for_passes_stacks(self, tmp_path: Path):
        from orchestrator.chat_runtime import ChatRuntime

        ar = AlwaysRemember(tmp_path)
        ar.remember("Python tip", dependency_stack=["python"])

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\n', encoding="utf-8"
        )
        rt = ChatRuntime(repo_path=tmp_path)
        rt.initialize()
        mems = rt.retrieve_memories_for("python task")
        assert isinstance(mems, list)
