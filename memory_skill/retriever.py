"""Memory retriever — typed, ranked, explainable memory selection.

"Always remember" does **not** mean "dump everything into context".
It means: durable storage → ranked retrieval → explainable selection.

Every ``MemoryEntry`` carries typed dimensions so the ranker can weight
them against the current task:

- **repo** — which repository this memory belongs to
- **workspace** — which workspace (multi-repo root) it belongs to
- **dependency_stack** — language/framework stack tags (python, fastapi, …)
- **workflow_type** — what kind of workflow produced it (build, test, lint, …)
- **task_category** — the broad task type (bugfix, refactor, debug, …)
- **recency** — ``created_at`` and ``last_accessed_at`` timestamps
- **acceptance** — ``hit_count``, ``accept_count``, ``usefulness_score``
- **suppressed** — explicitly suppressed entries are filtered out
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from orchestrator.logging_setup import get_logger

log = get_logger("memory.retriever")

_EPOCH = datetime(2024, 1, 1, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Typed memory entry
# ---------------------------------------------------------------------------


class MemoryEntry(BaseModel):
    """A single retrievable memory item with typed dimensions."""

    # identity
    id: str = ""
    content: str
    source: str = ""
    category: str = ""

    # typed dimensions
    repo: str = ""
    workspace: str = ""
    dependency_stack: list[str] = Field(default_factory=list)
    workflow_type: str = ""
    task_category: str = ""

    # recency
    created_at: str = ""
    last_accessed_at: str = ""

    # acceptance / usefulness
    hit_count: int = 0
    accept_count: int = 0
    usefulness_score: float = 0.0

    # suppression
    suppressed: bool = False

    # tags (flat, for backward compat and free-form tagging)
    tags: list[str] = Field(default_factory=list)

    # scoring output (filled by the ranker)
    relevance: float = 0.0
    explanation: str = ""


# ---------------------------------------------------------------------------
# Retrieval result
# ---------------------------------------------------------------------------


class RetrievalResult(BaseModel):
    """Result of a retrieval pass — includes what was selected *and why*."""

    task_description: str
    entries: list[MemoryEntry] = Field(default_factory=list)
    total_candidates: int = 0
    suppressed_count: int = 0
    explanation: str = ""


# ---------------------------------------------------------------------------
# Ranker weights (tunable)
# ---------------------------------------------------------------------------

_W_TOKEN_OVERLAP = 0.05
_W_TOKEN_CAP = 0.25
_W_TAG_OVERLAP = 0.08
_W_STACK_MATCH = 0.12
_W_WORKFLOW_MATCH = 0.10
_W_TASK_CAT_MATCH = 0.10
_W_SOURCE_ALWAYS = 0.10
_W_SOURCE_REPO = 0.06
_W_SOURCE_CROSS = 0.03
_W_REPO_MATCH = 0.12
_W_ACCEPTANCE = 0.15
_RECENCY_HALF_LIFE_DAYS = 30.0


def _tokenize(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"\w+", text)}


def _recency_factor(iso_ts: str) -> float:
    """Exponential decay: 1.0 at now, 0.5 after ``_RECENCY_HALF_LIFE_DAYS``."""
    if not iso_ts:
        return 0.0
    try:
        ts = datetime.fromisoformat(iso_ts)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        age_days = max((datetime.now(UTC) - ts).total_seconds() / 86400, 0.0)
        return math.pow(0.5, age_days / _RECENCY_HALF_LIFE_DAYS)
    except Exception:
        return 0.0


def _acceptance_factor(entry: MemoryEntry) -> float:
    """0–1 score derived from explicit usefulness + accept/hit ratio."""
    if entry.usefulness_score > 0:
        return min(entry.usefulness_score, 1.0)
    if entry.hit_count > 0:
        return min(entry.accept_count / entry.hit_count, 1.0) * 0.8
    return 0.0


# ---------------------------------------------------------------------------
# Ranker
# ---------------------------------------------------------------------------


def rank_entries(
    candidates: list[MemoryEntry],
    task: str,
    *,
    repo_path: str = "",
    task_stacks: list[str] | None = None,
    task_workflow: str = "",
    task_category: str = "",
) -> list[MemoryEntry]:
    """Score, sort, and annotate *candidates* for *task*.

    Suppressed entries are **excluded** — they never appear in results.
    """
    task_tokens = _tokenize(task)
    stack_set = {s.lower() for s in (task_stacks or [])}

    live: list[MemoryEntry] = []
    for entry in candidates:
        if entry.suppressed:
            continue

        score = 0.0
        reasons: list[str] = []

        # 1. token overlap
        entry_tokens = _tokenize(entry.content)
        overlap = task_tokens & entry_tokens
        if overlap:
            tok_score = min(len(overlap) * _W_TOKEN_OVERLAP, _W_TOKEN_CAP)
            score += tok_score
            reasons.append(f"tokens({len(overlap)})")

        # 2. tag overlap
        tag_set = {t.lower() for t in entry.tags}
        tag_overlap = task_tokens & tag_set
        if tag_overlap:
            score += len(tag_overlap) * _W_TAG_OVERLAP
            reasons.append(f"tags({','.join(sorted(tag_overlap)[:3])})")

        # 3. dependency-stack match
        entry_stacks = {s.lower() for s in entry.dependency_stack}
        stack_overlap = stack_set & entry_stacks
        if stack_overlap:
            score += len(stack_overlap) * _W_STACK_MATCH
            reasons.append(f"stack({','.join(sorted(stack_overlap)[:3])})")

        # 4. workflow-type match
        if task_workflow and entry.workflow_type and task_workflow.lower() == entry.workflow_type.lower():
            score += _W_WORKFLOW_MATCH
            reasons.append(f"workflow={task_workflow}")

        # 5. task-category match
        if task_category and entry.task_category and task_category.lower() == entry.task_category.lower():
            score += _W_TASK_CAT_MATCH
            reasons.append(f"category={task_category}")

        # 6. source boost
        if entry.source == "always_remember":
            score += _W_SOURCE_ALWAYS
        elif entry.source == "repo":
            score += _W_SOURCE_REPO
        elif entry.source == "cross_repo":
            score += _W_SOURCE_CROSS

        # 7. repo proximity
        if repo_path and entry.repo == repo_path:
            score += _W_REPO_MATCH
            reasons.append("same_repo")

        # 8. recency
        ts = entry.last_accessed_at or entry.created_at
        recency = _recency_factor(ts)
        if recency > 0:
            score += recency * 0.10
            reasons.append(f"recency={recency:.2f}")

        # 9. acceptance / usefulness
        acc = _acceptance_factor(entry)
        if acc > 0:
            score += acc * _W_ACCEPTANCE
            reasons.append(f"accept={acc:.2f}")

        entry.relevance = round(min(score, 1.0), 4)
        entry.explanation = " | ".join(reasons) if reasons else "baseline"
        live.append(entry)

    live.sort(key=lambda e: e.relevance, reverse=True)
    return live


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------


class MemoryRetriever:
    """Loads candidates from all memory sources, ranks them, and returns
    only the top-*k* with explainability metadata.
    """

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def retrieve(
        self,
        task: str,
        repo_path: Path | None = None,
        max_entries: int = 10,
        include_cross_repo: bool = True,
        task_stacks: list[str] | None = None,
        task_workflow: str = "",
        task_category: str = "",
    ) -> RetrievalResult:
        """Retrieve ranked, non-suppressed memories relevant to *task*."""
        effective_repo = (repo_path or self.workspace_root).resolve()
        candidates: list[MemoryEntry] = []

        candidates.extend(self._load_repo_memory(effective_repo))
        candidates.extend(self._load_always_remember())

        if include_cross_repo:
            candidates.extend(self._load_cross_repo_memory(effective_repo))

        total = len(candidates)
        suppressed = sum(1 for c in candidates if c.suppressed)

        ranked = rank_entries(
            candidates,
            task,
            repo_path=str(effective_repo),
            task_stacks=task_stacks,
            task_workflow=task_workflow,
            task_category=task_category,
        )
        selected = ranked[:max_entries]

        parts = [f"Retrieved {len(selected)}/{total} entries ({suppressed} suppressed)."]
        if selected:
            top = selected[0]
            parts.append(
                f"Top: [{top.category}] {top.content[:60]}… "
                f"(relevance={top.relevance:.3f}, {top.explanation})"
            )

        return RetrievalResult(
            task_description=task,
            entries=selected,
            total_candidates=total,
            suppressed_count=suppressed,
            explanation=" ".join(parts),
        )

    # -- loaders -----------------------------------------------------------

    def _load_repo_memory(self, repo_path: Path) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        clawsmith_dir = repo_path / "clawsmith"
        if not clawsmith_dir.exists():
            return entries

        from memory_skill.reader import MemoryReader

        reader = MemoryReader(repo_path)
        repo_str = str(repo_path)

        arch = reader.read_architecture()
        if arch:
            entries.append(MemoryEntry(
                source="repo",
                category="architecture",
                content=(
                    f"Hardware: {arch.hardware_tier}, OS: {arch.os_name} {arch.os_version}, "
                    f"CPU: {arch.cpu_summary}, RAM: {arch.ram_gb}GB, GPU: {arch.gpu_summary}"
                ),
                repo=repo_str,
                workspace=repo_str,
                tags=["hardware", "architecture"],
            ))
            if arch.repos:
                repo_names = [r.name for r in arch.repos]
                entries.append(MemoryEntry(
                    source="repo",
                    category="workspace",
                    content=f"Known repos: {', '.join(repo_names)}",
                    repo=repo_str,
                    workspace=repo_str,
                    tags=["repos", "workspace"],
                ))

        prefs = reader.read_preferences()
        if prefs:
            for conv in prefs.coding_conventions:
                entries.append(MemoryEntry(
                    source="repo",
                    category="convention",
                    content=f"[{conv.language}] {conv.convention}",
                    repo=repo_str,
                    dependency_stack=[conv.language],
                    tags=["convention", conv.language],
                ))
            for note in prefs.stack_notes:
                entries.append(MemoryEntry(
                    source="repo",
                    category="stack_note",
                    content=f"{note.key}: {note.value}",
                    repo=repo_str,
                    dependency_stack=[note.key],
                    tags=["stack", note.key],
                ))
            for lang, cmds in prefs.build_commands.items():
                entries.append(MemoryEntry(
                    source="repo",
                    category="build_command",
                    content=f"[{lang}] build: {', '.join(cmds)}",
                    repo=repo_str,
                    dependency_stack=[lang],
                    workflow_type="build",
                    tags=["build", lang],
                ))
            for lang, cmds in prefs.test_commands.items():
                entries.append(MemoryEntry(
                    source="repo",
                    category="test_command",
                    content=f"[{lang}] test: {', '.join(cmds)}",
                    repo=repo_str,
                    dependency_stack=[lang],
                    workflow_type="test",
                    tags=["test", lang],
                ))

        tooling = reader.read_tooling_profile()
        if tooling and tooling.ai_tooling:
            tools_str = ", ".join(f"{k} {v}" for k, v in tooling.ai_tooling.items())
            entries.append(MemoryEntry(
                source="repo",
                category="tooling",
                content=f"AI tooling: {tools_str}",
                repo=repo_str,
                tags=["tooling", "ai"],
            ))

        memory_md = reader.read_memory_md()
        if memory_md:
            entries.append(MemoryEntry(
                source="repo",
                category="memory_md",
                content=memory_md[:2000],
                repo=repo_str,
                tags=["memory", "summary"],
            ))

        return entries

    def _load_always_remember(self) -> list[MemoryEntry]:
        from memory_skill.always_remember import AlwaysRemember

        ar = AlwaysRemember(self.workspace_root)
        entries: list[MemoryEntry] = []
        for raw in ar.list_entries(include_suppressed=True):
            entries.append(MemoryEntry(
                id=raw.get("id", ""),
                source="always_remember",
                category=raw.get("category", "note"),
                content=raw.get("content", ""),
                repo=raw.get("repo_path", ""),
                workspace=raw.get("workspace", ""),
                dependency_stack=raw.get("dependency_stack", []),
                workflow_type=raw.get("workflow_type", ""),
                task_category=raw.get("task_category", ""),
                created_at=raw.get("created_at", ""),
                last_accessed_at=raw.get("last_accessed_at", ""),
                hit_count=raw.get("hit_count", 0),
                accept_count=raw.get("accept_count", 0),
                usefulness_score=raw.get("usefulness_score", 0.0),
                suppressed=raw.get("suppressed", False),
                tags=raw.get("tags", []),
            ))
        return entries

    def _load_cross_repo_memory(self, current_repo: Path) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        try:
            from repo_graph.linker import RepoLinker

            linker = RepoLinker(self.workspace_root)
            graph = linker.load()
            if not graph:
                return entries

            for repo_node in graph.repos:
                repo_p = Path(repo_node.path).resolve()
                if repo_p == current_repo.resolve():
                    continue
                if not repo_p.exists():
                    continue

                clawsmith_dir = repo_p / "clawsmith"
                if clawsmith_dir.exists():
                    from memory_skill.reader import MemoryReader

                    reader = MemoryReader(repo_p)
                    prefs = reader.read_preferences()
                    if prefs and prefs.stack_notes:
                        for note in prefs.stack_notes:
                            entries.append(MemoryEntry(
                                source="cross_repo",
                                category="stack_note",
                                content=f"[{repo_node.name}] {note.key}: {note.value}",
                                repo=str(repo_p),
                                dependency_stack=[note.key],
                                tags=["cross_repo", repo_node.name, note.key],
                            ))
        except Exception as exc:
            log.debug("Cross-repo memory load failed (non-fatal): %s", exc)

        return entries
