"""Model router — maps a task classification to the cheapest capable model tier.

Thresholds are loaded from ``config/settings.yaml``.  High ambiguity or
critical severity can bump the selected tier upward so that harder tasks
automatically escalate to more capable (and more expensive) models.
"""

from __future__ import annotations

from config.config_loader import get_config
from orchestrator.schemas import ModelTier, RoutingDecision, TaskClassification

# Tier escalation ladder: local_router → local_code → premium.
_TIER_ORDER = [ModelTier.local_router, ModelTier.local_code, ModelTier.premium]


def _bump_tier(tier: ModelTier) -> ModelTier:
    """Move one step up in the tier ladder, capping at premium."""
    idx = _TIER_ORDER.index(tier) if tier in _TIER_ORDER else len(_TIER_ORDER) - 1
    return _TIER_ORDER[min(idx + 1, len(_TIER_ORDER) - 1)]


class ModelRouter:
    """Selects a model tier and concrete model name for a classified task.

    The router compares the task's complexity score against configurable
    thresholds, then optionally bumps the tier if ambiguity is high or
    failure severity is critical.
    """

    def __init__(self) -> None:
        config = get_config()
        self.routing_config = config.routing
        self.models_config = config.models

    def route_task(self, classification: TaskClassification) -> RoutingDecision:
        """Return a ``RoutingDecision`` with the selected tier, model, and reasoning."""
        low = self.routing_config.low_complexity_threshold
        high = self.routing_config.high_complexity_threshold
        ambiguity_bump = self.routing_config.ambiguity_bump_threshold
        cs = classification.complexity_score

        if cs < low:
            selected_tier = ModelTier.local_router
        elif cs < high:
            selected_tier = ModelTier.local_code
        else:
            selected_tier = ModelTier.premium

        reasoning_parts: list[str] = [
            f"complexity={cs:.2f}"
        ]

        if cs < low:
            reasoning_parts.append(f"< low_threshold={low} → local_router")
        elif cs < high:
            reasoning_parts.append(f"< high_threshold={high} → local_code")
        else:
            reasoning_parts.append(f"≥ high_threshold={high} → premium")

        if classification.ambiguity_score > ambiguity_bump:
            prev = selected_tier
            selected_tier = _bump_tier(selected_tier)
            reasoning_parts.append(
                f"ambiguity={classification.ambiguity_score:.2f} > "
                f"bump_threshold={ambiguity_bump} → bumped {prev.value} → {selected_tier.value}"
            )

        if classification.failure_severity > 0.8:
            if selected_tier != ModelTier.premium:
                selected_tier = ModelTier.premium
                reasoning_parts.append(
                    f"failure_severity={classification.failure_severity:.2f}"
                    " > 0.8 → override to premium"
                )

        model_cfg = getattr(self.models_config, selected_tier.value)
        confidence_score = 1.0 - classification.ambiguity_score

        return RoutingDecision(
            selected_tier=selected_tier,
            model_name=model_cfg.model_name,
            provider=model_cfg.provider,
            reasoning="; ".join(reasoning_parts),
            confidence_score=confidence_score,
            estimated_tokens=classification.estimated_tokens,
        )
