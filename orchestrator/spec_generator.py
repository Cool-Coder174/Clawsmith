"""LLM-powered specification generator — the core of Traycer-style planning.

Takes a high-level goal + codebase context and produces structured,
file-level implementation specs that coding agents can execute precisely.

Supports both local (Ollama) and cloud providers via the existing
provider registry, but defaults to local models for zero-cost planning.

Spec tiers:
    quick   — single-phase, lightweight spec for simple tasks
    full    — multi-section PRD with file-level implementation detail
    epic    — phased spec with dependency graph and rollback points
"""

from __future__ import annotations

import json
import time
from enum import StrEnum
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from orchestrator.logging_setup import get_logger
from orchestrator.schemas import (
    ComplexityBucket,
    ContextPacket,
    TaskClassification,
    TaskType,
    YoloPhase,
    YoloPlan,
)
from orchestrator.planner import TaskPlanner

logger = get_logger("spec_generator")

OLLAMA_BASE = "http://localhost:11434"
DEFAULT_LOCAL_MODEL = "gpt-oss:20b"
SPEC_TIMEOUT = 120  # seconds


class SpecTier(StrEnum):
    quick = "quick"
    full = "full"
    epic = "epic"


class FileChange(BaseModel):
    """A single file-level change specification."""
    path: str
    action: str = Field(description="create | modify | delete | rename")
    description: str
    key_changes: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)


class SpecPhase(BaseModel):
    """A phase within an epic-tier spec."""
    index: int
    title: str
    objective: str
    file_changes: list[FileChange] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    estimated_complexity: float = 0.5
    rollback_notes: str = ""


class GeneratedSpec(BaseModel):
    """The output of spec generation — a structured implementation plan."""
    id: str = Field(default_factory=lambda: __import__("uuid").uuid4().hex[:12])
    goal: str
    tier: SpecTier
    summary: str = ""
    architecture_impact: str = ""
    file_changes: list[FileChange] = Field(default_factory=list)
    phases: list[SpecPhase] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    raw_llm_output: str = ""
    model_used: str = ""
    generation_time_seconds: float = 0.0
    created_at: float = Field(default_factory=lambda: time.time())

    def to_markdown(self) -> str:
        """Render the spec as a human-readable markdown document."""
        lines = [
            f"# Spec: {self.goal}",
            f"**ID:** {self.id}  ",
            f"**Tier:** {self.tier.value}  ",
            f"**Model:** {self.model_used}  ",
            f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.created_at))}  ",
            "",
        ]

        if self.summary:
            lines += ["## Summary", self.summary, ""]

        if self.architecture_impact:
            lines += ["## Architecture Impact", self.architecture_impact, ""]

        if self.file_changes:
            lines += ["## File Changes"]
            for fc in self.file_changes:
                lines.append(f"### `{fc.path}` ({fc.action})")
                lines.append(fc.description)
                if fc.key_changes:
                    for kc in fc.key_changes:
                        lines.append(f"- {kc}")
                if fc.dependencies:
                    lines.append(f"**Depends on:** {', '.join(fc.dependencies)}")
                lines.append("")

        if self.phases:
            lines += ["## Phases"]
            for phase in self.phases:
                lines.append(f"### Phase {phase.index + 1}: {phase.title}")
                lines.append(phase.objective)
                if phase.file_changes:
                    lines.append("**Files:**")
                    for fc in phase.file_changes:
                        lines.append(f"- `{fc.path}` ({fc.action}): {fc.description}")
                if phase.acceptance_criteria:
                    lines.append("**Acceptance Criteria:**")
                    for ac in phase.acceptance_criteria:
                        lines.append(f"- {ac}")
                if phase.rollback_notes:
                    lines.append(f"**Rollback:** {phase.rollback_notes}")
                lines.append("")

        if self.risks:
            lines += ["## Risks"]
            for r in self.risks:
                lines.append(f"- {r}")
            lines.append("")

        if self.open_questions:
            lines += ["## Open Questions"]
            for q in self.open_questions:
                lines.append(f"- {q}")
            lines.append("")

        return "\n".join(lines)

    def to_yolo_plan(self, repo_path: str) -> YoloPlan:
        """Convert this spec into a YoloPlan for YOLO execution."""
        planner = TaskPlanner()
        analysis = planner.analyze_complexity(self.goal)

        if self.phases:
            phases = []
            for sp in self.phases:
                phases.append(YoloPhase(
                    index=sp.index,
                    title=sp.title,
                    objective=sp.objective,
                    task_type=TaskType.implementation,
                    files_in_scope=[fc.path for fc in sp.file_changes],
                    acceptance_criteria=sp.acceptance_criteria,
                    estimated_complexity=sp.estimated_complexity,
                ))
            # Wire dependencies
            for i in range(1, len(phases)):
                phases[i].depends_on = [phases[i - 1].id]
        else:
            phases = [
                YoloPhase(
                    index=0,
                    title="Execute spec",
                    objective=self.summary or self.goal,
                    task_type=TaskType.implementation,
                    files_in_scope=[fc.path for fc in self.file_changes],
                    acceptance_criteria=["All file changes implemented", "Build succeeds"],
                )
            ]

        return YoloPlan(
            goal=self.goal,
            repo_path=repo_path,
            complexity=analysis,
            phases=phases,
        )


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

