"""LLM execution backend — sends phase prompts to a local model via LiteLLM.

When no CLI agent (Cursor, Claude Code, Gemini) is available on the machine,
this backend sends each phase prompt directly to the local Ollama model.
The LLM response is returned as stdout so the YOLO verification pipeline
can process it normally.
"""

from __future__ import annotations

import time

from execution.backend import BackendConfig, ExecutionBackend
from execution.models import PhaseExecStatus, PhaseExecutionResult
from orchestrator.logging_setup import get_logger

logger = get_logger("llm_backend")


class LlmBackend(ExecutionBackend):
    """Executes phases by prompting a local LLM instead of a CLI agent."""

    def __init__(
        self,
        config: BackendConfig | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self._config = config or BackendConfig()
        self._model = model or self._default_model()

    @property
    def backend_id(self) -> str:
        return "llm"

    @property
    def display_name(self) -> str:
        return f"Local LLM ({self._model})"

    async def execute_phase(
        self,
        prompt: str,
        *,
        phase_id: str,
        phase_index: int,
        phase_title: str,
        working_directory: str | None = None,
        timeout_seconds: int | None = None,
        env_overrides: dict[str, str] | None = None,
    ) -> PhaseExecutionResult:
        import litellm

        result = PhaseExecutionResult(
            phase_id=phase_id,
            phase_index=phase_index,
            title=phase_title,
            backend_id=self.backend_id,
            prompt_generated=prompt,
            command_executed=f"litellm({self._model})",
            start_time=time.time(),
            status=PhaseExecStatus.executing,
        )

        timeout = timeout_seconds or self._config.timeout_seconds

        try:
            logger.info(
                "Sending phase %d/%s to %s (%d chars)",
                phase_index, phase_title, self._model, len(prompt),
            )
            response = await litellm.acompletion(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are an expert software engineer. Follow the "
                            "instructions in the prompt precisely. Provide "
                            "complete, working code when asked."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                max_tokens=4096,
                temperature=0.2,
                timeout=timeout,
            )

            text = response.choices[0].message.content or ""
            result.stdout = text
            result.stderr = ""
            result.exit_code = 0
            result.status = PhaseExecStatus.completed
            logger.info(
                "Phase %d/%s completed via LLM (%d chars response)",
                phase_index, phase_title, len(text),
            )

        except Exception as exc:
            result.stdout = ""
            result.stderr = str(exc)
            result.exit_code = 1
            result.status = PhaseExecStatus.failed
            result.error_message = f"LLM execution failed: {exc}"
            logger.error("LLM backend error for phase %d: %s", phase_index, exc)

        result.end_time = time.time()
        result.duration_seconds = result.end_time - result.start_time
        return result

    async def health_check(self) -> bool:
        try:
            import litellm

            response = await litellm.acompletion(
                model=self._model,
                messages=[{"role": "user", "content": "ping"}],
                max_tokens=5,
                timeout=10,
            )
            return bool(response.choices)
        except Exception:
            return False

    @staticmethod
    def _default_model() -> str:
        try:
            from config.config_loader import get_config

            cfg = get_config()
            return cfg.models.local_router.model_name
        except Exception:
            return "ollama/mistral"
