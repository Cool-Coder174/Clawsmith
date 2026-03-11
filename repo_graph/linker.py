"""Repository linker — manages adding and removing repos in the workspace graph.

Provides the ``link-repo`` and ``unlink-repo`` CLI actions, persisting the
workspace graph to disk so that cross-repo context is available across runs.
"""

from __future__ import annotations

from pathlib import Path

from orchestrator.logging_setup import get_logger
from repo_graph.models import RepoNode, WorkspaceGraph
from repo_graph.scanner import WorkspaceScanner

logger = get_logger("repo_graph.linker")


class RepoLinker:
    """High-level API for linking/unlinking repos in a persisted workspace graph."""

    def __init__(self, config_path: Path) -> None:
        self._config_path = Path(config_path)
        self._scanner = WorkspaceScanner()
        self._graph = WorkspaceGraph()

        if self._config_path.exists():
            self._graph = self.load()
            for repo in self._graph.repos:
                self._scanner._repos.append(repo)
                self._scanner._repo_by_name[repo.name] = repo
                self._scanner._repo_by_path[repo.path] = repo

    def link(self, repo_path: Path, role: str = "", description: str = "") -> RepoNode:
        """Add a repo to the workspace graph."""
        node = self._scanner.add_repo(repo_path)
        if role:
            node.role = role
        if description:
            node.description = description
        self._graph = self._scanner.build_graph()
        self.save()
        logger.info("Linked repo %s (%s)", node.name, node.path)
        return node

    def unlink(self, repo_name_or_path: str) -> bool:
        """Remove a repo from the workspace graph by name or path."""
        target: RepoNode | None = None
        has_path_sep = "/" in repo_name_or_path or "\\" in repo_name_or_path
        resolved = str(Path(repo_name_or_path).resolve()) if has_path_sep else None

        for repo in list(self._scanner._repos):
            if repo.name == repo_name_or_path or repo.path == repo_name_or_path:
                target = repo
                break
            if resolved and repo.path == resolved:
                target = repo
                break

        if target is None:
            logger.warning("Repo '%s' not found in workspace graph", repo_name_or_path)
            return False

        self._scanner._repos.remove(target)
        self._scanner._repo_by_name.pop(target.name, None)
        self._scanner._repo_by_path.pop(target.path, None)

        self._scanner._edges = [
            e for e in self._scanner._edges
            if e.source != target.name and e.target != target.name
        ]

        self._graph = self._scanner.build_graph()
        self.save()
        logger.info("Unlinked repo %s", target.name)
        return True

    def list_repos(self) -> list[RepoNode]:
        """List all linked repos."""
        return list(self._graph.repos)

    def refresh(self) -> WorkspaceGraph:
        """Rescan all linked repos and rebuild edges."""
        paths = [Path(r.path) for r in self._scanner._repos]
        roles = {r.name: r.role for r in self._scanner._repos}
        descriptions = {r.name: r.description for r in self._scanner._repos}

        self._scanner = WorkspaceScanner()
        for p in paths:
            if p.exists():
                node = self._scanner.add_repo(p)
                node.role = roles.get(node.name, "")
                node.description = descriptions.get(node.name, "")
            else:
                logger.warning("Repo path no longer exists: %s", p)

        self._graph = self._scanner.build_graph()
        self.save()
        logger.info("Refreshed workspace graph — %d repos, %d edges",
                     len(self._graph.repos), len(self._graph.edges))
        return self._graph

    def save(self) -> Path:
        """Persist the graph to disk as JSON."""
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            self._graph.model_dump_json(indent=2),
            encoding="utf-8",
        )
        logger.debug("Saved workspace graph to %s", self._config_path)
        return self._config_path

    def load(self) -> WorkspaceGraph:
        """Load the graph from disk."""
        if not self._config_path.exists():
            logger.debug("No graph file at %s — returning empty graph", self._config_path)
            return WorkspaceGraph()
        raw = self._config_path.read_text(encoding="utf-8")
        self._graph = WorkspaceGraph.model_validate_json(raw)
        logger.debug("Loaded workspace graph from %s (%d repos)",
                      self._config_path, len(self._graph.repos))
        return self._graph
