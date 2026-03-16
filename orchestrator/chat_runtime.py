"""Chat-first runtime — the shared orchestration layer for clawsmith chat and CLI.

This module owns the session lifecycle:
- loading skills and memory
- routing user input to the right handler
- enforcing scope and safety guardrails
- tracking explainability metadata
- supporting both interactive and non-interactive use
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.logging_setup import get_logger
from orchestrator.session_state import SessionState

log = get_logger("chat_runtime")


class ChatRuntime:
    """Shared runtime for chat-first orchestration.

    Used by:
    - `clawsmith chat` (interactive mode)
    - CLI wrappers like `run-task`, `audit` (non-interactive mode)
    - Programmatic / test use
    """

    def __init__(
        self,
        repo_path: Path | str = ".",
        *,
        dry_run: bool = False,
        safe_mode: bool = True,
        interactive: bool = True,
    ) -> None:
        repo = Path(repo_path).resolve()
        self.state = SessionState(
            repo_path=repo,
            workspace_root=repo,
            dry_run=dry_run,
            safe_mode=safe_mode,
            interactive=interactive,
        )
        self._initialized = False

    def initialize(self) -> None:
        """Load skills, detect stacks, and prepare runtime state.

        Idempotent — safe to call multiple times.
        """
        if self._initialized:
            return

        self._detect_repo_stacks()
        self._load_skills()
        self._initialized = True
        log.info(
            "Runtime initialized: repo=%s, skills=%d, stacks=%s",
            self.state.repo_path.name,
            len(self.state.loaded_skills),
            self.state.repo_stacks,
        )

    def process_task(self, task: str) -> dict[str, Any]:
        """Process a task description through the skill-aware pipeline.

        Returns a dict with keys: success, output, skills, memories, explain.
        """
        self.initialize()
        self.state.add_user_message(task)

        self._retrieve_memories(task)
        self._select_skills(task)

        result: dict[str, Any] = {
            "success": True,
            "output": "",
            "skills": [],
            "memories": [],
            "explain": {},
        }

        if self.state.skill_selection and self.state.skill_selection.selected_skills:
            from skills.executor import SkillExecutionRequest, execute_skill
            from skills.registry import SkillRegistry

            registry = self._get_registry()
            skill_outputs: list[str] = []

            for sid in self.state.skill_selection.selected_skills:
                skill = registry.get(sid)
                if not skill:
                    continue

                exec_result = execute_skill(SkillExecutionRequest(
                    skill=skill,
                    task_description=task,
                    repo_path=self.state.repo_path,
                    dry_run=self.state.dry_run,
                    safe_mode=self.state.safe_mode,
                ))
                skill_outputs.append(exec_result.output)
                result["skills"].append({
                    "id": skill.id,
                    "name": skill.name,
                    "success": exec_result.success,
                    "output": exec_result.output,
                    "scope_violations": exec_result.scope_violations,
                })

            if skill_outputs:
                result["output"] = "\n\n".join(skill_outputs)

        if self.state.retrieved_memories:
            result["memories"] = [
                {
                    "category": e.category,
                    "content": e.content[:200],
                    "relevance": e.relevance,
                    "source": e.source,
                }
                for e in self.state.retrieved_memories.entries[:5]
            ]

        result["explain"] = self.state.get_explainability_summary()
        self.state.add_agent_message(result.get("output", ""))
        return result

    def retrieve_memories_for(self, task: str) -> list[dict[str, Any]]:
        """Retrieve and return ranked memories for a task (standalone)."""
        self.initialize()
        self._retrieve_memories(task)
        if not self.state.retrieved_memories:
            return []
        return [
            {
                "category": e.category,
                "content": e.content[:200],
                "relevance": e.relevance,
                "source": e.source,
                "explanation": e.explanation,
            }
            for e in self.state.retrieved_memories.entries
        ]

    def select_skills_for(self, task: str) -> dict[str, Any]:
        """Select and return scored skills for a task (standalone)."""
        self.initialize()
        self._select_skills(task)
        if not self.state.skill_selection:
            return {"selected": [], "scored": [], "explanation": "No skills loaded."}
        return {
            "selected": self.state.skill_selection.selected_skills,
            "scored": [
                {
                    "id": s.skill_id,
                    "name": s.skill_name,
                    "score": s.score,
                    "reason": s.relevance_reason,
                }
                for s in self.state.skill_selection.scored_skills
            ],
            "explanation": self.state.skill_selection.explanation,
        }

    def list_skills(self) -> list[dict[str, Any]]:
        """List all loaded skills."""
        self.initialize()
        return [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "source_type": s.source_type.value,
                "enabled": s.enabled,
                "confidence": s.confidence,
                "triggers": s.triggers,
                "stacks": s.applicable_stacks,
            }
            for s in self.state.loaded_skills
        ]

    def regenerate_skills(self) -> int:
        """Regenerate skills from the current repo. Returns count generated."""
        from skills.generator import SkillGenerator

        generator = SkillGenerator(self.state.repo_path)
        new_skills = generator.generate()

        registry = self._get_registry()
        for skill in new_skills:
            registry.register(skill, persist=True)

        self.state.loaded_skills = registry.list_all()
        return len(new_skills)

    def remember(self, content: str, category: str = "note", tags: list[str] | None = None) -> str:
        """Store an always-remember entry."""
        from memory_skill.always_remember import AlwaysRemember

        ar = AlwaysRemember(self.state.workspace_root)
        return ar.remember(content, category, tags, str(self.state.repo_path))

    def list_memories(self) -> list[dict[str, Any]]:
        """List all always-remember entries."""
        from memory_skill.always_remember import AlwaysRemember

        ar = AlwaysRemember(self.state.workspace_root)
        return ar.list_entries()

    def _detect_repo_stacks(self) -> None:
        """Detect the technology stacks present in the repo."""
        try:
            from tools.repo_auditor import RepoAuditor

            report = RepoAuditor(self.state.repo_path).audit()
            stacks = list(report.languages)
            stacks.extend(report.frameworks)
            stacks.extend(report.package_managers)
            stacks.extend(report.test_frameworks)
            self.state.repo_stacks = stacks
        except Exception as exc:
            log.debug("Stack detection failed (non-fatal): %s", exc)

    def _load_skills(self) -> None:
        """Load skills from disk into the registry."""
        registry = self._get_registry()
        registry.load_from_disk()
        self.state.loaded_skills = registry.list_all()

    def _get_registry(self) -> Any:
        """Get or create the skill registry."""
        from skills.registry import SkillRegistry

        storage = self.state.repo_path / ".clawsmith" / "skills"
        return SkillRegistry(storage_root=storage)

    def _retrieve_memories(self, task: str) -> None:
        """Retrieve relevant memories for the current task."""
        try:
            from memory_skill.retriever import MemoryRetriever

            retriever = MemoryRetriever(self.state.workspace_root)
            self.state.retrieved_memories = retriever.retrieve(
                task,
                repo_path=self.state.repo_path,
            )
        except Exception as exc:
            log.debug("Memory retrieval failed (non-fatal): %s", exc)

    def _select_skills(self, task: str) -> None:
        """Score and select skills relevant to the current task."""
        from skills.resolver import resolve_skills

        if self.state.loaded_skills:
            self.state.skill_selection = resolve_skills(
                self.state.loaded_skills,
                task,
                repo_stacks=self.state.repo_stacks,
            )
