"""Skill resolver — scores and selects skills for a given task."""

from __future__ import annotations

import re

from orchestrator.logging_setup import get_logger

from .models import SkillDefinition, SkillScore, SkillSelectionResult

log = get_logger("skills.resolver")

_SCORE_THRESHOLD = 0.1


def _tokenize(text: str) -> set[str]:
    """Split text into lowercase word tokens."""
    return {w.lower() for w in re.findall(r"\w+", text)}


def score_skill(skill: SkillDefinition, task: str, repo_stacks: list[str] | None = None) -> SkillScore:
    """Score a single skill against a task description and repo context."""
    task_tokens = _tokenize(task)
    score = 0.0
    trigger_matches: list[str] = []
    stack_matches: list[str] = []
    keyword_matches: list[str] = []

    for trigger in skill.triggers:
        trigger_lower = trigger.lower()
        if trigger_lower in task.lower():
            score += 0.3
            trigger_matches.append(trigger)

    if repo_stacks:
        repo_set = {s.lower() for s in repo_stacks}
        for stack in skill.applicable_stacks:
            if stack.lower() in repo_set:
                score += 0.2
                stack_matches.append(stack)

    skill_keywords = _tokenize(skill.name) | _tokenize(skill.description)
    overlap = task_tokens & skill_keywords
    if overlap:
        kw_score = min(len(overlap) * 0.05, 0.3)
        score += kw_score
        keyword_matches = sorted(overlap)

    for tag in skill.tags:
        if tag.lower() in task.lower():
            score += 0.1

    score *= skill.confidence

    reason_parts = []
    if trigger_matches:
        reason_parts.append(f"trigger match: {', '.join(trigger_matches)}")
    if stack_matches:
        reason_parts.append(f"stack match: {', '.join(stack_matches)}")
    if keyword_matches:
        reason_parts.append(f"keyword overlap: {', '.join(keyword_matches[:5])}")

    return SkillScore(
        skill_id=skill.id,
        skill_name=skill.name,
        score=round(min(score, 1.0), 3),
        relevance_reason=" | ".join(reason_parts) if reason_parts else "no match",
        trigger_matches=trigger_matches,
        stack_matches=stack_matches,
        keyword_matches=keyword_matches,
    )


def resolve_skills(
    skills: list[SkillDefinition],
    task: str,
    repo_stacks: list[str] | None = None,
    max_skills: int = 5,
) -> SkillSelectionResult:
    """Score all skills against a task and return the top matches."""
    scored = [score_skill(s, task, repo_stacks) for s in skills if s.enabled]
    scored.sort(key=lambda s: s.score, reverse=True)
    above_threshold = [s for s in scored if s.score >= _SCORE_THRESHOLD]
    selected = above_threshold[:max_skills]

    if selected:
        explanation = (
            f"Selected {len(selected)} skill(s) for task. "
            f"Top: {selected[0].skill_name} (score={selected[0].score})"
        )
    else:
        explanation = "No skills scored above threshold for this task."

    return SkillSelectionResult(
        task_description=task,
        scored_skills=scored,
        selected_skills=[s.skill_id for s in selected],
        explanation=explanation,
    )