_QUICK_SPEC_PROMPT = """You are a senior software engineer creating an implementation spec.

## Goal
{goal}

## Repository Context
{context}

## Instructions
Produce a concise implementation plan as JSON with this exact structure:
{{
  "summary": "1-2 sentence overview of the approach",
  "file_changes": [
    {{
      "path": "relative/path/to/file.py",
      "action": "create|modify|delete",
      "description": "what changes and why",
      "key_changes": ["specific change 1", "specific change 2"]
    }}
  ],
  "risks": ["potential risk 1"],
  "open_questions": ["question if any"]
}}

Be specific about file paths based on the repo structure. Only output valid JSON."""

_FULL_SPEC_PROMPT = """You are a senior software architect creating a detailed implementation specification.

## Goal
{goal}

## Repository Context
{context}

## Task Classification
- Type: {task_type}
- Complexity: {complexity:.2f}
- Files likely touched: {files_touched}
- Architectural impact: {arch_impact:.2f}

## Instructions
Produce a detailed implementation spec as JSON with this exact structure:
{{
  "summary": "2-4 sentence technical overview of the approach",
  "architecture_impact": "how this changes the system architecture (if at all)",
  "file_changes": [
    {{
      "path": "relative/path/to/file.py",
      "action": "create|modify|delete|rename",
      "description": "detailed description of changes",
      "key_changes": ["specific implementation detail 1", "specific detail 2"],
      "dependencies": ["other/file.py"]
    }}
  ],
  "risks": ["risk with mitigation strategy"],
  "open_questions": ["design question that needs human input"]
}}

Requirements:
- Be specific about file paths — use the actual repo structure shown above
- Include ALL files that need changes, not just the primary ones
- For each file, list concrete changes (new functions, modified classes, etc.)
- Order file_changes by dependency (foundations first)
- Flag genuine risks, not generic boilerplate
- Only output valid JSON."""

_EPIC_SPEC_PROMPT = """You are a principal engineer creating a phased implementation plan for a complex feature.

## Goal
{goal}

## Repository Context
{context}

## Task Classification
- Type: {task_type}
- Complexity: {complexity:.2f} (bucket: {bucket})
- Files likely touched: {files_touched}
- Architectural impact: {arch_impact:.2f}

## Instructions
Break this into sequential phases. Each phase should be independently executable and verifiable.
Produce a phased spec as JSON with this exact structure:
{{
  "summary": "3-5 sentence technical overview",
  "architecture_impact": "system-level impact analysis",
  "phases": [
    {{
      "index": 0,
      "title": "Phase name",
      "objective": "what this phase accomplishes",
      "file_changes": [
        {{
          "path": "relative/path/to/file.py",
          "action": "create|modify|delete|rename",
          "description": "what changes in this file during this phase",
          "key_changes": ["concrete change 1", "concrete change 2"]
        }}
      ],
      "acceptance_criteria": ["verifiable criterion 1", "criterion 2"],
      "estimated_complexity": 0.4,
      "rollback_notes": "how to undo this phase if needed"
    }}
  ],
  "risks": ["risk with mitigation"],
  "open_questions": ["question for human"]
}}

Requirements:
- 2-6 phases depending on complexity
- Each phase should leave the repo in a working state
- Earlier phases lay foundations; later phases build on them
- Include rollback notes for each phase
- Acceptance criteria must be objectively verifiable
- Only output valid JSON."""


