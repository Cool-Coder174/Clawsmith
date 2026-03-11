"""Base abstractions for LLM providers.

Every provider (LiteLLM, future OpenRouter direct, etc.) implements
``ModelProvider``.  ``CompletionResult`` is the uniform response type
returned from any ``complete()`` call.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel


class CompletionResult(BaseModel):
    """Uniform response from any LLM provider call."""

    text: str
    input_tokens: int
    output_tokens: int
    model: str
    cost_estimate: float
    latency_ms: float


class ProviderError(Exception):
    """Wraps provider-level failures."""


class ModelProvider(ABC):
    """Interface that all LLM providers must implement."""

    @abstractmethod
    async def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> CompletionResult: ...

    @abstractmethod
    def supports_model(self, model_name: str) -> bool: ...

    @abstractmethod
    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float: ...
