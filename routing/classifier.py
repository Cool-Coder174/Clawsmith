from __future__ import annotations

import re

from orchestrator.schemas import ContextPacket, TaskClassification, TaskType

_TASK_TYPE_KEYWORDS: list[tuple[TaskType, list[str]]] = [
    (TaskType.audit, ["audit", "inspect", "scan", "review"]),
    (TaskType.refactor, ["refactor", "restructure", "reorganize", "clean up"]),
    (TaskType.debugging, ["debug", "fix bug", "traceback", "error", "exception", "crash"]),
    (TaskType.bugfix, ["bugfix", "bug fix", "patch", "hotfix"]),
    (TaskType.implementation, ["implement", "add feature", "build", "create", "write"]),
    (TaskType.planning, ["plan", "design", "architect", "propose"]),
    (TaskType.testing, ["test", "spec", "coverage", "assert"]),
    (TaskType.summarization, ["summarize", "explain", "describe", "document"]),
    (TaskType.prompt_polish, ["polish", "refine prompt", "improve prompt"]),
]

_AMBIGUITY_MARKERS = [
    "maybe", "possibly", "not sure", "unclear", "might",
    "could", "somehow", "figure out", "investigate",
]

_HIGH_IMPACT_KEYWORDS = [
    "refactor", "migrate", "redesign", "overhaul", "rewrite",
    "restructure", "replace", "extract", "split", "merge",
]

_SEVERITY_MARKERS = [
    "broken", "crash", "critical", "production", "outage",
    "data loss", "security", "urgent", "blocker",
]

_FILE_PATH_RE = re.compile(r"[\w/\\.-]+\.\w{1,6}")


class TaskClassifier:
    def classify(
        self,
        task_description: str,
        context: ContextPacket | None = None,
    ) -> TaskClassification:
        lower = task_description.lower()

        task_type = self._detect_task_type(lower)
        files_likely_touched = self._count_files(task_description, context)
        ambiguity_score = self._score_markers(lower, _AMBIGUITY_MARKERS, 10)
        architectural_impact = self._score_markers(lower, _HIGH_IMPACT_KEYWORDS, 5)
        failure_severity = self._score_markers(lower, _SEVERITY_MARKERS, 5)

        estimated_tokens = (
            context.token_estimate
            if context and context.token_estimate
            else int(len(task_description.split()) / 0.75)
        )

        complexity_score = min(
            1.0,
            max(
                0.0,
                0.30 * min(files_likely_touched / 10, 1.0)
                + 0.25 * ambiguity_score
                + 0.25 * architectural_impact
                + 0.20 * failure_severity,
            ),
        )

        return TaskClassification(
            task_type=task_type,
            complexity_score=complexity_score,
            files_likely_touched=files_likely_touched,
            ambiguity_score=ambiguity_score,
            architectural_impact=architectural_impact,
            failure_severity=failure_severity,
            estimated_tokens=estimated_tokens,
        )

    @staticmethod
    def _detect_task_type(lower: str) -> TaskType:
        for task_type, keywords in _TASK_TYPE_KEYWORDS:
            if any(kw in lower for kw in keywords):
                return task_type
        return TaskType.implementation

    @staticmethod
    def _count_files(
        task_description: str, context: ContextPacket | None
    ) -> int:
        count = len(_FILE_PATH_RE.findall(task_description))
        if context:
            count += len(context.relevant_files)
        return min(count, 50)

    @staticmethod
    def _score_markers(text: str, markers: list[str], divisor: int) -> float:
        hits = sum(1 for m in markers if m in text)
        return min(hits / divisor, 1.0)