def _build_context_str(context: ContextPacket | None) -> str:
    """Flatten a ContextPacket into a string for prompt injection."""
    if not context:
        return "(no repository context available)"

    parts = []
    if context.architecture_summary:
        parts.append(f"### Architecture\n{context.architecture_summary}")
    if context.relevant_files:
        parts.append("### Key Files")
        for path, content in list(context.relevant_files.items())[:15]:
            # Truncate large files
            truncated = content[:2000] + "\n...(truncated)" if len(content) > 2000 else content
            parts.append(f"#### `{path}`\n```\n{truncated}\n```")
    if context.build_test_commands:
        cmds = ", ".join(f"`{c}`" for c in context.build_test_commands)
        parts.append(f"### Build/Test Commands\n{cmds}")
    if context.constraints:
        parts.append("### Constraints\n" + "\n".join(f"- {c}" for c in context.constraints))

    return "\n\n".join(parts) if parts else "(minimal context)"


class SpecGenerator:
    """Generates structured implementation specs using LLM reasoning.

    Defaults to local Ollama models for zero-cost operation.
    Falls back to the provider registry for cloud models when configured.
    """

    def __init__(
        self,
        *,
        model: str = DEFAULT_LOCAL_MODEL,
        ollama_base: str = OLLAMA_BASE,
        timeout: int = SPEC_TIMEOUT,
    ) -> None:
        self._model = model
        self._ollama_base = ollama_base
        self._timeout = timeout

    async def generate(
        self,
        goal: str,
        context: ContextPacket | None = None,
        classification: TaskClassification | None = None,
        tier: SpecTier | None = None,
    ) -> GeneratedSpec:
        """Generate a structured spec for the given goal.

        If ``tier`` is None, auto-selects based on classification complexity.
        """
        if tier is None:
            tier = self._auto_tier(classification)

        prompt = self._build_prompt(goal, context, classification, tier)
        start = time.monotonic()

        raw_output = await self._call_ollama(prompt)
        gen_time = time.monotonic() - start

        spec = self._parse_response(raw_output, goal, tier)
        spec.model_used = self._model
        spec.generation_time_seconds = round(gen_time, 2)
        spec.raw_llm_output = raw_output

        logger.info(
            "Generated %s spec for '%s' in %.1fs (%d file changes, %d phases)",
            tier.value, goal[:60], gen_time,
            len(spec.file_changes), len(spec.phases),
        )
        return spec

    async def generate_and_save(
        self,
        goal: str,
        repo_path: str,
        context: ContextPacket | None = None,
        classification: TaskClassification | None = None,
        tier: SpecTier | None = None,
    ) -> tuple[GeneratedSpec, Path]:
        """Generate a spec and save it to .clawsmith/specs/."""
        spec = await self.generate(goal, context, classification, tier)

        specs_dir = Path(repo_path) / ".clawsmith" / "specs"
        specs_dir.mkdir(parents=True, exist_ok=True)

        spec_path = specs_dir / f"{spec.id}.md"
        spec_path.write_text(spec.to_markdown(), encoding="utf-8")

        json_path = specs_dir / f"{spec.id}.json"
        json_path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")

        logger.info("Saved spec to %s", spec_path)
        return spec, spec_path

    def _auto_tier(self, classification: TaskClassification | None) -> SpecTier:
        if not classification:
            return SpecTier.full

        score = classification.complexity_score
        if score < 0.30:
            return SpecTier.quick
        if score < 0.70:
            return SpecTier.full
        return SpecTier.epic

    def _build_prompt(
        self,
        goal: str,
        context: ContextPacket | None,
        classification: TaskClassification | None,
        tier: SpecTier,
    ) -> str:
        context_str = _build_context_str(context)

        if tier == SpecTier.quick:
            return _QUICK_SPEC_PROMPT.format(goal=goal, context=context_str)

        task_type = classification.task_type.value if classification else "implementation"
        complexity = classification.complexity_score if classification else 0.5
        files_touched = classification.files_likely_touched if classification else 0
        arch_impact = classification.architectural_impact if classification else 0.0

        if tier == SpecTier.full:
            return _FULL_SPEC_PROMPT.format(
                goal=goal,
                context=context_str,
                task_type=task_type,
                complexity=complexity,
                files_touched=files_touched,
                arch_impact=arch_impact,
            )

        # epic
        planner = TaskPlanner()
        analysis = planner.analyze_complexity(goal, context, classification)
        return _EPIC_SPEC_PROMPT.format(
            goal=goal,
            context=context_str,
            task_type=task_type,
            complexity=complexity,
            bucket=analysis.bucket.value,
            files_touched=files_touched,
            arch_impact=arch_impact,
        )

    async def _call_ollama(self, prompt: str) -> str:
        """Call Ollama's generate endpoint directly."""
        payload = {
            "model": self._model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": 0.3,
                "num_predict": 4096,
            },
        }

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                resp = await client.post(
                    f"{self._ollama_base}/api/generate",
                    json=payload,
                )
                resp.raise_for_status()
                return resp.json().get("response", "")
            except httpx.TimeoutException:
                logger.error("Ollama request timed out after %ds", self._timeout)
                raise
            except httpx.HTTPStatusError as exc:
                logger.error("Ollama HTTP error: %s", exc)
                raise

    def _parse_response(
        self,
        raw: str,
        goal: str,
        tier: SpecTier,
    ) -> GeneratedSpec:
        """Extract structured JSON from the LLM response."""
        data = self._extract_json(raw)

        if data is None:
            logger.warning("Could not parse JSON from LLM output; returning raw spec")
            return GeneratedSpec(
                goal=goal,
                tier=tier,
                summary=raw[:500],
                raw_llm_output=raw,
            )

        # Parse file changes
        file_changes = []
        for fc_data in data.get("file_changes", []):
            try:
                file_changes.append(FileChange(**fc_data))
            except Exception as exc:
                logger.warning("Skipping malformed file_change: %s", exc)

        # Parse phases (epic tier)
        phases = []
        for ph_data in data.get("phases", []):
            ph_file_changes = []
            for fc_data in ph_data.get("file_changes", []):
                try:
                    ph_file_changes.append(FileChange(**fc_data))
                except Exception:
                    pass
            try:
                phases.append(SpecPhase(
                    index=ph_data.get("index", len(phases)),
                    title=ph_data.get("title", f"Phase {len(phases) + 1}"),
                    objective=ph_data.get("objective", ""),
                    file_changes=ph_file_changes,
                    acceptance_criteria=ph_data.get("acceptance_criteria", []),
                    estimated_complexity=ph_data.get("estimated_complexity", 0.5),
                    rollback_notes=ph_data.get("rollback_notes", ""),
                ))
            except Exception as exc:
                logger.warning("Skipping malformed phase: %s", exc)

        return GeneratedSpec(
            goal=goal,
            tier=tier,
            summary=data.get("summary", ""),
            architecture_impact=data.get("architecture_impact", ""),
            file_changes=file_changes,
            phases=phases,
            risks=data.get("risks", []),
            open_questions=data.get("open_questions", []),
        )

    @staticmethod
    def _extract_json(text: str) -> dict[str, Any] | None:
        """Try to extract a JSON object from LLM output, handling markdown fences."""
        # Try direct parse first
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting from markdown code fences
        import re
        patterns = [
            r"```json\s*\n(.*?)\n\s*```",
            r"```\s*\n(.*?)\n\s*```",
            r"\{.*\}",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                candidate = match.group(1) if match.lastindex else match.group(0)
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    continue

        return None
