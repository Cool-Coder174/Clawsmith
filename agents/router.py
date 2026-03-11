"""Agent selection logic — picks the best available agent CLI for a job."""

from __future__ import annotations

from dataclasses import dataclass

from agents.base import AgentAdapter
from agents.capabilities import AgentCapability
from agents.registry import AgentRegistry
from orchestrator.logging_setup import get_logger

logger = get_logger("agent_router")


@dataclass(frozen=True)
class AgentRoutingDecision:
    """The result of selecting an agent CLI for a job."""

    agent_id: str
    adapter: AgentAdapter
    reasoning: str
    fallback_used: bool = False


class AgentRouter:
    """Selects the most appropriate agent CLI based on job requirements and availability."""

    def __init__(
        self,
        registry: AgentRegistry,
        default_agent: str | None = None,
        fallback_order: list[str] | None = None,
    ) -> None:
        self._registry = registry
        self._default_agent = default_agent
        self._fallback_order = fallback_order or [
            "claude_code",
            "cursor",
            "gemini_cli",
            "openclaw",
        ]

    def select_agent(
        self,
        *,
        requested_agent: str | None = None,
        required_capabilities: frozenset[AgentCapability] | None = None,
        needs_headless: bool = True,
        needs_structured_output: bool = False,
        needs_mcp: bool = False,
        needs_acp: bool = False,
        prefer_local: bool = True,
    ) -> AgentRoutingDecision:
        """Choose the best available agent.

        Priority:
        1. Explicit ``requested_agent`` if available
        2. Configured ``default_agent`` if available
        3. Best match from ``fallback_order`` filtered by capabilities
        """
        if requested_agent:
            adapter = self._registry.get_adapter(requested_agent)
            if adapter and self._registry.is_available(requested_agent):
                return AgentRoutingDecision(
                    agent_id=requested_agent,
                    adapter=adapter,
                    reasoning=f"Explicitly requested agent: {requested_agent}",
                )
            logger.warning(
                "Requested agent '%s' not available; falling back.", requested_agent
            )

        if self._default_agent:
            adapter = self._registry.get_adapter(self._default_agent)
            if adapter and self._registry.is_available(self._default_agent):
                if self._meets_requirements(
                    adapter, required_capabilities, needs_headless,
                    needs_structured_output, needs_mcp, needs_acp,
                ):
                    return AgentRoutingDecision(
                        agent_id=self._default_agent,
                        adapter=adapter,
                        reasoning=f"Using configured default agent: {self._default_agent}",
                    )

        required = self._build_required_set(
            required_capabilities, needs_headless,
            needs_structured_output, needs_mcp, needs_acp,
        )

        candidates = []
        for agent_id in self._fallback_order:
            adapter = self._registry.get_adapter(agent_id)
            if not adapter or not self._registry.is_available(agent_id):
                continue
            if prefer_local and getattr(adapter, "is_gateway", False):
                continue
            if required and not required.issubset(adapter.capabilities):
                continue
            candidates.append((agent_id, adapter))

        if not candidates and prefer_local:
            for agent_id in self._fallback_order:
                adapter = self._registry.get_adapter(agent_id)
                if not adapter or not self._registry.is_available(agent_id):
                    continue
                if required and not required.issubset(adapter.capabilities):
                    continue
                candidates.append((agent_id, adapter))

        if candidates:
            agent_id, adapter = candidates[0]
            return AgentRoutingDecision(
                agent_id=agent_id,
                adapter=adapter,
                reasoning=f"Auto-selected best available agent: {agent_id}",
                fallback_used=True,
            )

        available = self._registry.available_agents()
        if available:
            agent_id = available[0]
            adapter = self._registry.get_adapter(agent_id)
            assert adapter is not None
            return AgentRoutingDecision(
                agent_id=agent_id,
                adapter=adapter,
                reasoning=(
                    f"No agent matched all requirements; fell back to: {agent_id}"
                ),
                fallback_used=True,
            )

        raise AgentNotAvailableError(
            "No agent CLI is available. Install at least one supported agent:\n"
            + "\n".join(
                f"  - {a.display_name}: {a.installation_hint}"
                for a in self._registry.list_adapters()
            )
        )

    @staticmethod
    def _meets_requirements(
        adapter: AgentAdapter,
        required_capabilities: frozenset[AgentCapability] | None,
        needs_headless: bool,
        needs_structured_output: bool,
        needs_mcp: bool,
        needs_acp: bool,
    ) -> bool:
        if needs_headless and not adapter.supports_headless:
            return False
        if needs_structured_output and not adapter.supports_json_output:
            return False
        if needs_mcp and not adapter.supports_mcp:
            return False
        if needs_acp and not adapter.supports_acp:
            return False
        if required_capabilities and not required_capabilities.issubset(adapter.capabilities):
            return False
        return True

    @staticmethod
    def _build_required_set(
        required_capabilities: frozenset[AgentCapability] | None,
        needs_headless: bool,
        needs_structured_output: bool,
        needs_mcp: bool,
        needs_acp: bool,
    ) -> frozenset[AgentCapability]:
        caps: set[AgentCapability] = set()
        if required_capabilities:
            caps.update(required_capabilities)
        if needs_headless:
            caps.add(AgentCapability.headless_prompt)
        if needs_structured_output:
            caps.add(AgentCapability.json_output)
        if needs_mcp:
            caps.add(AgentCapability.mcp_client)
        if needs_acp:
            caps.add(AgentCapability.acp_client)
        return frozenset(caps)


class AgentNotAvailableError(RuntimeError):
    """Raised when no suitable agent CLI is available for a job."""
