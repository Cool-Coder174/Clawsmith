"""Repository auditor — scans a repo to detect languages, frameworks, and tooling.

The audit report feeds into the ``ContextPacker`` and ``ModelRouter`` so
that ClawSmith can tailor prompts and routing decisions to the actual
project stack (e.g. pytest for Python, jest for Node, Cargo for Rust).
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build"}

_KNOWN_EXTENSIONS = {
    ".py", ".ts", ".js", ".tsx", ".jsx", ".rs", ".cs",
    ".go", ".java", ".rb", ".cpp", ".c", ".h",
}

_MARKER_FILES = [
    "pyproject.toml", "setup.py", "requirements.txt",
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "Cargo.toml", "go.mod", "Gemfile", "Makefile", "Dockerfile",
    "docker-compose.yml",
]

_CI_PATHS = [
    ".github/workflows",
    "Jenkinsfile",
    ".gitlab-ci.yml",
    ".circleci/config.yml",
    "azure-pipelines.yml",
]

_LINTER_CONFIGS = [
    ".ruff.toml", "ruff.toml", ".flake8",
    ".eslintrc", ".eslintrc.json", ".eslintrc.js", ".eslintrc.yml",
    "mypy.ini", ".mypy.ini", "pyrightconfig.json", "clippy.toml",
]


class AuditReport(BaseModel):
    """Structured snapshot of a repository's tech stack and tooling."""

    languages: dict[str, int] = Field(default_factory=dict)
    frameworks: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    build_systems: list[str] = Field(default_factory=list)
    test_frameworks: list[str] = Field(default_factory=list)
    ci_configs: list[str] = Field(default_factory=list)
    linter_configs: list[str] = Field(default_factory=list)
    marker_files: dict[str, bool] = Field(default_factory=dict)
    root_path: str = ""


