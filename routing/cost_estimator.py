"""Cost estimator — projects USD cost across all model tiers for a given task.

Used by the MCP ``cost_estimate`` tool and the TUI to show users what each
tier would cost *before* they commit to running a task.
"""

from __future__ import annotations

from pydantic import BaseModel

from config.config_loader import ModelConfig, get_config
from orchestrator.schemas import ContextPacket

_TIER_NAMES = ("local_router", "local_code", "premium", "prompt_polisher")


class TierCostEstimate(BaseModel):
    tier: str
    model_name: str
    estimated_input_tokens: int
    estimated_output_tokens: int
    estimated_cost_usd: float


class CostEstimator:
    """Estimates the dollar cost of running a task on each available model tier."""

    def __init__(self) -> None:
        self.models_config = get_config().models

    @staticmethod
    def _compute_cost(
        model_cfg: ModelConfig, input_tokens: int, output_tokens: int
    ) -> float:
        """Compute USD cost from configured per-token pricing."""
        input_price = model_cfg.input_cost_per_token
        output_price = model_cfg.output_cost_per_token
        if input_price is None or output_price is None:
            return 0.0
        return (input_tokens * input_price) + (output_tokens * output_price)

    def estimate(
        self,
        task_description: str,
        context_size_tokens: int = 0,
        expected_output_tokens: int = 500,
    ) -> list[TierCostEstimate]:
        """Return per-tier cost estimates sorted cheapest-first."""
        results: list[TierCostEstimate] = []
        input_tokens = context_size_tokens + len(task_description.split()) * 2

        for tier in _TIER_NAMES:
            model_cfg = getattr(self.models_config, tier)
            cost = self._compute_cost(model_cfg, input_tokens, expected_output_tokens)
            results.append(
                TierCostEstimate(
                    tier=tier,
                    model_name=model_cfg.model_name,
                    estimated_input_tokens=input_tokens,
                    estimated_output_tokens=expected_output_tokens,
                    estimated_cost_usd=cost,
                )
            )

        results.sort(key=lambda e: e.estimated_cost_usd)
        return results

    def estimate_from_context(
        self,
        task_description: str,
        context: ContextPacket,
        expected_output_tokens: int = 500,
    ) -> list[TierCostEstimate]:
        return self.estimate(
            task_description,
            context_size_tokens=context.token_estimate,
            expected_output_tokens=expected_output_tokens,
        )
