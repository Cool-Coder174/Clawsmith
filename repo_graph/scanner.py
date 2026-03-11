from __future__ import annotations

import configparser
import json
import subprocess
from collections import defaultdict, deque
from pathlib import Path

from orchestrator.logging_setup import get_logger
from repo_graph.models import DependencyEdge, RepoNode, WorkspaceGraph

logger = get_logger("repo_graph.scanner")

_MANIFEST_LANGUAGE_MAP: dict[str, str] = {
    "pyproject.toml": "Python",
    "setup.py": "Python",
    "setup.cfg": "Python",
    "Cargo.toml": "Rust",
    "package.json": "JavaScript",
    "go.mod": "Go",
    "pom.xml": "Java",
    "build.gradle": "Java",
    "Gemfile": "Ruby",
    "mix.exs": "Elixir",
}

_MANIFEST_PM_MAP: dict[str, str] = {
    "pyproject.toml": "pip",
    "setup.py": "pip",
    "setup.cfg": "pip",
    "Cargo.toml": "cargo",
    "package.json": "npm",
    "go.mod": "go",
    "pom.xml": "maven",
    "build.gradle": "gradle",
    "Gemfile": "bundler",
    "mix.exs": "mix",
}


def _run_git(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def _try_read_toml(path: Path) -> dict:
    """Read a TOML file, falling back to a minimal configparser-based parse."""
    try:
        import tomllib  # noqa: F811 — stdlib 3.11+
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _try_read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _parse_gitmodules(repo_path: Path) -> list[tuple[str, str]]:
    """Return list of (submodule_name, submodule_path) from .gitmodules."""
    gitmodules = repo_path / ".gitmodules"
    if not gitmodules.exists():
        return []
    cp = configparser.ConfigParser()
    try:
        cp.read(str(gitmodules), encoding="utf-8")
    except configparser.Error:
        return []
    results: list[tuple[str, str]] = []
    for section in cp.sections():
        name = section.replace('submodule "', "").rstrip('"')
        sub_path = cp.get(section, "path", fallback=name)
        results.append((name, sub_path))
    return results


class WorkspaceScanner:
    """Scans directories for repos and builds a workspace dependency graph."""

    def __init__(self) -> None:
        self._repos: list[RepoNode] = []
        self._edges: list[DependencyEdge] = []
        self._repo_by_name: dict[str, RepoNode] = {}
        self._repo_by_path: dict[str, RepoNode] = {}

    def add_repo(self, path: Path) -> RepoNode:
        """Register and scan a single repo."""
        path = path.resolve()
        abs_path = str(path)

        if abs_path in self._repo_by_path:
            return self._repo_by_path[abs_path]

        manifests: list[str] = []
        languages: list[str] = []
        package_managers: list[str] = []

        seen_langs: set[str] = set()
        seen_pms: set[str] = set()

        for manifest_name, lang in _MANIFEST_LANGUAGE_MAP.items():
            if (path / manifest_name).exists():
                manifests.append(manifest_name)
                if lang not in seen_langs:
                    languages.append(lang)
                    seen_langs.add(lang)
                pm = _MANIFEST_PM_MAP.get(manifest_name, "")
                if pm and pm not in seen_pms:
                    package_managers.append(pm)
                    seen_pms.add(pm)

        branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], path)
        remote_url = _run_git(["config", "--get", "remote.origin.url"], path)

        node = RepoNode(
            path=abs_path,
            name=path.name,
            languages=languages,
            package_managers=package_managers,
            manifest_files=manifests,
            branch=branch,
            remote_url=remote_url,
        )

        self._repos.append(node)
        self._repo_by_name[node.name] = node
        self._repo_by_path[abs_path] = node
        logger.info("Added repo %s (%s)", node.name, abs_path)
        return node

    def scan_directory(self, path: Path, max_depth: int = 2) -> list[RepoNode]:
        """Discover repos under *path* by looking for .git directories."""
        path = path.resolve()
        found: list[RepoNode] = []

        def _walk(current: Path, depth: int) -> None:
            if depth > max_depth:
                return
            if not current.is_dir():
                return
            if (current / ".git").exists():
                found.append(self.add_repo(current))
                return
            try:
                children = sorted(current.iterdir())
            except PermissionError:
                return
            for child in children:
                if child.is_dir() and not child.name.startswith("."):
                    _walk(child, depth + 1)

        _walk(path, 0)
        logger.info("Scanned %s — found %d repos", path, len(found))
        return found

    def discover_edges(self) -> list[DependencyEdge]:
        """Analyze manifests across all registered repos for cross-repo deps."""
        self._edges.clear()

        for repo in self._repos:
            repo_path = Path(repo.path)
            self._discover_python_edges(repo, repo_path)
            self._discover_rust_edges(repo, repo_path)
            self._discover_node_edges(repo, repo_path)
            self._discover_submodule_edges(repo, repo_path)

        logger.info("Discovered %d dependency edges", len(self._edges))
        return list(self._edges)

    def _add_edge(self, edge: DependencyEdge) -> None:
        self._edges.append(edge)

    def _resolve_path_target(self, repo_path: Path, relative: str) -> RepoNode | None:
        """Try to resolve a relative path to a known repo."""
        target = (repo_path / relative).resolve()
        target_str = str(target)
        if target_str in self._repo_by_path:
            return self._repo_by_path[target_str]
        for registered in self._repos:
            if target_str == registered.path or target_str.startswith(registered.path + "/") or target_str.startswith(registered.path + "\\"):
                return registered
        return None

    # -- Python -----------------------------------------------------------------

    def _discover_python_edges(self, repo: RepoNode, repo_path: Path) -> None:
        pyproject = repo_path / "pyproject.toml"
        if not pyproject.exists():
            return
        data = _try_read_toml(pyproject)

        deps = data.get("project", {}).get("dependencies", [])
        if isinstance(deps, list):
            for dep in deps:
                if isinstance(dep, str) and "@ file" in dep:
                    parts = dep.split("@ file://")
                    if len(parts) == 2:
                        target = self._resolve_path_target(repo_path, parts[1].strip())
                        if target:
                            self._add_edge(DependencyEdge(
                                source=repo.name,
                                target=target.name,
                                dependency_type="runtime",
                                version_constraint=f"path:{parts[1].strip()}",
                            ))

        for group_name in ("dependencies", "dev-dependencies", "build-system"):
            section = data.get("project", {}).get(group_name, {})
            if not isinstance(section, dict):
                continue
            for _pkg, spec in section.items():
                if isinstance(spec, dict) and "path" in spec:
                    rel_path = spec["path"]
                    dep_type = "dev" if "dev" in group_name else "runtime"
                    target = self._resolve_path_target(repo_path, rel_path)
                    if target:
                        self._add_edge(DependencyEdge(
                            source=repo.name,
                            target=target.name,
                            dependency_type=dep_type,
                            version_constraint=f"path:{rel_path}",
                        ))

        tool_poetry = data.get("tool", {}).get("poetry", {})
        for group_name in ("dependencies", "dev-dependencies", "group"):
            section = tool_poetry.get(group_name, {})
            if not isinstance(section, dict):
                continue
            for _pkg, spec in section.items():
                if isinstance(spec, dict) and "path" in spec:
                    rel_path = spec["path"]
                    dep_type = "dev" if "dev" in group_name else "runtime"
                    target = self._resolve_path_target(repo_path, rel_path)
                    if target:
                        self._add_edge(DependencyEdge(
                            source=repo.name,
                            target=target.name,
                            dependency_type=dep_type,
                            version_constraint=f"path:{rel_path}",
                        ))

    # -- Rust -------------------------------------------------------------------

    def _discover_rust_edges(self, repo: RepoNode, repo_path: Path) -> None:
        cargo = repo_path / "Cargo.toml"
        if not cargo.exists():
            return
        data = _try_read_toml(cargo)

        workspace_members = data.get("workspace", {}).get("members", [])
        for member_glob in workspace_members:
            member_str = str(member_glob).replace("/*", "").replace("\\*", "")
            member_dir = repo_path / member_str
            if member_dir.is_dir():
                target = self._resolve_path_target(repo_path, member_str)
                if target and target.name != repo.name:
                    self._add_edge(DependencyEdge(
                        source=repo.name,
                        target=target.name,
                        dependency_type="workspace",
                        version_constraint=f"member:{member_str}",
                    ))

        for dep_section_key in ("dependencies", "dev-dependencies", "build-dependencies"):
            section = data.get(dep_section_key, {})
            if not isinstance(section, dict):
                continue
            dep_type = (
                "dev" if "dev" in dep_section_key
                else "build" if "build" in dep_section_key
                else "runtime"
            )
            for _crate, spec in section.items():
                if isinstance(spec, dict) and "path" in spec:
                    rel_path = spec["path"]
                    version = spec.get("version", "")
                    target = self._resolve_path_target(repo_path, rel_path)
                    if target:
                        vc = f"path:{rel_path}" + (f", {version}" if version else "")
                        self._add_edge(DependencyEdge(
                            source=repo.name,
                            target=target.name,
                            dependency_type=dep_type,
                            version_constraint=vc,
                        ))

    # -- Node -------------------------------------------------------------------

    def _discover_node_edges(self, repo: RepoNode, repo_path: Path) -> None:
        pkg_json = repo_path / "package.json"
        if not pkg_json.exists():
            return
        data = _try_read_json(pkg_json)

        dep_keys = (
            "dependencies", "devDependencies",
            "peerDependencies", "optionalDependencies",
        )
        for dep_key in dep_keys:
            section = data.get(dep_key, {})
            if not isinstance(section, dict):
                continue
            dep_type = "dev" if dep_key == "devDependencies" else "runtime"
            for _pkg, version_spec in section.items():
                if not isinstance(version_spec, str):
                    continue
                rel_path: str | None = None
                if version_spec.startswith("file:"):
                    rel_path = version_spec[len("file:"):]
                elif version_spec.startswith("link:"):
                    rel_path = version_spec[len("link:"):]

                if rel_path:
                    target = self._resolve_path_target(repo_path, rel_path)
                    if target:
                        self._add_edge(DependencyEdge(
                            source=repo.name,
                            target=target.name,
                            dependency_type=dep_type,
                            version_constraint=version_spec,
                        ))

    # -- Git submodules ---------------------------------------------------------

    def _discover_submodule_edges(self, repo: RepoNode, repo_path: Path) -> None:
        for name, sub_path in _parse_gitmodules(repo_path):
            target = self._resolve_path_target(repo_path, sub_path)
            target_name = target.name if target else name
            self._add_edge(DependencyEdge(
                source=repo.name,
                target=target_name,
                dependency_type="git-submodule",
                version_constraint=f"submodule:{sub_path}",
            ))

    # -- Graph building ---------------------------------------------------------

    def build_graph(self) -> WorkspaceGraph:
        """Build the complete workspace graph from registered repos."""
        self.discover_edges()
        build_order = self.compute_build_order()

        warnings: list[str] = []
        edge_targets = {e.target for e in self._edges}
        repo_names = {r.name for r in self._repos}
        for target in edge_targets - repo_names:
            warnings.append(f"Dependency target '{target}' is not a registered repo")

        root = ""
        if self._repos:
            root = self._repos[0].path

        return WorkspaceGraph(
            repos=list(self._repos),
            edges=list(self._edges),
            root_workspace=root,
            build_order=build_order,
            warnings=warnings,
        )

    def compute_build_order(self) -> list[str]:
        """Topological sort of repos by dependency edges (Kahn's algorithm)."""
        repo_names = {r.name for r in self._repos}
        in_degree: dict[str, int] = defaultdict(int)
        adjacency: dict[str, list[str]] = defaultdict(list)

        for name in repo_names:
            in_degree.setdefault(name, 0)

        for edge in self._edges:
            if edge.source in repo_names and edge.target in repo_names:
                adjacency[edge.target].append(edge.source)
                in_degree[edge.source] += 1

        queue: deque[str] = deque(n for n in repo_names if in_degree[n] == 0)
        order: list[str] = []

        while queue:
            node = queue.popleft()
            order.append(node)
            for neighbour in adjacency[node]:
                in_degree[neighbour] -= 1
                if in_degree[neighbour] == 0:
                    queue.append(neighbour)

        if len(order) != len(repo_names):
            logger.warning(
                "Cycle detected in dependency graph — build order is incomplete "
                "(got %d of %d repos)", len(order), len(repo_names),
            )

        return order
