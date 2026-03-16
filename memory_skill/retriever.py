"""Memory retriever — fetches relevant memories for a given task context."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.logging_setup import get_logger

log = get_logger("memory.retriever")


@dataclass
class MemoryEntry:
    """A single retrievable memory item."""

    source: str
    category: str
    content: str
    relevance: float = 0.0
    repo_path: str = ""
    tags: list[str] = field(default_factory=list)
    explanation: str = ""


@dataclass
class RetrievalResult:
    """Result of memory retrieval for a task."""

    task_description: str
    entries: list[MemoryEntry] = field(default_factory=list)
    total_candidates: int = 0
    explanation: str = ""


class MemoryRetriever:
    """Retrieves and ranks memory entries relevant to a task."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root.resolve()

    def retrieve(
        self,
        task: str,
        repo_path: Path | None = None,
        max_entries: int = 10,
        include_cross_repo: bool = True,
    ) -> RetrievalResult:
        """Retrieve ranked memories relevant to a task.

        Loads from:
        - Current repo memory (clawsmith/ dir)
        - Always-remember entries (.clawsmith/always_remember/)
        - Cross-repo memory (if enabled and available)
        """
        candidates: list[MemoryEntry] = []

        candidates.extend(self._load_repo_memory(repo_path or self.workspace_root))
        candidates.extend(self._load_always_remember())

        if include_cross_repo:
            candidates.extend(self._load_cross_repo_memory(repo_path or self.workspace_root))

        total = len(candidates)
        ranked = self._rank(candidates, task, repo_path)
        selected = ranked[:max_entries]

        explanation_parts = [f"Retrieved {len(selected)}/{total} memory entries."]
        if selected:
            top = selected[0]
            explanation_parts.append(
                f"Top: [{top.category}] {top.content[:80]}... (relevance={top.relevance:.2f})"
            )

        return RetrievalResult(
            task_description=task,
            entries=selected,
            total_candidates=total,
            explanation=" ".join(explanation_parts),
        )

    def _load_repo_memory(self, repo_path: Path) -> list[MemoryEntry]:
        """Load structured memory from the repo's clawsmith/ directory."""
        entries: list[MemoryEntry] = []
        clawsmith_dir = repo_path / "clawsmith"

        if not clawsmith_dir.exists():
            return entries

        from memory_skill.reader import MemoryReader

        reader = MemoryReader(repo_path)

        arch = reader.read_architecture()
        if arch:
            entries.append(MemoryEntry(
                source="repo",
                category="architecture",
                content=(
                    f"Hardware: {arch.hardware_tier}, OS: {arch.os_name} {arch.os_version}, "
                    f"CPU: {arch.cpu_summary}, RAM: {arch.ram_gb}GB, GPU: {arch.gpu_summary}"
                ),
                repo_path=str(repo_path),
                tags=["hardware", "architecture"],
            ))
            if arch.repos:
                repo_names = [r.name for r in arch.repos]
                entries.append(MemoryEntry(
                    source="repo",
                    category="workspace",
                    content=f"Known repos: {', '.join(repo_names)}",
                    repo_path=str(repo_path),
                    tags=["repos", "workspace"],
                ))

        prefs = reader.read_preferences()
        if prefs:
            if prefs.coding_conventions:
                for conv in prefs.coding_conventions:
                    entries.append(MemoryEntry(
                        source="repo",
                        category="convention",
                        content=f"[{conv.language}] {conv.convention}",
                        repo_path=str(repo_path),
                        tags=["convention", conv.language],
                    ))
            if prefs.stack_notes:
                for note in prefs.stack_notes:
                    entries.append(MemoryEntry(
                        source="repo",
                        category="stack_note",
                        content=f"{note.key}: {note.value}",
                        repo_path=str(repo_path),
                        tags=["stack", note.key],
                    ))
            if prefs.build_commands:
                for lang, cmds in prefs.build_commands.items():
                    entries.append(MemoryEntry(
                        source="repo",
                        category="build_command",
                        content=f"[{lang}] build: {', '.join(cmds)}",
                        repo_path=str(repo_path),
                        tags=["build", lang],
                    ))
            if prefs.test_commands:
                for lang, cmds in prefs.test_commands.items():
                    entries.append(MemoryEntry(
                        source="repo",
                        category="test_command",
                        content=f"[{lang}] test: {', '.join(cmds)}",
                        repo_path=str(repo_path),
                        tags=["test", lang],
                    ))

        tooling = reader.read_tooling_profile()
        if tooling:
            if tooling.ai_tooling:
                tools_str = ", ".join(f"{k} {v}" for k, v in tooling.ai_tooling.items())
                entries.append(MemoryEntry(
                    source="repo",
                    category="tooling",
                    content=f"AI tooling: {tools_str}",
                    repo_path=str(repo_path),
                    tags=["tooling", "ai"],
                ))

        memory_md = reader.read_memory_md()
        if memory_md:
            entries.append(MemoryEntry(
                source="repo",
                category="memory_md",
                content=memory_md[:2000],
                repo_path=str(repo_path),
                tags=["memory", "summary"],
            ))

        return entries

    def _load_always_remember(self) -> list[MemoryEntry]:
        """Load always-remember entries from .clawsmith/always_remember/."""
        import json

        entries: list[MemoryEntry] = []
        ar_dir = self.workspace_root / ".clawsmith" / "always_remember"
        if not ar_dir.exists():
            return entries

        for path in sorted(ar_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                entries.append(MemoryEntry(
                    source="always_remember",
                    category=data.get("category", "note"),
                    content=data.get("content", ""),
                    repo_path=data.get("repo_path", ""),
                    tags=data.get("tags", []),
                ))
            except Exception as exc:
                log.warning("Failed to load always-remember from %s: %s", path, exc)

        return entries

    def _load_cross_repo_memory(self, current_repo: Path) -> list[MemoryEntry]:
        """Load memory from linked repos in the workspace graph."""
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
                                repo_path=str(repo_p),
                                tags=["cross_repo", repo_node.name, note.key],
                            ))

        except Exception as exc:
            log.debug("Cross-repo memory load failed (non-fatal): %s", exc)

        return entries

    def _rank(
        self,
        candidates: list[MemoryEntry],
        task: str,
        repo_path: Path | None = None,
    ) -> list[MemoryEntry]:
        """Rank memory entries by relevance to the task."""
        import re

        task_tokens = {w.lower() for w in re.findall(r"\w+", task)}

        for entry in candidates:
            score = 0.0
            entry_tokens = {w.lower() for w in re.findall(r"\w+", entry.content)}
            tag_set = {t.lower() for t in entry.tags}

            token_overlap = task_tokens & entry_tokens
            if token_overlap:
                score += min(len(token_overlap) * 0.05, 0.3)

            tag_overlap = task_tokens & tag_set
            if tag_overlap:
                score += len(tag_overlap) * 0.1

            if entry.source == "always_remember":
                score += 0.2
            elif entry.source == "repo":
                score += 0.1
            elif entry.source == "cross_repo":
                score += 0.05

            if repo_path and entry.repo_path == str(repo_path.resolve()):
                score += 0.15

            category_boost = {
                "convention": 0.1,
                "stack_note": 0.1,
                "build_command": 0.05,
                "test_command": 0.05,
            }
            score += category_boost.get(entry.category, 0.0)

            entry.relevance = round(min(score, 1.0), 3)
            entry.explanation = (
                f"source={entry.source}, category={entry.category}, "
                f"token_overlap={len(token_overlap) if 'token_overlap' in dir() else 0}"
            )

        candidates.sort(key=lambda e: e.relevance, reverse=True)
        return candidates
