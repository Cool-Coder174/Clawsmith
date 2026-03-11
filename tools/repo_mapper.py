from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from pydantic import BaseModel, Field

_ALWAYS_IGNORE = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    "dist", "build", ".mypy_cache", ".ruff_cache", ".pytest_cache",
}

_ALWAYS_IGNORE_PATTERNS = ["*.egg-info"]

_ENTRYPOINT_CANDIDATES = [
    "main.py", "src/main.py", "app.py", "src/app.py",
    "index.ts", "src/index.ts", "index.js", "src/index.js",
    "Program.cs", "src/lib.rs", "src/main.rs",
    "cmd/main.go", "main.go",
]

_IMPORTANT_FILE_CANDIDATES = [
    "README.md", "README.rst", "README.txt",
    "Dockerfile", "docker-compose.yml", "docker-compose.yaml",
    ".env.example", "Makefile",
    "pyproject.toml", "package.json", "Cargo.toml", "go.mod",
]


class RepoMap(BaseModel):
    tree_text: str = ""
    entrypoints: list[str] = Field(default_factory=list)
    important_files: list[str] = Field(default_factory=list)
    total_files: int = 0
    total_dirs: int = 0
    truncated: bool = False


class RepoMapper:
    def __init__(self, root_path: Path, max_lines: int = 200) -> None:
        self.root_path = root_path
        self.default_max_lines = max_lines

    def map(self, max_lines: int | None = None) -> RepoMap:
        cap = max_lines if max_lines is not None else self.default_max_lines
        gitignore_patterns = self._load_gitignore_patterns()

        lines: list[str] = []
        total_files = 0
        total_dirs = 0
        truncated = False

        for dirpath, dirnames, filenames in os.walk(self.root_path, topdown=True):
            rel_dir = os.path.relpath(dirpath, self.root_path)
            depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1

            dirnames[:] = sorted(
                d for d in dirnames
                if not self._is_ignored(Path(dirpath) / d, gitignore_patterns)
            )
            filenames = sorted(
                f for f in filenames
                if not self._is_ignored(Path(dirpath) / f, gitignore_patterns)
            )

            if rel_dir != ".":
                total_dirs += 1
                if len(lines) < cap:
                    indent = "  " * (depth - 1)
                    lines.append(f"{indent}{os.path.basename(dirpath)}/")
                elif not truncated:
                    truncated = True

            for fname in filenames:
                total_files += 1
                if len(lines) < cap:
                    indent = "  " * depth
                    lines.append(f"{indent}{fname}")
                elif not truncated:
                    truncated = True

        entrypoints = self._detect_entrypoints()
        important_files = self._detect_important_files()

        return RepoMap(
            tree_text="\n".join(lines),
            entrypoints=entrypoints,
            important_files=important_files,
            total_files=total_files,
            total_dirs=total_dirs,
            truncated=truncated,
        )

    def _load_gitignore_patterns(self) -> list[str]:
        gi_path = self.root_path / ".gitignore"
        if not gi_path.is_file():
            return []
        patterns: list[str] = []
        try:
            for line in gi_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    patterns.append(stripped)
        except OSError:
            pass
        return patterns

    def _is_ignored(self, path: Path, patterns: list[str]) -> bool:
        name = path.name

        if name in _ALWAYS_IGNORE:
            return True
        for pat in _ALWAYS_IGNORE_PATTERNS:
            if fnmatch.fnmatch(name, pat):
                return True

        try:
            rel = str(path.relative_to(self.root_path)).replace(os.sep, "/")
        except ValueError:
            rel = name

        for pat in patterns:
            clean = pat.rstrip("/")
            if fnmatch.fnmatch(rel, clean) or fnmatch.fnmatch(name, clean):
                return True
            if fnmatch.fnmatch(rel, f"**/{clean}"):
                return True

        return False

    def _detect_entrypoints(self) -> list[str]:
        found: list[str] = []
        for candidate in _ENTRYPOINT_CANDIDATES:
            if (self.root_path / candidate).is_file():
                found.append(candidate.replace(os.sep, "/"))
        return found

    def _detect_important_files(self) -> list[str]:
        found: list[str] = []
        for candidate in _IMPORTANT_FILE_CANDIDATES:
            if (self.root_path / candidate).is_file():
                found.append(candidate.replace(os.sep, "/"))

        csproj = list(self.root_path.rglob("*.csproj"))
        for cp in csproj:
            found.append(str(cp.relative_to(self.root_path)).replace(os.sep, "/"))

        return found
