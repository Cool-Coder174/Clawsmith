"""Smoke tests for packaging: every source package must be included in the build."""

from __future__ import annotations

import fnmatch
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {"tests", "__pycache__", ".git", ".venv", "venv", "node_modules", ".ruff_cache"}


def _top_level_packages() -> list[str]:
    """Return directory names under the repo root that contain an __init__.py."""
    return sorted(
        d.name
        for d in ROOT.iterdir()
        if d.is_dir() and d.name not in SKIP_DIRS and (d / "__init__.py").exists()
    )


def _pyproject_include_patterns() -> list[str]:
    """Parse the include list from pyproject.toml [tool.setuptools.packages.find]."""
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib  # type: ignore[no-redef]

    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    return data["tool"]["setuptools"]["packages"]["find"]["include"]


def _pattern_matches(patterns: list[str], name: str) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


class TestPackageDiscovery:
    """Guard against packages being left out of setuptools discovery."""

    def test_all_source_packages_in_include_list(self) -> None:
        patterns = _pyproject_include_patterns()
        packages = _top_level_packages()

        missing = [p for p in packages if not _pattern_matches(patterns, p)]
        assert not missing, (
            f"Top-level packages missing from [tool.setuptools.packages.find] include: "
            f"{missing}. Add them to pyproject.toml to include them in the build."
        )

    @pytest.mark.parametrize(
        "module",
        [
            "execution",
            "execution.backend",
            "execution.cli_agent",
            "execution.models",
            "execution.phase_executor",
        ],
    )
    def test_execution_package_importable(self, module: str) -> None:
        """The execution package must be importable — regression for the YOLO runtime crash."""
        importlib.import_module(module)

    def test_yolo_imports_resolve(self) -> None:
        """orchestrator.yolo must import without ModuleNotFoundError."""
        importlib.import_module("orchestrator.yolo")