class RepoAuditor:
    """Walks a repository tree and identifies its languages, build systems, and CI setup."""

    def __init__(self, root_path: Path) -> None:
        self.root_path = root_path

    def audit(self) -> AuditReport:
        """Run the full audit and return an ``AuditReport``."""
        languages = self._detect_languages()
        marker_files = self._detect_marker_files()
        frameworks = self._detect_frameworks(marker_files)
        package_managers = self._detect_package_managers(marker_files)
        build_systems = self._detect_build_systems(marker_files)
        test_frameworks = self._detect_test_frameworks(marker_files)
        ci_configs = self._detect_ci_configs()
        linter_configs = self._detect_linter_configs()

        return AuditReport(
            languages=languages,
            frameworks=frameworks,
            package_managers=package_managers,
            build_systems=build_systems,
            test_frameworks=test_frameworks,
            ci_configs=ci_configs,
            linter_configs=linter_configs,
            marker_files=marker_files,
            root_path=str(self.root_path),
        )

    def _detect_languages(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for p in self.root_path.rglob("*"):
            if not p.is_file():
                continue
            if any(part in _SKIP_DIRS for part in p.relative_to(self.root_path).parts):
                continue
            suffix = p.suffix.lower()
            if suffix in _KNOWN_EXTENSIONS:
                counts[suffix] = counts.get(suffix, 0) + 1
        return counts

    def _detect_marker_files(self) -> dict[str, bool]:
        markers: dict[str, bool] = {}
        for name in _MARKER_FILES:
            markers[name] = (self.root_path / name).exists()
        markers["*.csproj"] = bool(list(self.root_path.rglob("*.csproj")))
        return markers

    def _detect_frameworks(self, markers: dict[str, bool]) -> list[str]:
        frameworks: list[str] = []

        if markers.get("package.json"):
            deps = self._read_package_json_deps()
            for fw in ("react", "vue", "next", "express", "angular", "svelte"):
                if fw in deps:
                    frameworks.append(fw)

        if markers.get("pyproject.toml"):
            content = self._read_text("pyproject.toml")
            for fw in ("fastapi", "django", "flask"):
                if fw in content.lower():
                    frameworks.append(fw)

        if markers.get("requirements.txt"):
            content = self._read_text("requirements.txt")
            for fw in ("fastapi", "django", "flask"):
                if fw in content.lower():
                    frameworks.append(fw)

        return list(dict.fromkeys(frameworks))

    def _detect_package_managers(self, markers: dict[str, bool]) -> list[str]:
        managers: list[str] = []
        has_python_pkg = (
            markers.get("pyproject.toml")
            or markers.get("setup.py")
            or markers.get("requirements.txt")
        )
        if has_python_pkg:
            managers.append("pip")
        if markers.get("pnpm-lock.yaml"):
            managers.append("pnpm")
        elif markers.get("yarn.lock"):
            managers.append("yarn")
        elif markers.get("package-lock.json") or markers.get("package.json"):
            managers.append("npm")
        if markers.get("Cargo.toml"):
            managers.append("cargo")
        if markers.get("go.mod"):
            managers.append("go")
        if markers.get("*.csproj"):
            managers.append("dotnet")
        if markers.get("Gemfile"):
            managers.append("bundler")
        return managers

    def _detect_build_systems(self, markers: dict[str, bool]) -> list[str]:
        systems: list[str] = []

        if markers.get("pyproject.toml"):
            content = self._read_text("pyproject.toml")
            if "[build-system]" in content:
                for bs in ("setuptools", "hatchling", "hatch", "flit", "poetry"):
                    if bs in content.lower():
                        systems.append(bs)

        if markers.get("package.json"):
            dev_deps = self._read_package_json_deps(dev_only=True)
            for bs in ("webpack", "vite", "esbuild", "rollup", "parcel", "turbo"):
                if bs in dev_deps:
                    systems.append(bs)

        if markers.get("Cargo.toml"):
            systems.append("cargo")
        if markers.get("Makefile"):
            systems.append("make")

        return list(dict.fromkeys(systems))

    def _detect_test_frameworks(self, markers: dict[str, bool]) -> list[str]:
        frameworks: list[str] = []

        if markers.get("pyproject.toml"):
            content = self._read_text("pyproject.toml")
            if "pytest" in content.lower():
                frameworks.append("pytest")
        if (self.root_path / "pytest.ini").exists():
            if "pytest" not in frameworks:
                frameworks.append("pytest")

        if markers.get("package.json"):
            dev_deps = self._read_package_json_deps(dev_only=True)
            for tf in ("jest", "vitest", "mocha", "ava", "jasmine"):
                if tf in dev_deps:
                    frameworks.append(tf)

        if markers.get("Cargo.toml"):
            frameworks.append("cargo test")

        return list(dict.fromkeys(frameworks))

    def _detect_ci_configs(self) -> list[str]:
        found: list[str] = []

        workflows_dir = self.root_path / ".github" / "workflows"
        if workflows_dir.is_dir():
            for f in workflows_dir.iterdir():
                if f.suffix in (".yml", ".yaml") and f.is_file():
                    found.append(str(f.relative_to(self.root_path)))

        simple_ci = [
            "Jenkinsfile", ".gitlab-ci.yml",
            ".circleci/config.yml", "azure-pipelines.yml",
        ]
        for name in simple_ci:
            ci_path = self.root_path / name
            if ci_path.exists():
                found.append(name)

        return found

    def _detect_linter_configs(self) -> list[str]:
        found: list[str] = []
        for name in _LINTER_CONFIGS:
            if (self.root_path / name).exists():
                found.append(name)

        for pattern in ("eslint.config.*",):
            for p in self.root_path.glob(pattern):
                rel = str(p.relative_to(self.root_path))
                if rel not in found:
                    found.append(rel)

        return found

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_text(self, relative: str) -> str:
        try:
            return (self.root_path / relative).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def _read_package_json_deps(self, *, dev_only: bool = False) -> set[str]:
        try:
            data = json.loads(self._read_text("package.json"))
        except (json.JSONDecodeError, ValueError):
            return set()
        deps: set[str] = set()
        if not dev_only:
            deps.update(data.get("dependencies", {}).keys())
        deps.update(data.get("devDependencies", {}).keys())
        return deps
