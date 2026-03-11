from __future__ import annotations

import os
from pathlib import Path

from orchestrator.schemas import ContextPacket
from tools.build_detector import BuildDetector
from tools.repo_auditor import AuditReport
from tools.repo_mapper import RepoMap


class ContextPacker:
    def __init__(self, root_path: Path, token_budget: int = 8000) -> None:
        self.root_path = root_path
        self.token_budget = token_budget

    def pack(
        self,
        audit: AuditReport,
        repo_map: RepoMap,
        task_description: str,
        file_list: list[str] | None = None,
        recent_errors: list[str] | None = None,
    ) -> ContextPacket:
        architecture_summary = self._build_architecture_summary(audit)
        build_test_commands = self._build_command_strings()
        selected_files = self._select_relevant_files(file_list, audit, repo_map, task_description)

        file_budget = int(self.token_budget * 0.7)
        relevant_files = self._read_files_within_budget(selected_files, file_budget)

        recommended_steps = self._build_recommended_steps(audit)
        constraints = self._build_constraints(audit)

        packet = ContextPacket(
            task_summary=task_description,
            relevant_files=relevant_files,
            architecture_summary=architecture_summary,
            build_test_commands=build_test_commands,
            recent_errors=recent_errors or [],
            constraints=constraints,
            recommended_steps=recommended_steps,
            token_estimate=0,
        )

        packet.token_estimate = self._estimate_tokens(packet.model_dump_json())
        return packet

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        words = text.split()
        return int(len(words) / 0.75) if words else 0

    def _read_file_safe(self, path: Path) -> str:
        try:
            with open(path, encoding="utf-8") as fh:
                lines = []
                for i, line in enumerate(fh):
                    if i >= 500:
                        break
                    lines.append(line)
                return "".join(lines)
        except UnicodeDecodeError:
            return "[binary file, skipped]"
        except OSError:
            return "[unreadable file, skipped]"

    def _select_relevant_files(
        self,
        file_list: list[str] | None,
        audit: AuditReport,
        repo_map: RepoMap,
        task_description: str,
    ) -> list[Path]:
        resolved_root = self.root_path.resolve()

        if file_list:
            resolved: list[Path] = []
            for f in file_list:
                p = (self.root_path / f).resolve()
                try:
                    p.relative_to(resolved_root)
                except ValueError:
                    continue
                if p.is_file():
                    resolved.append(p)
            return resolved

        candidates: list[str] = []
        candidates.extend(repo_map.entrypoints)
        candidates.extend(repo_map.important_files)

        task_lower = task_description.lower()
        for dirpath, _dirnames, filenames in os.walk(self.root_path, topdown=True):
            rel_dir = os.path.relpath(dirpath, self.root_path)
            skip_parts = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}
            if any(part in skip_parts for part in Path(rel_dir).parts):
                continue
            for fname in filenames:
                rel_path = os.path.join(rel_dir, fname).replace(os.sep, "/")
                if rel_path.startswith("./"):
                    rel_path = rel_path[2:]
                if rel_path.lower() in task_lower or fname.lower() in task_lower:
                    candidates.append(rel_path)

        seen: set[str] = set()
        deduped: list[Path] = []
        for c in candidates:
            normalized = c.replace(os.sep, "/")
            if normalized not in seen:
                seen.add(normalized)
                p = self.root_path / normalized
                if p.is_file():
                    deduped.append(p)
            if len(deduped) >= 20:
                break

        return deduped

    def _read_files_within_budget(
        self, files: list[Path], file_budget: int
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        running_tokens = 0

        for p in files:
            try:
                rel = str(p.relative_to(self.root_path)).replace(os.sep, "/")
            except ValueError:
                try:
                    rel = str(p.resolve().relative_to(self.root_path.resolve())).replace(os.sep, "/")
                except ValueError:
                    continue

            content = self._read_file_safe(p)
            cost = self._estimate_tokens(content)

            if running_tokens + cost > file_budget:
                remaining = file_budget - running_tokens
                if remaining > 0:
                    words = content.split()
                    keep = int(remaining * 0.75)
                    content = " ".join(words[:keep])
                else:
                    break

            result[rel] = content
            running_tokens += self._estimate_tokens(content)

        return result

    def _build_architecture_summary(self, audit: AuditReport) -> str:
        parts: list[str] = []

        if audit.languages:
            top_langs = sorted(audit.languages.items(), key=lambda x: x[1], reverse=True)[:3]
            lang_str = ", ".join(f"{ext} ({count} files)" for ext, count in top_langs)
            parts.append(f"Languages: {lang_str}")

        if audit.frameworks:
            parts.append(f"Frameworks: {', '.join(audit.frameworks)}")
        if audit.package_managers:
            parts.append(f"Package managers: {', '.join(audit.package_managers)}")
        if audit.build_systems:
            parts.append(f"Build systems: {', '.join(audit.build_systems)}")
        if audit.test_frameworks:
            parts.append(f"Test frameworks: {', '.join(audit.test_frameworks)}")
        if audit.ci_configs:
            parts.append(f"CI configs: {', '.join(audit.ci_configs)}")

        return "\n".join(parts) if parts else "No architecture metadata detected."

    def _build_command_strings(self) -> list[str]:
        commands = BuildDetector(self.root_path).detect()
        return [f"{cmd.ecosystem}: {cmd.command}" for cmd in commands]

    @staticmethod
    def _build_recommended_steps(audit: AuditReport) -> list[str]:
        steps: list[str] = []
        if "pytest" in audit.test_frameworks:
            steps.append("Run: pytest")
        if any("ruff" in lc for lc in audit.linter_configs):
            steps.append("Run: ruff check .")
        if any("mypy" in lc for lc in audit.linter_configs):
            steps.append("Run: mypy .")
        if "npm" in audit.package_managers:
            steps.append("Run: npm install && npm test")
        return steps

    def _build_constraints(self, audit: AuditReport) -> list[str]:
        constraints: list[str] = [f"Token budget: {self.token_budget}"]

        total_markers = sum(1 for v in audit.marker_files.values() if v)
        if total_markers > 5:
            constraints.append("Large project detected — consider dry-run before full execution.")

        expected_configs = {
            "pyproject.toml": any(ext in audit.languages for ext in (".py",)),
            "package.json": any(ext in audit.languages for ext in (".ts", ".js", ".tsx", ".jsx")),
            "Cargo.toml": ".rs" in audit.languages,
            "go.mod": ".go" in audit.languages,
        }
        for cfg, expected in expected_configs.items():
            if expected and not audit.marker_files.get(cfg):
                constraints.append(f"Missing expected config: {cfg}")

        return constraints
