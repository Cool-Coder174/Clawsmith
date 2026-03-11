from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

_AddFn = Callable[[str, str, str], None]


class BuildCommand(BaseModel):
    ecosystem: str
    command: str
    purpose: Literal["build", "test", "lint", "format", "typecheck", "install"]


class BuildDetector:
    def __init__(self, root_path: Path) -> None:
        self.root_path = root_path

    def detect(self) -> list[BuildCommand]:
        seen: set[tuple[str, str, str]] = set()
        commands: list[BuildCommand] = []

        def _add(ecosystem: str, command: str, purpose: str) -> None:
            key = (ecosystem, command, purpose)
            if key not in seen:
                seen.add(key)
                commands.append(
                    BuildCommand(ecosystem=ecosystem, command=command, purpose=purpose)  # type: ignore[arg-type]
                )

        self._detect_python(_add)
        self._detect_node(_add)
        self._detect_rust(_add)
        self._detect_dotnet(_add)

        return commands

    def _detect_python(self, add: _AddFn) -> None:
        has_pyproject = (self.root_path / "pyproject.toml").exists()
        has_setup = (self.root_path / "setup.py").exists()
        has_requirements = (self.root_path / "requirements.txt").exists()

        if not (has_pyproject or has_setup or has_requirements):
            return

        add("python", "pip install -e .[dev]", "install")

        pyproject_content = ""
        if has_pyproject:
            pyproject_content = (self.root_path / "pyproject.toml").read_text(
                encoding="utf-8", errors="ignore"
            )
            if "[tool.pytest" in pyproject_content:
                add("python", "pytest", "test")
            if "[tool.ruff" in pyproject_content:
                add("python", "ruff check .", "lint")
                add("python", "ruff format .", "format")
            if "[tool.mypy" in pyproject_content:
                add("python", "mypy .", "typecheck")

        requirements_content = ""
        if has_requirements:
            try:
                requirements_content = (self.root_path / "requirements.txt").read_text(
                    encoding="utf-8", errors="ignore"
                ).lower()
            except OSError:
                pass

        all_config_text = (pyproject_content + " " + requirements_content).lower()

        has_pytest_ini = (self.root_path / "pytest.ini").exists()
        has_setup_cfg_pytest = False
        if (self.root_path / "setup.cfg").exists():
            try:
                setup_cfg = (self.root_path / "setup.cfg").read_text(
                    encoding="utf-8", errors="ignore"
                )
                has_setup_cfg_pytest = "[tool:pytest]" in setup_cfg
            except OSError:
                pass
        has_conftest = (self.root_path / "conftest.py").exists()
        has_tests_dir = (self.root_path / "tests").is_dir()
        pytest_in_deps = "pytest" in all_config_text

        if has_pytest_ini or has_setup_cfg_pytest or has_conftest or has_tests_dir or pytest_in_deps:
            add("python", "pytest", "test")

        has_ruff_toml = (self.root_path / "ruff.toml").exists() or (self.root_path / ".ruff.toml").exists()
        ruff_in_deps = "ruff" in all_config_text

        if has_ruff_toml or ruff_in_deps:
            add("python", "ruff check .", "lint")
            add("python", "ruff format .", "format")

        has_mypy_ini = (self.root_path / "mypy.ini").exists() or (self.root_path / ".mypy.ini").exists()
        mypy_in_deps = "mypy" in all_config_text

        if has_mypy_ini or mypy_in_deps:
            add("python", "mypy .", "typecheck")

        if not (has_pyproject or has_setup):
            add("python", "pytest", "test")

    def _detect_node(self, add: _AddFn) -> None:
        pkg_path = self.root_path / "package.json"
        if not pkg_path.exists():
            return

        add("node", "npm install", "install")

        try:
            data = json.loads(pkg_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return

        scripts: dict[str, str] = data.get("scripts", {})
        mapping: dict[str, str] = {
            "build": "build",
            "test": "test",
            "lint": "lint",
            "typecheck": "typecheck",
        }
        for key, purpose in mapping.items():
            if key in scripts:
                add("node", f"npm run {key}", purpose)

    def _detect_rust(self, add: _AddFn) -> None:
        if not (self.root_path / "Cargo.toml").exists():
            return

        add("rust", "cargo build", "build")
        add("rust", "cargo test", "test")
        add("rust", "cargo fmt", "format")
        add("rust", "cargo clippy", "lint")

    def _detect_dotnet(self, add: _AddFn) -> None:
        csproj_files = list(self.root_path.rglob("*.csproj"))
        sln_files = list(self.root_path.rglob("*.sln"))

        if not (csproj_files or sln_files):
            return

        add("dotnet", "dotnet restore", "install")
        add("dotnet", "dotnet build", "build")
        add("dotnet", "dotnet test", "test")
