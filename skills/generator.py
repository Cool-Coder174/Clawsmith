"""Auto skill generator — scans repo structure and dependencies to create skills."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from orchestrator.logging_setup import get_logger

from .models import SkillDefinition, SourceType

log = get_logger("skills.generator")


def _file_exists(repo: Path, *names: str) -> list[str]:
    """Return which of the given filenames exist in the repo."""
    return [n for n in names if (repo / n).exists()]


def _read_json_safe(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_toml_project(path: Path) -> dict | None:
    """Read [project] section from pyproject.toml using basic parsing."""
    try:
        text = path.read_text(encoding="utf-8")
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]
        return tomllib.loads(text)
    except Exception:
        return None


def _skill_id(prefix: str, name: str) -> str:
    """Generate a deterministic skill ID from prefix and name."""
    raw = f"{prefix}:{name}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


class SkillGenerator:
    """Scans a repository and generates skills from its structure and dependencies."""

    def __init__(self, repo_path: Path) -> None:
        self.repo_path = repo_path.resolve()
        self._skills: list[SkillDefinition] = []

    def generate(self) -> list[SkillDefinition]:
        """Run all detectors and return generated skills."""
        self._skills = []
        self._detect_python()
        self._detect_node()
        self._detect_rust()
        self._detect_go()
        self._detect_docker()
        self._detect_ci()
        self._detect_makefile()
        self._detect_mcp_openclaw()
        log.info("Generated %d skills from %s", len(self._skills), self.repo_path.name)
        return self._skills

    def _add(self, skill: SkillDefinition) -> None:
        self._skills.append(skill)

    def _detect_python(self) -> None:
        pyproject = self.repo_path / "pyproject.toml"
        reqs = _file_exists(
            self.repo_path,
            "requirements.txt", "requirements-dev.txt",
            "Pipfile", "poetry.lock", "setup.py", "setup.cfg",
        )
        if not pyproject.exists() and not reqs:
            return

        evidence = [f"pyproject.toml exists: {pyproject.exists()}"]
        evidence.extend(f"found: {f}" for f in reqs)
        stacks = ["python"]
        deps_text = ""

        if pyproject.exists():
            data = _read_toml_project(pyproject)
            if data:
                proj = data.get("project", {})
                deps = proj.get("dependencies", [])
                deps_text = " ".join(deps) if isinstance(deps, list) else str(deps)

                tool = data.get("tool", {})
                if "pytest" in str(data) or (self.repo_path / "pytest.ini").exists():
                    stacks.append("pytest")
                    self._add(SkillDefinition(
                        id=_skill_id("gen", "pytest-triage"),
                        name="Test Triage (pytest)",
                        description="Run pytest, identify failures, and suggest targeted fixes.",
                        source_type=SourceType.dependency_derived,
                        triggers=["test", "pytest", "failing test", "test failure", "fix test"],
                        applicable_stacks=["python", "pytest"],
                        inferred_commands=["pytest", "pytest -x --tb=short"],
                        acceptance_criteria=["all tests pass", "no new failures introduced"],
                        constraints=["only modify test files and source under test"],
                        confidence=0.9,
                        generation_evidence=evidence,
                        tags=["testing", "python"],
                    ))

                if "ruff" in str(tool) or "ruff" in deps_text:
                    stacks.append("ruff")
                    self._add(SkillDefinition(
                        id=_skill_id("gen", "ruff-lint-fix"),
                        name="Lint Fix (ruff)",
                        description="Run ruff to detect and auto-fix linting issues.",
                        source_type=SourceType.dependency_derived,
                        triggers=["lint", "ruff", "code style", "fix lint", "format"],
                        applicable_stacks=["python", "ruff"],
                        inferred_commands=["ruff check --fix .", "ruff format ."],
                        acceptance_criteria=["ruff check passes with no errors"],
                        confidence=0.9,
                        generation_evidence=evidence,
                        tags=["linting", "python"],
                    ))

                if "mypy" in str(tool) or "mypy" in deps_text:
                    stacks.append("mypy")
                    self._add(SkillDefinition(
                        id=_skill_id("gen", "mypy-typecheck"),
                        name="Type Check (mypy)",
                        description="Run mypy and fix type errors.",
                        source_type=SourceType.dependency_derived,
                        triggers=["type", "mypy", "typing", "type error", "type check"],
                        applicable_stacks=["python", "mypy"],
                        inferred_commands=["mypy ."],
                        acceptance_criteria=["mypy passes with no errors"],
                        confidence=0.85,
                        generation_evidence=evidence,
                        tags=["typing", "python"],
                    ))

                if "fastapi" in deps_text.lower():
                    stacks.append("fastapi")
                    self._add(SkillDefinition(
                        id=_skill_id("gen", "fastapi-debug"),
                        name="FastAPI Debug",
                        description="Debug FastAPI endpoints: startup validation, route tracing, OpenAPI check.",
                        source_type=SourceType.dependency_derived,
                        triggers=["fastapi", "endpoint", "api", "route", "openapi", "swagger"],
                        applicable_stacks=["python", "fastapi"],
                        inferred_commands=["python -c 'from main import app; print(app.routes)'"],
                        acceptance_criteria=["server starts without errors", "routes resolve correctly"],
                        confidence=0.8,
                        generation_evidence=evidence + ["fastapi in dependencies"],
                        tags=["api", "python", "fastapi"],
                    ))

                if "django" in deps_text.lower():
                    stacks.append("django")
                    self._add(SkillDefinition(
                        id=_skill_id("gen", "django-debug"),
                        name="Django Debug",
                        description="Debug Django views, models, migrations, and URL routing.",
                        source_type=SourceType.dependency_derived,
                        triggers=["django", "migration", "model", "view", "url"],
                        applicable_stacks=["python", "django"],
                        inferred_commands=["python manage.py check", "python manage.py showmigrations"],
                        acceptance_criteria=["manage.py check passes", "migrations are consistent"],
                        confidence=0.8,
                        generation_evidence=evidence + ["django in dependencies"],
                        tags=["web", "python", "django"],
                    ))

                if "flask" in deps_text.lower():
                    stacks.append("flask")

                if "pydantic" in deps_text.lower():
                    stacks.append("pydantic")

                if "click" in deps_text.lower() or "typer" in deps_text.lower():
                    stacks.append("cli")

        self._add(SkillDefinition(
            id=_skill_id("gen", "python-build-validate"),
            name="Python Build Validation",
            description="Validate Python package builds, check imports, and verify installability.",
            source_type=SourceType.repo_derived,
            triggers=["build", "install", "import", "package", "pip"],
            applicable_stacks=stacks,
            inferred_commands=["pip install -e .", "python -c 'import <pkg>'"],
            acceptance_criteria=["editable install succeeds", "key imports work"],
            confidence=0.85,
            generation_evidence=evidence,
            tags=["build", "python"],
        ))

    def _detect_node(self) -> None:
        pkg_json = self.repo_path / "package.json"
        if not pkg_json.exists():
            return

        data = _read_json_safe(pkg_json)
        evidence = ["package.json exists"]
        stacks = ["node", "javascript"]
        scripts = {}

        if data:
            scripts = data.get("scripts", {})
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            dep_names = " ".join(deps.keys()).lower()

            if "react" in dep_names:
                stacks.append("react")
            if "next" in dep_names:
                stacks.append("nextjs")
            if "vue" in dep_names:
                stacks.append("vue")
            if "vite" in dep_names:
                stacks.append("vite")
            if "typescript" in dep_names or (self.repo_path / "tsconfig.json").exists():
                stacks.append("typescript")
            if "jest" in dep_names:
                stacks.append("jest")
            if "vitest" in dep_names:
                stacks.append("vitest")
            if "eslint" in dep_names:
                stacks.append("eslint")

        if "test" in scripts:
            test_cmd = scripts["test"]
            self._add(SkillDefinition(
                id=_skill_id("gen", "node-test-triage"),
                name="Test Triage (Node)",
                description="Run JS/TS tests and triage failures.",
                source_type=SourceType.dependency_derived,
                triggers=["test", "jest", "vitest", "failing test"],
                applicable_stacks=stacks,
                inferred_commands=[f"npm test", test_cmd],
                acceptance_criteria=["all tests pass"],
                confidence=0.85,
                generation_evidence=evidence,
                tags=["testing", "node"],
            ))

        if "build" in scripts:
            self._add(SkillDefinition(
                id=_skill_id("gen", "node-build-validate"),
                name="Build Validation (Node)",
                description="Run build and verify output.",
                source_type=SourceType.repo_derived,
                triggers=["build", "compile", "bundle", "webpack", "vite"],
                applicable_stacks=stacks,
                inferred_commands=["npm run build"],
                acceptance_criteria=["build completes without errors"],
                confidence=0.85,
                generation_evidence=evidence,
                tags=["build", "node"],
            ))

        if "lint" in scripts:
            self._add(SkillDefinition(
                id=_skill_id("gen", "node-lint-fix"),
                name="Lint Fix (ESLint/Node)",
                description="Run linter and auto-fix issues.",
                source_type=SourceType.dependency_derived,
                triggers=["lint", "eslint", "code style", "format"],
                applicable_stacks=stacks,
                inferred_commands=["npm run lint", "npm run lint -- --fix"],
                acceptance_criteria=["lint passes with no errors"],
                confidence=0.85,
                generation_evidence=evidence,
                tags=["linting", "node"],
            ))

    def _detect_rust(self) -> None:
        cargo = self.repo_path / "Cargo.toml"
        if not cargo.exists():
            return

        evidence = ["Cargo.toml exists"]
        self._add(SkillDefinition(
            id=_skill_id("gen", "rust-build-test"),
            name="Rust Build & Test",
            description="Build and test Rust project with cargo.",
            source_type=SourceType.repo_derived,
            triggers=["build", "test", "cargo", "rust", "compile"],
            applicable_stacks=["rust"],
            inferred_commands=["cargo build", "cargo test", "cargo clippy"],
            acceptance_criteria=["cargo build succeeds", "cargo test passes"],
            confidence=0.85,
            generation_evidence=evidence,
            tags=["build", "testing", "rust"],
        ))

    def _detect_go(self) -> None:
        gomod = self.repo_path / "go.mod"
        if not gomod.exists():
            return

        evidence = ["go.mod exists"]
        self._add(SkillDefinition(
            id=_skill_id("gen", "go-build-test"),
            name="Go Build & Test",
            description="Build and test Go project.",
            source_type=SourceType.repo_derived,
            triggers=["build", "test", "go", "compile"],
            applicable_stacks=["go"],
            inferred_commands=["go build ./...", "go test ./...", "go vet ./..."],
            acceptance_criteria=["go build succeeds", "go test passes"],
            confidence=0.85,
            generation_evidence=evidence,
            tags=["build", "testing", "go"],
        ))

    def _detect_docker(self) -> None:
        dockerfiles = _file_exists(
            self.repo_path,
            "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
            "compose.yml", "compose.yaml",
        )
        if not dockerfiles:
            return

        evidence = [f"found: {f}" for f in dockerfiles]
        has_compose = any("compose" in f for f in dockerfiles)

        self._add(SkillDefinition(
            id=_skill_id("gen", "docker-debug"),
            name="Docker Debug",
            description="Debug Docker builds, compose services, and container issues.",
            source_type=SourceType.repo_derived,
            triggers=["docker", "container", "compose", "image", "build"],
            applicable_stacks=["docker"],
            inferred_commands=(
                ["docker compose up --build", "docker compose logs"]
                if has_compose
                else ["docker build ."]
            ),
            acceptance_criteria=["containers start successfully", "health checks pass"],
            confidence=0.75,
            generation_evidence=evidence,
            tags=["docker", "infrastructure"],
        ))

    def _detect_ci(self) -> None:
        ci_files: list[str] = []
        gh_workflows = self.repo_path / ".github" / "workflows"
        if gh_workflows.exists():
            ci_files.extend(str(f.relative_to(self.repo_path)) for f in gh_workflows.glob("*.yml"))
            ci_files.extend(str(f.relative_to(self.repo_path)) for f in gh_workflows.glob("*.yaml"))

        for name in (".gitlab-ci.yml", "Jenkinsfile", ".circleci/config.yml"):
            if (self.repo_path / name).exists():
                ci_files.append(name)

        if not ci_files:
            return

        evidence = [f"CI config: {f}" for f in ci_files]
        self._add(SkillDefinition(
            id=_skill_id("gen", "ci-debug"),
            name="CI Pipeline Debug",
            description="Debug CI/CD pipeline failures and configuration issues.",
            source_type=SourceType.repo_derived,
            triggers=["ci", "pipeline", "github actions", "workflow", "build failure"],
            applicable_stacks=["ci"],
            inferred_file_targets=ci_files,
            acceptance_criteria=["CI pipeline passes"],
            confidence=0.7,
            generation_evidence=evidence,
            tags=["ci", "devops"],
        ))

    def _detect_makefile(self) -> None:
        makefile = self.repo_path / "Makefile"
        if not makefile.exists():
            return

        try:
            text = makefile.read_text(encoding="utf-8")
        except Exception:
            return

        import re
        targets = re.findall(r"^(\w[\w-]*)\s*:", text, re.MULTILINE)
        if not targets:
            return

        evidence = [f"Makefile targets: {', '.join(targets[:10])}"]
        self._add(SkillDefinition(
            id=_skill_id("gen", "makefile-runner"),
            name="Makefile Runner",
            description=f"Execute Makefile targets: {', '.join(targets[:5])}.",
            source_type=SourceType.repo_derived,
            triggers=["make", "makefile"] + targets[:5],
            applicable_stacks=["make"],
            inferred_commands=[f"make {t}" for t in targets[:5]],
            acceptance_criteria=["make target completes successfully"],
            confidence=0.7,
            generation_evidence=evidence,
            tags=["build", "make"],
        ))

    def _detect_mcp_openclaw(self) -> None:
        mcp_configs = _file_exists(
            self.repo_path,
            "mcp_server/server.py", "config/openclaw_skill.yaml",
        )
        if not mcp_configs:
            return

        evidence = [f"MCP/OpenClaw config: {f}" for f in mcp_configs]
        self._add(SkillDefinition(
            id=_skill_id("gen", "mcp-openclaw-validate"),
            name="MCP/OpenClaw Validation",
            description="Validate MCP server and OpenClaw integration configuration.",
            source_type=SourceType.repo_derived,
            triggers=["mcp", "openclaw", "integration", "tool surface"],
            applicable_stacks=["mcp", "openclaw"],
            inferred_file_targets=mcp_configs,
            acceptance_criteria=["MCP server starts", "OpenClaw registration succeeds"],
            confidence=0.75,
            generation_evidence=evidence,
            tags=["mcp", "openclaw", "integration"],
        ))
