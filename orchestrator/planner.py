"""Task decomposition engine for YOLO mode.

Analyses a high-level goal against repository context and breaks it into
ordered phases, each with its own focused objective and acceptance criteria.

Complexity buckets drive decomposition:

    trivial  (score < 0.15) → 1 phase   (direct execution)
    low      (score < 0.30) → 1–2 phases
    medium   (score < 0.55) → 2–4 phases
    high     (score < 0.80) → 3–5 phases
    epic     (score >= 0.80) → 4–6 phases
"""

from __future__ import annotations

import re

from orchestrator.logging_setup import get_logger
from orchestrator.schemas import (
    ComplexityAnalysis,
    ComplexityBucket,
    ContextPacket,
    TaskClassification,
    TaskType,
    YoloPhase,
    YoloPlan,
)

logger = get_logger("planner")

_BUCKET_THRESHOLDS: list[tuple[float, ComplexityBucket, int, int]] = [
    (0.15, ComplexityBucket.trivial, 1, 1),
    (0.30, ComplexityBucket.low, 1, 2),
    (0.55, ComplexityBucket.medium, 2, 4),
    (0.80, ComplexityBucket.high, 3, 5),
    (1.01, ComplexityBucket.epic, 4, 6),
]

_MULTI_CONCERN_MARKERS = [
    "and then", "after that", "also", "additionally", "plus",
    "as well as", "followed by", "next", "finally", "then",
]

_DESIGN_KEYWORDS = [
    "architect", "design", "plan", "structure", "schema", "api",
    "interface", "contract", "spec", "proposal",
]

_TEST_KEYWORDS = [
    "test", "spec", "coverage", "assert", "verify", "validate",
    "integration test", "unit test", "e2e",
]

_REFACTOR_KEYWORDS = [
    "refactor", "clean up", "reorganize", "simplify", "extract",
    "move", "rename", "consolidate", "dedup",
]


