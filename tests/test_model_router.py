from __future__ import annotations

from config.config_loader import load_config
from orchestrator.schemas import ModelTier, TaskClassification, TaskType
from routing.router import ModelRouter


def _make_classification(
    complexity: float, ambiguity: float = 0.0, severity: float = 0.0
) -> TaskClassification:
    return TaskClassification(
        task_type=TaskType.implementation,
        complexity_score=complexity,
        files_likely_touched=1,
        ambiguity_score=ambiguity,
        architectural_impact=0.0,
        failure_severity=severity,
        estimated_tokens=500,
    )


def _router(sample_config_yaml) -> ModelRouter:
    load_config(sample_config_yaml)
    return ModelRouter()


def test_low_complexity_routes_to_local_router(sample_config_yaml):
    router = _router(sample_config_yaml)
    decision = router.route_task(_make_classification(complexity=0.1))
    assert decision.selected_tier == ModelTier.local_router


def test_medium_complexity_routes_to_local_code(sample_config_yaml):
    router = _router(sample_config_yaml)
    decision = router.route_task(_make_classification(complexity=0.5))
    assert decision.selected_tier == ModelTier.local_code


def test_high_complexity_routes_to_premium(sample_config_yaml):
    router = _router(sample_config_yaml)
    decision = router.route_task(_make_classification(complexity=0.9))
    assert decision.selected_tier == ModelTier.premium


def test_high_ambiguity_bumps_tier(sample_config_yaml):
    router = _router(sample_config_yaml)
    decision = router.route_task(_make_classification(complexity=0.1, ambiguity=0.8))
    assert decision.selected_tier != ModelTier.local_router


def test_critical_severity_overrides_to_premium(sample_config_yaml):
    router = _router(sample_config_yaml)
    decision = router.route_task(_make_classification(complexity=0.1, severity=0.9))
    assert decision.selected_tier == ModelTier.premium


def test_reasoning_string_is_populated(sample_config_yaml):
    router = _router(sample_config_yaml)
    decision = router.route_task(_make_classification(complexity=0.5))
    assert decision.reasoning
    assert "complexity=" in decision.reasoning


def test_confidence_inversely_related_to_ambiguity(sample_config_yaml):
    router = _router(sample_config_yaml)
    high_amb = router.route_task(_make_classification(complexity=0.5, ambiguity=0.8))
    low_amb = router.route_task(_make_classification(complexity=0.5, ambiguity=0.1))
    assert low_amb.confidence_score > high_amb.confidence_score
