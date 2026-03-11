from __future__ import annotations

from orchestrator.schemas import TaskType
from routing.classifier import TaskClassifier


def test_simple_task_scores_low():
    c = TaskClassifier().classify("summarize the README")
    assert c.complexity_score < 0.35
    assert c.task_type == TaskType.summarization


def test_complex_refactor_scores_high():
    c = TaskClassifier().classify(
        "refactor and redesign the entire authentication module across "
        "auth/login.py, auth/provider.py, auth/session.py, auth/tokens.py, "
        "auth/middleware.py, migrate to new provider in auth/config.py, "
        "overhaul the database layer in db/models.py, db/migrations.py, "
        "critical security risk, possibly breaking production"
    )
    assert c.complexity_score >= 0.50
    assert c.architectural_impact > 0


def test_ambiguous_task_flagged():
    c = TaskClassifier().classify(
        "maybe fix something, not sure what, could be the login, figure out the issue"
    )
    assert c.ambiguity_score > 0


def test_critical_bug_has_severity():
    c = TaskClassifier().classify(
        "critical production outage, data loss in payment system, urgent fix needed"
    )
    assert c.failure_severity > 0


def test_file_mentions_increase_files_touched():
    c = TaskClassifier().classify(
        "update orchestrator/pipeline.py and routing/router.py and tools/repo_auditor.py"
    )
    assert c.files_likely_touched >= 3


def test_context_packet_tokens_used(sample_context_packet):
    c = TaskClassifier().classify("Fix the login bug", context=sample_context_packet)
    assert c.estimated_tokens == 5000
