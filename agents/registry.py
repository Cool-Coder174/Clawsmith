"""Central registry of agent CLI adapters and their detection status."""

from __future__ import annotations

from agents.base import AgentAdapter, DetectionResult
from agents.detector import AgentDetector
from orchestrator.logging_setup import get_logger

logger = get_logger("agent_registry")


class AgentRegistry:
    """Manages available agent adapters and their detection state."""

    def __init__(self) -> None:
        self._adapters: dict[str, AgentAdapter] = {}
        self._detections: dict[str, DetectionResult] = {}

    def register(self, adapter: AgentAdapter) -> None:
        self._adapters[adapter.agent_id] = adapter

    def register_builtins(self) -> None:
        from agents.adapters.claude_code_adapter import ClaudeCodeAdapter
        from agents.adapters.cursor_adapter import CursorAdapter
        from agents.adapters.gemini_adapter import GeminiAdapter
        from agents.adapters.openclaw_adapter import OpenClawAdapter

        for adapter in [CursorAdapter(), ClaudeCodeAdapter(), GeminiAdapter(), OpenClawAdapter()]:
            self.register(adapter)

    def run_detection(self, extra_paths: list[str] | None = None) -> dict[str, DetectionResult]:
        """Run detection for all registered adapters and cache results."""
        adapters = list(self._adapters.values())
        detector = AgentDetector(adapters=adapters, extra_paths=extra_paths)
        self._detections = detector.detect_all()
        for agent_id, result in self._detections.items():
            if result.found:
                logger.info(
                    "Detected %s at %s (version: %s)",
                    agent_id,
                    result.executable_path,
                    result.version or "unknown",
                )
            else:
                logger.debug("Not found: %s — %s", agent_id, result.notes)
        return self._detections

    def get_adapter(self, agent_id: str) -> AgentAdapter | None:
        return self._adapters.get(agent_id)

    def get_detection(self, agent_id: str) -> DetectionResult | None:
        return self._detections.get(agent_id)

    def is_available(self, agent_id: str) -> bool:
        detection = self._detections.get(agent_id)
        adapter = self._adapters.get(agent_id)
        if not detection or not adapter:
            return False
        return adapter.validate_availability(detection)

    def available_agents(self) -> list[str]:
        return [aid for aid in self._adapters if self.is_available(aid)]

    def all_agents(self) -> list[str]:
        return list(self._adapters)

    def list_adapters(self) -> list[AgentAdapter]:
        return list(self._adapters.values())

    def get_capability_matrix(self) -> dict[str, dict]:
        """Return a machine-readable capability matrix for all adapters."""
        matrix: dict[str, dict] = {}
        for agent_id, adapter in self._adapters.items():
            detection = self._detections.get(agent_id)
            matrix[agent_id] = {
                "display_name": adapter.display_name,
                "capabilities": sorted(c.value for c in adapter.capabilities),
                "available": self.is_available(agent_id),
                "executable": detection.executable_path if detection else None,
                "version": detection.version if detection else None,
                "supports_headless": adapter.supports_headless,
                "supports_model_switching": adapter.supports_model_switching,
                "supports_json_output": adapter.supports_json_output,
                "supports_mcp": adapter.supports_mcp,
                "supports_acp": adapter.supports_acp,
            }
        return matrix


_registry: AgentRegistry | None = None


def get_agent_registry(auto_detect: bool = True) -> AgentRegistry:
    """Return the cached agent registry, initialising on first call."""
    global _registry
    if _registry is None:
        _registry = AgentRegistry()
        _registry.register_builtins()
        if auto_detect:
            _registry.run_detection()
    return _registry


def reset_agent_registry() -> None:
    """Clear the cached registry (useful in tests)."""
    global _registry
    _registry = None
