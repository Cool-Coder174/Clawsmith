from __future__ import annotations

from config.config_loader import get_config
from providers.base import ModelProvider
from providers.litellm_provider import LiteLLMProvider

_TIER_NAMES = ("local_router", "local_code", "premium", "prompt_polisher")


class ProviderRegistry:
    def __init__(self) -> None:
        config = get_config()
        self._providers: dict[str, ModelProvider] = {}
        for tier in _TIER_NAMES:
            model_cfg = getattr(config.models, tier)
            self._providers[tier] = LiteLLMProvider(model_cfg)

    def get_provider(self, tier: str) -> ModelProvider:
        if tier not in self._providers:
            valid = ", ".join(self._providers)
            raise KeyError(
                f"Unknown tier {tier!r}. Valid tiers: {valid}"
            )
        return self._providers[tier]

    def list_tiers(self) -> list[str]:
        return list(self._providers)


_registry: ProviderRegistry | None = None


def get_registry() -> ProviderRegistry:
    """Return the cached registry, creating it on first call."""
    global _registry  # noqa: PLW0603
    if _registry is None:
        _registry = ProviderRegistry()
    return _registry


def reset_registry() -> None:
    """Clear the cached registry (useful in tests)."""
    global _registry  # noqa: PLW0603
    _registry = None
