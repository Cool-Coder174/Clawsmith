"""LiteLLM provider — routes completions through the litellm library.

LiteLLM abstracts 100+ LLM APIs behind a single ``acompletion`` call,
so ClawSmith can target OpenAI, Anthropic, Ollama, and others without
provider-specific code.  Retries with exponential back-off are handled
by tenacity.
"""

from __future__ import annotations

import os
import time

import litellm
from tenacity import retry, stop_after_attempt, wait_exponential

from config.config_loader import ModelConfig
from providers.base import CompletionResult, ModelProvider, ProviderError


class LiteLLMProvider(ModelProvider):
    """Concrete provider backed by the ``litellm`` library."""
    def __init__(self, model_config: ModelConfig) -> None:
        self.model_config = model_config
        self._inject_api_keys()

    @staticmethod
    def _inject_api_keys() -> None:
        """Read API keys from the environment and set them on litellm.

        Called at construction time so that keys are picked up *after*
        dotenv / config loading has populated the environment.
        """
        if key := os.environ.get("OPENAI_API_KEY"):
            litellm.openai_key = key
        if key := os.environ.get("ANTHROPIC_API_KEY"):
            litellm.anthropic_key = key
        if key := os.environ.get("OPENROUTER_API_KEY"):
            litellm.openrouter_key = key

    def supports_model(self, model_name: str) -> bool:
        return (
            model_name == self.model_config.model_name
            or model_name.startswith(self.model_config.provider)
        )

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float:
        try:
            return litellm.completion_cost(
                model=self.model_config.model_name,
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
            )
        except Exception:
            return 0.0

    async def complete(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> CompletionResult:
        return await self._complete_with_retry(
            prompt, system_prompt, max_tokens, temperature
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        reraise=True,
    )
    async def _complete_with_retry(
        self,
        prompt: str,
        system_prompt: str,
        max_tokens: int,
        temperature: float,
    ) -> CompletionResult:
        start = time.monotonic()
        try:
            messages: list[dict[str, str]] = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            response = await litellm.acompletion(
                model=self.model_config.model_name,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            text = response.choices[0].message.content or ""

            usage = response.usage
            input_tokens = usage.prompt_tokens if usage else 0
            output_tokens = usage.completion_tokens if usage else 0

            cost_estimate = self.estimate_cost(input_tokens, output_tokens)
            latency_ms = (time.monotonic() - start) * 1000

            return CompletionResult(
                text=text,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                model=self.model_config.model_name,
                cost_estimate=cost_estimate,
                latency_ms=latency_ms,
            )
        except Exception as exc:
            raise ProviderError(f"LiteLLM call failed: {exc}") from exc