class TaskPlanner:
    """Decomposes a high-level goal into ordered YOLO phases."""

    def analyze_complexity(
        self,
        task: str,
        context: ContextPacket | None = None,
        classification: TaskClassification | None = None,
    ) -> ComplexityAnalysis:
        if classification:
            raw = classification.complexity_score
            arch_impact = classification.architectural_impact
            files = classification.files_likely_touched
        else:
            raw = self._estimate_raw_complexity(task)
            arch_impact = 0.0
            files = 0

        concern_count = self._count_concerns(task)
        if concern_count > 2:
            raw = min(1.0, raw + 0.15 * (concern_count - 2))

        bucket = ComplexityBucket.trivial
        rec_phases = 1
        for threshold, b, lo, hi in _BUCKET_THRESHOLDS:
            if raw < threshold:
                bucket = b
                scale = min((raw - (threshold - 0.15)) / 0.15, 1.0) if threshold > 0.15 else 0.0
                rec_phases = lo + round(scale * (hi - lo))
                rec_phases = max(lo, min(hi, rec_phases))
                break

        if concern_count > rec_phases:
            rec_phases = min(concern_count, 6)

        reasoning_parts = [
            f"score={raw:.2f} → {bucket.value}",
            f"concerns={concern_count}",
        ]
        if arch_impact > 0.5:
            reasoning_parts.append(f"high architectural impact ({arch_impact:.2f})")
        if files > 10:
            reasoning_parts.append(f"touches ~{files} files")

        return ComplexityAnalysis(
            bucket=bucket,
            raw_score=round(raw, 3),
            recommended_phases=rec_phases,
            reasoning="; ".join(reasoning_parts),
            architectural_impact=arch_impact,
            files_likely_touched=files,
        )

    def decompose(
        self,
        goal: str,
        repo_path: str,
        context: ContextPacket | None = None,
        classification: TaskClassification | None = None,
        skip_planning: bool = False,
    ) -> YoloPlan:
        analysis = self.analyze_complexity(goal, context, classification)
        n = analysis.recommended_phases

        if n <= 1:
            phases = self._single_phase(goal, classification)
        else:
            phases = self._multi_phase(goal, n, context, classification)

        for i, phase in enumerate(phases):
            phase.index = i

        logger.info(
            "Decomposed goal into %d phases (bucket=%s, score=%.2f): %s",
            len(phases), analysis.bucket.value, analysis.raw_score,
            [p.title for p in phases],
        )

        return YoloPlan(
            goal=goal,
            repo_path=repo_path,
            complexity=analysis,
            phases=phases,
            skip_planning=skip_planning,
        )

    # -- internal decomposition strategies ----------------------------------

    def _single_phase(
        self,
        goal: str,
        classification: TaskClassification | None,
    ) -> list[YoloPhase]:
        tt = classification.task_type if classification else TaskType.implementation
        return [
            YoloPhase(
                index=0,
                title="Execute",
                objective=goal,
                task_type=tt,
                acceptance_criteria=["Task objective is met", "Build succeeds"],
            ),
        ]

    def _multi_phase(
        self,
        goal: str,
        target_count: int,
        context: ContextPacket | None,
        classification: TaskClassification | None,
    ) -> list[YoloPhase]:
        phases: list[YoloPhase] = []
        lower = goal.lower()

        needs_design = any(kw in lower for kw in _DESIGN_KEYWORDS) or (
            classification and classification.architectural_impact > 0.5
        )
        needs_tests = any(kw in lower for kw in _TEST_KEYWORDS)
        needs_refactor = any(kw in lower for kw in _REFACTOR_KEYWORDS)

        concerns = self._extract_concerns(goal)

        if needs_design and target_count >= 3:
            phases.append(YoloPhase(
                index=0,
                title="Design & Planning",
                objective=f"Design the approach for: {goal}. "
                          "Identify files to change, interfaces to define, "
                          "and any architectural decisions.",
                task_type=TaskType.planning,
                acceptance_criteria=[
                    "Approach is documented",
                    "Files to change are identified",
                ],
                estimated_complexity=0.2,
            ))

        if len(concerns) > 1 and len(concerns) <= target_count:
            for concern in concerns:
                phases.append(YoloPhase(
                    index=len(phases),
                    title=f"Implement: {concern[:60]}",
                    objective=concern,
                    task_type=self._infer_task_type(concern, classification),
                    files_in_scope=self._scope_files_for_concern(concern, context),
                    acceptance_criteria=[
                        f"'{concern[:50]}' is implemented",
                        "No new build errors",
                    ],
                    estimated_complexity=0.5,
                ))
        else:
            impl_slots = target_count - len(phases)
            if needs_tests:
                impl_slots -= 1
            if needs_refactor:
                impl_slots -= 1
            impl_slots = max(1, impl_slots)

            if impl_slots == 1:
                phases.append(YoloPhase(
                    index=len(phases),
                    title="Core Implementation",
                    objective=goal,
                    task_type=classification.task_type if classification else TaskType.implementation,
                    files_in_scope=list((context.relevant_files or {}).keys()) if context else [],
                    acceptance_criteria=[
                        "Primary objective is met",
                        "Build succeeds",
                    ],
                    estimated_complexity=0.6,
                ))
            else:
                for slot in range(impl_slots):
                    suffix = f" (part {slot + 1}/{impl_slots})" if impl_slots > 1 else ""
                    phases.append(YoloPhase(
                        index=len(phases),
                        title=f"Implementation{suffix}",
                        objective=f"Part {slot + 1} of {impl_slots}: {goal}",
                        task_type=classification.task_type if classification else TaskType.implementation,
                        acceptance_criteria=[
                            f"Part {slot + 1} objective is met",
                            "No new build errors",
                        ],
                        estimated_complexity=0.5,
                    ))

        if needs_tests and len(phases) < target_count:
            phases.append(YoloPhase(
                index=len(phases),
                title="Testing",
                objective=f"Write or update tests for: {goal}",
                task_type=TaskType.testing,
                acceptance_criteria=["Tests pass", "Coverage is adequate"],
                estimated_complexity=0.3,
            ))

        if needs_refactor and len(phases) < target_count:
            phases.append(YoloPhase(
                index=len(phases),
                title="Cleanup & Refactor",
                objective=f"Clean up implementation from: {goal}",
                task_type=TaskType.refactor,
                acceptance_criteria=["No regressions", "Code follows conventions"],
                estimated_complexity=0.2,
            ))

        if not phases:
            return self._single_phase(goal, classification)

        # Wire up sequential dependencies
        for i in range(1, len(phases)):
            phases[i].depends_on = [phases[i - 1].id]

        return phases

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _estimate_raw_complexity(task: str) -> float:
        words = len(task.split())
        length_factor = min(words / 80, 0.4)
        concern_markers = sum(1 for m in _MULTI_CONCERN_MARKERS if m in task.lower())
        concern_factor = min(concern_markers * 0.08, 0.3)
        return min(1.0, length_factor + concern_factor + 0.1)

    @staticmethod
    def _count_concerns(task: str) -> int:
        lower = task.lower()
        splits = 1
        for marker in _MULTI_CONCERN_MARKERS:
            splits += lower.count(marker)
        sentence_count = len(re.split(r"[.;]\s+", task.strip()))
        return max(splits, sentence_count)

    @staticmethod
    def _extract_concerns(goal: str) -> list[str]:
        pattern = "|".join(re.escape(m) for m in _MULTI_CONCERN_MARKERS)
        parts = re.split(pattern, goal, flags=re.IGNORECASE)
        return [p.strip().rstrip(".,;") for p in parts if p.strip() and len(p.strip()) > 8]

    @staticmethod
    def _infer_task_type(
        concern: str,
        classification: TaskClassification | None,
    ) -> TaskType:
        lower = concern.lower()
        if any(kw in lower for kw in _TEST_KEYWORDS):
            return TaskType.testing
        if any(kw in lower for kw in _REFACTOR_KEYWORDS):
            return TaskType.refactor
        if any(kw in lower for kw in _DESIGN_KEYWORDS):
            return TaskType.planning
        if any(kw in lower for kw in ("fix", "bug", "patch", "error")):
            return TaskType.bugfix
        return classification.task_type if classification else TaskType.implementation

    @staticmethod
    def _scope_files_for_concern(
        concern: str,
        context: ContextPacket | None,
    ) -> list[str]:
        if not context or not context.relevant_files:
            return []
        lower = concern.lower()
        matched = []
        for path in context.relevant_files:
            name = path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].lower()
            stem = name.rsplit(".", 1)[0]
            if stem in lower or any(word in stem for word in lower.split() if len(word) > 3):
                matched.append(path)
        return matched[:10]
