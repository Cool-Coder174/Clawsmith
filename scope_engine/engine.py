"""Scope engine — creates and enforces scope contracts for tasks.

A scope contract limits which files, directories, and repos a task is
allowed to read or modify.  This prevents accidental cross-repo writes
and supports read-only external-repo access for multi-repo workflows.
"""

from __future__ import annotations

import fnmatch
import uuid
from datetime import UTC, datetime
from pathlib import Path

from orchestrator.logging_setup import get_logger
from repo_graph.models import WorkspaceGraph
from scope_engine.models import RepoScope, ScopeContract, ScopeLevel

logger = get_logger("scope_engine.engine")


class ScopeEngine:
    """Evaluates and manages scope contracts for tasks across a workspace."""

    def __init__(self, workspace_root: Path) -> None:
        self._workspace_root = Path(workspace_root).resolve()

    def create_contract(
        self,
        task_description: str,
        primary_repo: str,
        graph: WorkspaceGraph | None = None,
    ) -> ScopeContract:
        """Create a scope contract for a task based on the workspace graph.

        The primary repo is always in-scope. Direct dependencies discovered
        from the graph are added as conditional (read-only) by default.
        All other repos are out-of-scope.
        """
        repo_scopes: list[RepoScope] = []
        direct_dep_names: set[str] = set()

        if graph:
            for edge in graph.edges:
                if edge.source == primary_repo:
                    direct_dep_names.add(edge.target)
                if edge.target == primary_repo:
                    direct_dep_names.add(edge.source)

            for repo in graph.repos:
                if repo.name == primary_repo:
                    repo_scopes.append(RepoScope(
                        repo_name=repo.name,
                        repo_path=repo.path,
                        level=ScopeLevel.in_scope,
                    ))
                elif repo.name in direct_dep_names:
                    repo_scopes.append(RepoScope(
                        repo_name=repo.name,
                        repo_path=repo.path,
                        level=ScopeLevel.conditional,
                        read_only=True,
                        notes="Direct dependency — read-only by default",
                    ))
                else:
                    repo_scopes.append(RepoScope(
                        repo_name=repo.name,
                        repo_path=repo.path,
                        level=ScopeLevel.out_of_scope,
                        notes="No direct relationship to primary repo",
                    ))

        if not any(r.repo_name == primary_repo for r in repo_scopes):
            repo_scopes.insert(0, RepoScope(
                repo_name=primary_repo,
                repo_path=str(self._workspace_root / primary_repo),
                level=ScopeLevel.in_scope,
            ))

        contract = ScopeContract(
            task_id=uuid.uuid4().hex[:12],
            primary_repo=primary_repo,
            repos=repo_scopes,
            allow_multi_repo_changes=len(direct_dep_names) > 0,
            created_at=datetime.now(UTC).isoformat(),
            notes=task_description,
        )
        logger.info("Created scope contract %s for repo '%s' (%d repos)",
                     contract.task_id, primary_repo, len(repo_scopes))
        return contract

    def check_file_in_scope(
        self, contract: ScopeContract, file_path: str,
    ) -> tuple[bool, str]:
        """Check if a file path is allowed under a scope contract."""
        resolved = str(Path(file_path).resolve())

        for repo_scope in contract.repos:
            repo_prefix = repo_scope.repo_path
            if resolved != repo_prefix and not resolved.startswith(repo_prefix + "/") and not resolved.startswith(repo_prefix + "\\"):
                continue

            if repo_scope.level == ScopeLevel.out_of_scope:
                return False, f"File is in repo '{repo_scope.repo_name}' which is out of scope"

            relative = resolved[len(repo_prefix):].lstrip("/").lstrip("\\")
            for pattern in repo_scope.restricted_paths:
                if fnmatch.fnmatch(relative, pattern):
                    return False, (
                        f"File matches restricted pattern '{pattern}' "
                        f"in repo '{repo_scope.repo_name}'"
                    )

            if repo_scope.read_only:
                return False, (
                    f"File is in repo '{repo_scope.repo_name}' which is read-only "
                    f"(level: {repo_scope.level.value})"
                )

            level = repo_scope.level.value
            return True, f"File is in repo '{repo_scope.repo_name}' (level: {level})"

        return False, "File does not belong to any repo in the scope contract"

    def check_repo_in_scope(
        self, contract: ScopeContract, repo_path: str,
    ) -> tuple[bool, str]:
        """Check if a repo is in scope. Returns (allowed, reason)."""
        resolved = str(Path(repo_path).resolve())

        for repo_scope in contract.repos:
            if repo_scope.repo_path == resolved or repo_scope.repo_name == repo_path:
                if repo_scope.level == ScopeLevel.in_scope:
                    return True, f"Repo '{repo_scope.repo_name}' is in scope"
                if repo_scope.level == ScopeLevel.conditional:
                    return True, (
                        f"Repo '{repo_scope.repo_name}' is conditionally in scope"
                        + (" (read-only)" if repo_scope.read_only else "")
                    )
                return False, f"Repo '{repo_scope.repo_name}' is out of scope"

        return False, f"Repo at '{repo_path}' is not in the scope contract"

    def get_scope_summary(self, contract: ScopeContract) -> str:
        """Human-readable summary of what's in/out of scope."""
        multi_ok = "allowed" if contract.allow_multi_repo_changes else "not allowed"
        lines: list[str] = [
            f"Scope Contract: {contract.task_id}",
            f"Primary repo: {contract.primary_repo}",
            f"Multi-repo changes: {multi_ok}",
            "",
        ]

        by_level: dict[ScopeLevel, list[RepoScope]] = {
            ScopeLevel.in_scope: [],
            ScopeLevel.conditional: [],
            ScopeLevel.out_of_scope: [],
        }
        for rs in contract.repos:
            by_level[rs.level].append(rs)

        if by_level[ScopeLevel.in_scope]:
            lines.append("IN SCOPE:")
            for rs in by_level[ScopeLevel.in_scope]:
                lines.append(f"  - {rs.repo_name} ({rs.repo_path})")

        if by_level[ScopeLevel.conditional]:
            lines.append("CONDITIONAL:")
            for rs in by_level[ScopeLevel.conditional]:
                flags = []
                if rs.read_only:
                    flags.append("read-only")
                if rs.allow_version_bumps:
                    flags.append("version-bumps ok")
                if rs.allow_coordinated_changes:
                    flags.append("coordinated changes ok")
                suffix = f" [{', '.join(flags)}]" if flags else ""
                lines.append(f"  - {rs.repo_name}{suffix}")
                if rs.restricted_paths:
                    lines.append(f"    restricted: {', '.join(rs.restricted_paths)}")

        if by_level[ScopeLevel.out_of_scope]:
            lines.append("OUT OF SCOPE:")
            for rs in by_level[ScopeLevel.out_of_scope]:
                lines.append(f"  - {rs.repo_name}")

        if contract.notes:
            lines.extend(["", f"Notes: {contract.notes}"])

        return "\n".join(lines)

    def save_contract(self, contract: ScopeContract, path: Path | None = None) -> Path:
        """Persist a scope contract to disk."""
        if path is None:
            path = self._workspace_root / ".clawsmith" / "scopes" / f"{contract.task_id}.json"
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contract.model_dump_json(indent=2), encoding="utf-8")
        logger.debug("Saved scope contract %s to %s", contract.task_id, path)
        return path

    def load_contract(self, path: Path) -> ScopeContract:
        """Load a scope contract from disk."""
        raw = Path(path).read_text(encoding="utf-8")
        contract = ScopeContract.model_validate_json(raw)
        logger.debug("Loaded scope contract %s from %s", contract.task_id, path)
        return contract

    def answer_scope_question(self, contract: ScopeContract, question: str) -> str:
        """Answer natural-language scope questions using keyword matching."""
        q = question.lower()

        if any(kw in q for kw in ("which repo", "what repo", "who owns")):
            for term in q.split():
                for rs in contract.repos:
                    if term in rs.repo_name.lower() or term in rs.repo_path.lower():
                        return (
                            f"'{rs.repo_name}' is {rs.level.value} "
                            f"(path: {rs.repo_path})"
                            + (f" — {rs.notes}" if rs.notes else "")
                        )
            known = ", ".join(r.repo_name for r in contract.repos)
            return f"No matching repo found. Known repos: {known}"

        if any(kw in q for kw in ("in scope", "allowed", "can i")):
            for rs in contract.repos:
                if rs.repo_name.lower() in q or rs.repo_path.lower() in q:
                    if rs.level == ScopeLevel.in_scope:
                        return f"Yes — '{rs.repo_name}' is in scope."
                    if rs.level == ScopeLevel.conditional:
                        constraints = []
                        if rs.read_only:
                            constraints.append("read-only")
                        if rs.restricted_paths:
                            constraints.append(f"restricted paths: {rs.restricted_paths}")
                        detail = f" ({', '.join(constraints)})" if constraints else ""
                        return f"Conditionally — '{rs.repo_name}' is conditional{detail}."
                    return f"No — '{rs.repo_name}' is out of scope."
            return self.get_scope_summary(contract)

        if any(kw in q for kw in ("primary", "main repo")):
            return f"The primary repo is '{contract.primary_repo}'."

        if any(kw in q for kw in ("list", "show", "all repos", "summary")):
            return self.get_scope_summary(contract)

        if any(kw in q for kw in ("read-only", "readonly", "read only", "write")):
            read_only_repos = [rs for rs in contract.repos if rs.read_only]
            if read_only_repos:
                names = ", ".join(rs.repo_name for rs in read_only_repos)
                return f"Read-only repos: {names}"
            return "No repos are marked read-only in this contract."

        return self.get_scope_summary(contract)
