"""Tests for tui.llm_chat — especially the content-serialised tool-call fallback.

Regression coverage for the bug where local Ollama/LiteLLM models return
tool invocations as JSON text in ``message.content`` instead of the
structured ``tool_calls`` field, causing ClawSmith to print raw JSON
back to the user instead of executing the requested tool.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import tui.llm_chat as llm_chat_mod
from tui.llm_chat import ChatBrain, _try_parse_content_tool_calls

# -----------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------

_VALID_NAMES = frozenset({
    "repo_audit", "repo_map", "detect_agents",
    "run_build", "run_tests", "run_task_pipeline", "run_yolo",
})


def _make_response(content: str | None, tool_calls: list | None = None):
    """Build a minimal LiteLLM-style response object."""
    msg = SimpleNamespace(content=content, tool_calls=tool_calls)
    msg.model_dump = lambda: {
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
    }
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)])


# -----------------------------------------------------------------------
# _try_parse_content_tool_calls  — unit tests
# -----------------------------------------------------------------------

class TestTryParseContentToolCalls:
    def test_envelope_with_tool_calls_key(self):
        payload = json.dumps({"Tool Calls": [{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "repo_audit",
                "arguments": {"repo_path": "/tmp/repo"},
            },
        }]})
        result = _try_parse_content_tool_calls(payload, _VALID_NAMES)
        assert result is not None
        assert len(result) == 1
        assert result[0].function.name == "repo_audit"
        assert json.loads(result[0].function.arguments) == {"repo_path": "/tmp/repo"}

    def test_lowercase_tool_calls_key(self):
        payload = json.dumps({"tool_calls": [{
            "id": "call_2",
            "type": "function",
            "function": {
                "name": "run_yolo",
                "arguments": {"goal": "fix it", "repo_path": "/repo"},
            },
        }]})
        result = _try_parse_content_tool_calls(payload, _VALID_NAMES)
        assert result is not None
        assert result[0].function.name == "run_yolo"

    def test_camel_case_tool_calls_key(self):
        payload = json.dumps({"toolCalls": [{
            "id": "call_3",
            "type": "function",
            "function": {
                "name": "detect_agents",
                "arguments": {},
            },
        }]})
        result = _try_parse_content_tool_calls(payload, _VALID_NAMES)
        assert result is not None
        assert result[0].function.name == "detect_agents"

    def test_bare_list_format(self):
        payload = json.dumps([{
            "id": "call_4",
            "type": "function",
            "function": {
                "name": "repo_map",
                "arguments": {"repo_path": "/tmp"},
            },
        }])
        result = _try_parse_content_tool_calls(payload, _VALID_NAMES)
        assert result is not None
        assert result[0].function.name == "repo_map"

    def test_markdown_fenced_json(self):
        payload = "```json\n" + json.dumps({"Tool Calls": [{
            "id": "call_5",
            "type": "function",
            "function": {
                "name": "run_tests",
                "arguments": {"repo_path": "/tmp"},
            },
        }]}) + "\n```"
        result = _try_parse_content_tool_calls(payload, _VALID_NAMES)
        assert result is not None
        assert result[0].function.name == "run_tests"

    def test_string_arguments_preserved(self):
        payload = json.dumps({"Tool Calls": [{
            "id": "call_6",
            "type": "function",
            "function": {
                "name": "repo_audit",
                "arguments": '{"repo_path": "/tmp"}',
            },
        }]})
        result = _try_parse_content_tool_calls(payload, _VALID_NAMES)
        assert result is not None
        assert result[0].function.arguments == '{"repo_path": "/tmp"}'

    def test_missing_id_gets_synthetic(self):
        payload = json.dumps({"Tool Calls": [{
            "type": "function",
            "function": {
                "name": "repo_audit",
                "arguments": {"repo_path": "/tmp"},
            },
        }]})
        result = _try_parse_content_tool_calls(payload, _VALID_NAMES)
        assert result is not None
        assert result[0].id == "content_tc_0"

    def test_invalid_tool_name_returns_none(self):
        payload = json.dumps({"Tool Calls": [{
            "id": "call_x",
            "type": "function",
            "function": {
                "name": "not_a_real_tool",
                "arguments": {},
            },
        }]})
        assert _try_parse_content_tool_calls(payload, _VALID_NAMES) is None

    def test_plain_text_returns_none(self):
        assert _try_parse_content_tool_calls("Hello there!", _VALID_NAMES) is None

    def test_empty_string_returns_none(self):
        assert _try_parse_content_tool_calls("", _VALID_NAMES) is None

    def test_none_returns_none(self):
        assert _try_parse_content_tool_calls(None, _VALID_NAMES) is None

    def test_random_json_object_returns_none(self):
        payload = json.dumps({"name": "Alice", "age": 30})
        assert _try_parse_content_tool_calls(payload, _VALID_NAMES) is None

    def test_empty_list_returns_none(self):
        assert _try_parse_content_tool_calls("[]", _VALID_NAMES) is None

    def test_malformed_entry_returns_none(self):
        payload = json.dumps({"Tool Calls": ["not a dict"]})
        assert _try_parse_content_tool_calls(payload, _VALID_NAMES) is None

    def test_missing_function_key_returns_none(self):
        payload = json.dumps({"Tool Calls": [{"id": "x"}]})
        assert _try_parse_content_tool_calls(payload, _VALID_NAMES) is None


# -----------------------------------------------------------------------
# ChatBrain.respond  — integration tests
# -----------------------------------------------------------------------

class TestRespondContentToolCalls:
    """Verify that serialised tool calls in content are executed, not echoed."""

    @pytest.mark.asyncio
    async def test_content_serialised_tool_call_is_executed(self, tmp_path: Path):
        """The core regression case: model returns a Tool Calls JSON blob
        in content instead of structured tool_calls.  The tool must run
        and the final reply must be a natural-language summary, not JSON."""
        serialised = json.dumps({"Tool Calls": [{
            "id": "call_abc",
            "type": "function",
            "function": {
                "name": "repo_audit",
                "arguments": json.dumps({"repo_path": str(tmp_path)}),
            },
        }]})

        resp_tool = _make_response(content=serialised, tool_calls=None)
        resp_final = _make_response(content="Audit complete — looks good!")

        call_count = 0
        async def fake_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            return resp_tool if call_count == 1 else resp_final

        mock_audit = AsyncMock(return_value='{"languages": ["python"]}')
        brain = ChatBrain(repo_path=tmp_path, model="ollama/mistral")

        saved = llm_chat_mod._TOOL_DISPATCH["repo_audit"]
        llm_chat_mod._TOOL_DISPATCH["repo_audit"] = mock_audit
        try:
            with patch("litellm.acompletion", side_effect=fake_completion):
                reply = await brain.respond("audit my repo please")
        finally:
            llm_chat_mod._TOOL_DISPATCH["repo_audit"] = saved

        mock_audit.assert_called_once_with(repo_path=str(tmp_path))
        assert reply == "Audit complete — looks good!"
        assert "Tool Calls" not in reply

    @pytest.mark.asyncio
    async def test_structured_tool_calls_still_work(self, tmp_path: Path):
        """Structured tool_calls (the normal path) must keep working."""
        tc = SimpleNamespace(
            id="call_structured",
            function=SimpleNamespace(
                name="repo_audit",
                arguments=json.dumps({"repo_path": str(tmp_path)}),
            ),
        )
        resp_tool = _make_response(content=None, tool_calls=[tc])
        resp_final = _make_response(content="Structured audit done.")

        call_count = 0
        async def fake_completion(**kwargs):
            nonlocal call_count
            call_count += 1
            return resp_tool if call_count == 1 else resp_final

        mock_audit = AsyncMock(return_value='{"languages": ["python"]}')
        brain = ChatBrain(repo_path=tmp_path, model="ollama/mistral")

        saved = llm_chat_mod._TOOL_DISPATCH["repo_audit"]
        llm_chat_mod._TOOL_DISPATCH["repo_audit"] = mock_audit
        try:
            with patch("litellm.acompletion", side_effect=fake_completion):
                reply = await brain.respond("audit the repo")
        finally:
            llm_chat_mod._TOOL_DISPATCH["repo_audit"] = saved

        mock_audit.assert_called_once()
        assert reply == "Structured audit done."

    @pytest.mark.asyncio
    async def test_plain_conversational_reply_unchanged(self, tmp_path: Path):
        """Normal text replies must pass through untouched."""
        resp = _make_response(content="Hey, I'm ClawSmith!")

        brain = ChatBrain(repo_path=tmp_path, model="ollama/mistral")

        with patch("litellm.acompletion", AsyncMock(return_value=resp)):
            reply = await brain.respond("hello")

        assert reply == "Hey, I'm ClawSmith!"

    @pytest.mark.asyncio
    async def test_content_tool_call_without_tools_flag_ignored(self, tmp_path: Path):
        """When use_tools is False (conversational), a JSON blob in content
        should NOT be parsed as a tool call — it's just model chatter."""
        serialised = json.dumps({"Tool Calls": [{
            "id": "call_sneaky",
            "type": "function",
            "function": {
                "name": "repo_audit",
                "arguments": {"repo_path": str(tmp_path)},
            },
        }]})
        resp = _make_response(content=serialised, tool_calls=None)

        brain = ChatBrain(repo_path=tmp_path, model="ollama/mistral")

        with patch("litellm.acompletion", AsyncMock(return_value=resp)):
            reply = await brain.respond("hello")

        assert "Tool Calls" in reply

    @pytest.mark.asyncio
    async def test_arbitrary_json_not_treated_as_tool_call(self, tmp_path: Path):
        """Random JSON that doesn't match the tool-call schema must pass
        through as a normal reply."""
        random_json = json.dumps({"status": "ok", "count": 42})
        resp = _make_response(content=random_json, tool_calls=None)

        brain = ChatBrain(repo_path=tmp_path, model="ollama/mistral")

        with patch("litellm.acompletion", AsyncMock(return_value=resp)):
            reply = await brain.respond("run something complex")

        assert reply == random_json
