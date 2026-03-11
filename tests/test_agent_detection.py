"""Tests for agent CLI detection, adapter capabilities, and agent routing."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agents.adapters.claude_code_adapter import ClaudeCodeAdapter
from agents.adapters.cursor_adapter import CursorAdapter
from agents.adapters.gemini_adapter import GeminiAdapter
from agents.adapters.openclaw_adapter import OpenClawAdapter
from agents.base import DetectionResult
from agents.capabilities import AgentCapability
from agents.detector import AgentDetector
from agents.registry import AgentRegistry
from agents.router import AgentNotAvailableError, AgentRouter


class TestAdapterProperties:
    def test_cursor_adapter_properties(self):
        adapter = CursorAdapter()
        assert adapter.agent_id == "cursor"
        assert adapter.display_name == "Cursor Agent"
        assert "agent" in adapter.executable_names
        assert AgentCapability.headless_prompt in adapter.capabilities
        assert adapter.supports_headless

    def test_claude_code_adapter_properties(self):
        adapter = ClaudeCodeAdapter()
        assert adapter.agent_id == "claude_code"
        assert AgentCapability.headless_prompt in adapter.capabilities
        assert AgentCapability.model_switching in adapter.capabilities
        assert AgentCapability.json_output in adapter.capabilities
        assert adapter.supports_headless
        assert adapter.supports_model_switching
        assert adapter.supports_json_output

    def test_gemini_adapter_properties(self):
        adapter = GeminiAdapter()
        assert adapter.agent_id == "gemini_cli"
        assert AgentCapability.headless_prompt in adapter.capabilities
        assert adapter.supports_headless

    def test_openclaw_adapter_properties(self):
        adapter = OpenClawAdapter()
        assert adapter.agent_id == "openclaw"
        assert adapter.is_gateway is True
        assert AgentCapability.acp_client in adapter.capabilities
        assert adapter.supports_acp


class TestCommandTemplateGeneration:
    def test_cursor_invocation(self):
        adapter = CursorAdapter()
        with patch.dict("os.environ", {"CURSOR_CLI_PATH": "agent"}, clear=False):
            spec = adapter.build_invocation("Fix the bug", working_directory=".")
        assert spec.args[0] == "agent"
        assert "chat" in spec.args
        assert spec.env_overrides.get("CLAWSMITH_PROMPT") == "Fix the bug"

    def test_claude_code_invocation(self):
        adapter = ClaudeCodeAdapter()
        spec = adapter.build_invocation("Fix the bug", model="claude-3-opus")
        assert spec.args[0] == "claude"
        assert "-p" in spec.args
        assert "Fix the bug" in spec.args
        assert "--model" in spec.args
        assert "claude-3-opus" in spec.args

    def test_claude_code_json_output(self):
        adapter = ClaudeCodeAdapter()
        spec = adapter.build_invocation("Fix", output_format="json")
        assert "--output-format" in spec.args
        assert "json" in spec.args

    def test_gemini_invocation(self):
        adapter = GeminiAdapter()
        spec = adapter.build_invocation("Implement feature")
        assert spec.args[0] == "gemini"
        assert "-p" in spec.args
        assert "Implement feature" in spec.args

    def test_openclaw_invocation(self):
        adapter = OpenClawAdapter()
        spec = adapter.build_invocation("Run task", output_format="json")
        assert spec.args[0] == "openclaw"
        assert "run" in spec.args
        assert "--prompt" in spec.args


class TestResultParsing:
    def test_cursor_success(self):
        adapter = CursorAdapter()
        result = adapter.parse_result(0, "output", "")
        assert result.success
        assert result.agent_id == "cursor"

    def test_cursor_failure(self):
        adapter = CursorAdapter()
        result = adapter.parse_result(1, "", "error msg")
        assert not result.success
        assert result.error_message == "error msg"

    def test_claude_code_success(self):
        adapter = ClaudeCodeAdapter()
        result = adapter.parse_result(0, "done", "")
        assert result.success
        assert result.agent_id == "claude_code"


class TestCapabilityLoading:
    def test_all_adapters_have_unique_ids(self):
        adapters = [CursorAdapter(), ClaudeCodeAdapter(), GeminiAdapter(), OpenClawAdapter()]
        ids = [a.agent_id for a in adapters]
        assert len(ids) == len(set(ids))

    def test_all_adapters_have_capabilities(self):
        adapters = [CursorAdapter(), ClaudeCodeAdapter(), GeminiAdapter(), OpenClawAdapter()]
        for adapter in adapters:
            assert len(adapter.capabilities) > 0

    def test_capability_matrix(self):
        registry = AgentRegistry()
        registry.register(CursorAdapter())
        registry.register(ClaudeCodeAdapter())
        matrix = registry.get_capability_matrix()
        assert "cursor" in matrix
        assert "claude_code" in matrix
        assert "capabilities" in matrix["cursor"]


class TestAgentDetection:
    def test_detector_returns_results_for_all_adapters(self):
        adapters = [CursorAdapter(), ClaudeCodeAdapter()]
        detector = AgentDetector(adapters=adapters)
        results = detector.detect_all()
        assert "cursor" in results
        assert "claude_code" in results
        for result in results.values():
            assert isinstance(result, DetectionResult)

    def test_detect_nonexistent_agent(self):
        adapters = [CursorAdapter()]
        detector = AgentDetector(adapters=adapters)
        result = detector.detect_one("nonexistent")
        assert not result.found

    @patch("shutil.which", return_value="/usr/bin/claude")
    @patch("subprocess.run")
    def test_detect_claude_on_path(self, mock_run, mock_which):
        mock_run.return_value = type("R", (), {"stdout": "1.0.0", "stderr": "", "returncode": 0})()
        adapters = [ClaudeCodeAdapter()]
        detector = AgentDetector(adapters=adapters)
        results = detector.detect_all()
        assert results["claude_code"].found


class TestAgentRouter:
    def _make_registry(self, available: list[str]) -> AgentRegistry:
        registry = AgentRegistry()
        registry.register_builtins()
        detections: dict[str, DetectionResult] = {}
        for adapter in registry.list_adapters():
            if adapter.agent_id in available:
                detections[adapter.agent_id] = DetectionResult(
                    found=True, executable_path=f"/usr/bin/{adapter.agent_id}", confidence=1.0,
                )
            else:
                detections[adapter.agent_id] = DetectionResult(found=False)
        registry._detections = detections
        return registry

    def test_selects_requested_agent(self):
        registry = self._make_registry(["cursor", "claude_code"])
        router = AgentRouter(registry)
        decision = router.select_agent(requested_agent="cursor")
        assert decision.agent_id == "cursor"
        assert not decision.fallback_used

    def test_fallback_when_requested_unavailable(self):
        registry = self._make_registry(["claude_code"])
        router = AgentRouter(registry)
        decision = router.select_agent(requested_agent="cursor")
        assert decision.agent_id == "claude_code"
        assert decision.fallback_used

    def test_respects_fallback_order(self):
        registry = self._make_registry(["gemini_cli", "claude_code"])
        router = AgentRouter(registry, fallback_order=["claude_code", "gemini_cli"])
        decision = router.select_agent()
        assert decision.agent_id == "claude_code"

    def test_uses_default_agent(self):
        registry = self._make_registry(["cursor", "claude_code"])
        router = AgentRouter(registry, default_agent="cursor")
        decision = router.select_agent()
        assert decision.agent_id == "cursor"

    def test_no_agents_raises_error(self):
        registry = self._make_registry([])
        router = AgentRouter(registry)
        with pytest.raises(AgentNotAvailableError):
            router.select_agent()

    def test_prefers_local_over_gateway(self):
        registry = self._make_registry(["openclaw", "claude_code"])
        router = AgentRouter(registry, fallback_order=["openclaw", "claude_code"])
        decision = router.select_agent(prefer_local=True)
        assert decision.agent_id == "claude_code"

    def test_gateway_used_when_only_option(self):
        registry = self._make_registry(["openclaw"])
        router = AgentRouter(registry, fallback_order=["openclaw"])
        decision = router.select_agent(prefer_local=True)
        assert decision.agent_id == "openclaw"

    def test_capability_filtering(self):
        registry = self._make_registry(["cursor", "claude_code"])
        router = AgentRouter(registry, fallback_order=["cursor", "claude_code"])
        decision = router.select_agent(needs_structured_output=True)
        assert decision.agent_id == "claude_code"
