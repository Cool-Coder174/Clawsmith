"""LLM-powered chat brain for the ClawSmith TUI.

Sends every non-slash-command message to the local LLM (Ollama/mistral by
default) with tool-calling enabled.  The LLM decides whether to answer
directly or invoke a tool, and the agentic loop keeps running until the
LLM produces a final text response.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import litellm

_REPO_ROOT = Path(__file__).resolve().parent.parent

_MAX_TOOL_ROUNDS = 6

# ---------------------------------------------------------------------------
# Lightweight conversational heuristic — keeps tools out of casual chat
# ---------------------------------------------------------------------------

_GREETING_WORDS = frozenset({
    "hello", "hi", "hey", "howdy", "yo", "sup", "greetings",
    "hola", "hiya", "heya", "morning", "afternoon", "evening",
})
_THANKS_WORDS = frozenset({"thanks", "thank", "thx", "cheers", "ty"})
_CHAT_PHRASES = (
    "what can you", "what do you do", "how can you help",
    "what are you", "who are you", "how are you",
    "how's it going", "what's up", "how do you",
    "tell me about yourself", "nice to meet",
    "good morning", "good afternoon", "good evening",
)


def _is_conversational(text: str) -> bool:
    """Return True if *text* looks like casual chat rather than a real task."""
    q = text.lower().strip()
    words = q.split()
    if not words:
        return True
    first = words[0].rstrip("!.,?;:")
    if first in _GREETING_WORDS:
        return True
    if first in _THANKS_WORDS or any(
        w.rstrip("!.,?;:") in _THANKS_WORDS for w in words
    ):
        return True
    if any(p in q for p in _CHAT_PHRASES):
        return True
    if len(words) <= 4 and not any(
        w.rstrip("!.,?;:") in {
            "fix", "add", "create", "build", "run", "test", "audit",
            "detect", "install", "delete", "remove", "deploy", "refactor",
        }
        for w in words
    ):
        return True
    return False


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_TEMPLATE = """\
You are **ClawSmith** — a sharp, local-first AI assistant forged for \
developers who keep their tools close and their models closer. You run \
directly on the user's machine, not in some distant cloud. Think of yourself \
as a digital blacksmith: precise with your tools, quick with your words, and \
always ready to get your hands dirty in a codebase.

**Your personality:**
- Confident and capable, with a dry wit — you don't waste words, but the ones \
you pick land well.
- You speak plainly. No corporate fluff, no filler paragraphs.
- You take genuine pride in craftsmanship: clean solutions, honest assessments, \
and code that doesn't make you wince.
- You're a colleague, not a servant — warm but direct. You'll push back on bad \
ideas politely.
- You have a soft spot for well-structured repos and clever automation.

Current working directory: {repo_path}

## CRITICAL — when to use tools vs. just talk

**RESPOND IN PLAIN TEXT** (no tool calls) for:
- Greetings, hellos, goodbyes, pleasantries
- Simple questions you already know the answer to
- Conversation, opinions, explanations, clarifications
- Anything that does NOT require reading the filesystem or running commands

**ONLY call a tool** when you genuinely need to:
- Inspect the repository structure or files
- Run builds or tests
- Detect installed agents
- Execute a full coding task pipeline

Your available tools are EXACTLY: repo_audit, repo_map, detect_agents, \
run_build, run_tests, run_task_pipeline, run_yolo. There are NO other tools. \
Do NOT invent tool names or call anything not in that list.

Use **run_yolo** for complex, multi-step goals that benefit from autonomous \
phase decomposition (e.g. "Add user auth with JWT", "Refactor the database layer"). \
Use **run_task_pipeline** for simpler, single-step tasks.

## After using a tool
- Summarise the result in a concise, helpful way.
- If a tool errors out, explain the problem clearly and suggest a fix.
- Use markdown formatting when it helps readability.
"""

# ---------------------------------------------------------------------------
# Tool schemas  (LiteLLM / OpenAI function-calling format)
# ---------------------------------------------------------------------------

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "repo_audit",
            "description": (
                "Audit a repository and return a report of languages, "
                "package managers, test frameworks, CI configs, and marker files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Absolute path to the repository root.",
                    },
                },
                "required": ["repo_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repo_map",
            "description": (
                "Generate a directory-tree map of a repository showing "
                "entrypoints and important files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Absolute path to the repository root.",
                    },
                },
                "required": ["repo_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "detect_agents",
            "description": (
                "Detect installed agent CLIs (Cursor, Claude, Gemini, etc.) "
                "and return a capability matrix."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_build",
            "description": (
                "Detect and run build/install commands for a repository."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Absolute path to the repository root.",
                    },
                },
                "required": ["repo_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_tests",
            "description": "Detect and run test commands for a repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo_path": {
                        "type": "string",
                        "description": "Absolute path to the repository root.",
                    },
                },
                "required": ["repo_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_task_pipeline",
            "description": (
                "Run the full ClawSmith orchestration pipeline for a coding "
                "task.  Use this for requests that require code changes, bug "
                "fixes, refactoring, or any substantial development work."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": "A natural-language description of the coding task.",
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Absolute path to the repository root.",
                    },
                },
                "required": ["task_description", "repo_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_yolo",
            "description": (
                "YOLO mode — autonomous multi-phase task execution. "
                "Decomposes a complex goal into phases, plans each one, "
                "executes them in order via the pipeline, verifies results, "
                "and retries on failure. Use this for large, multi-step goals."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "goal": {
                        "type": "string",
                        "description": "A high-level software engineering goal.",
                    },
                    "repo_path": {
                        "type": "string",
                        "description": "Absolute path to the repository root.",
                    },
                },
                "required": ["goal", "repo_path"],
            },
        },
    },
]

# ---------------------------------------------------------------------------
# Tool implementations  (call Python directly, no HTTP to MCP server)
# ---------------------------------------------------------------------------


async def _tool_repo_audit(repo_path: str) -> str:
    from tools.repo_auditor import RepoAuditor

    root = Path(repo_path).resolve()
    report = RepoAuditor(root).audit()
    return report.model_dump_json(indent=2)


async def _tool_repo_map(repo_path: str) -> str:
    from tools.repo_mapper import RepoMapper

    root = Path(repo_path).resolve()
    result = RepoMapper(root, max_lines=120).map()
    return result.model_dump_json(indent=2)


async def _tool_detect_agents() -> str:
    from agents.registry import get_agent_registry

    registry = get_agent_registry(auto_detect=True)
    matrix = registry.get_capability_matrix()
    return json.dumps(matrix, indent=2)


async def _tool_run_build(repo_path: str) -> str:
    from tools.build_detector import BuildDetector

    root = Path(repo_path).resolve()
    commands = BuildDetector(root).detect()
    commands = [c for c in commands if c.purpose in ("build", "install")]
    if not commands:
        return json.dumps({"message": "No build commands detected."})

    results = []
    for cmd in commands:
        proc = await asyncio.create_subprocess_shell(
            cmd.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(root),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        results.append({
            "command": cmd.command,
            "exit_code": proc.returncode,
            "stdout": (stdout or b"").decode("utf-8", errors="replace")[:2000],
            "stderr": (stderr or b"").decode("utf-8", errors="replace")[:2000],
        })
    return json.dumps(results, indent=2)


async def _tool_run_tests(repo_path: str) -> str:
    from tools.build_detector import BuildDetector

    root = Path(repo_path).resolve()
    commands = BuildDetector(root).detect()
    commands = [c for c in commands if c.purpose == "test"]
    if not commands:
        return json.dumps({"message": "No test commands detected."})

    results = []
    for cmd in commands:
        proc = await asyncio.create_subprocess_shell(
            cmd.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(root),
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        results.append({
            "command": cmd.command,
            "exit_code": proc.returncode,
            "stdout": (stdout or b"").decode("utf-8", errors="replace")[:4000],
            "stderr": (stderr or b"").decode("utf-8", errors="replace")[:4000],
        })
    return json.dumps(results, indent=2)


async def _tool_run_task_pipeline(task_description: str, repo_path: str) -> str:
    from orchestrator.agent_status import StatusTracker
    from orchestrator.pipeline import OrchestrationPipeline

    tracker = StatusTracker()
    result = await OrchestrationPipeline().run(
        task_description, repo_path, dry_run=False, status=tracker,
    )
    parts = [f"success: {result.success}"]
    parts.append(f"duration: {result.duration_seconds:.1f}s")
    if result.agent_status:
        parts.append(f"agent_phase: {result.agent_status.get('phase', 'unknown')}")
        parts.append(f"agent_steps: {result.agent_status.get('step_count', 0)}")
        if result.agent_status.get("verify_stage"):
            parts.append(f"verify_stage: {result.agent_status['verify_stage']}")
    if result.routing_decision:
        rd = result.routing_decision
        parts.append(f"tier: {rd.selected_tier.value} ({rd.model_name})")
    if result.execution_result:
        er = result.execution_result
        parts.append(f"exit_code: {er.exit_code}")
        if er.agent_used:
            parts.append(f"agent: {er.agent_used}")
    if result.error_message:
        parts.append(f"error: {result.error_message}")
    return "\n".join(parts)


async def _tool_run_yolo(goal: str, repo_path: str) -> str:
    from orchestrator.agent_status import StatusTracker
    from orchestrator.yolo import YoloEngine

    tracker = StatusTracker()
    result = await YoloEngine().execute(goal, repo_path, status=tracker)
    parts = [f"success: {result.success}"]
    parts.append(f"duration: {result.duration_seconds:.1f}s")
    parts.append(f"phases: {result.completed_phases}/{result.total_phases}")
    if result.failed_phases:
        parts.append(f"failed: {result.failed_phases}")
    if result.agent_status:
        parts.append(f"phase: {result.agent_status.get('phase', 'unknown')}")
    if result.phase_results:
        for pr in result.phase_results:
            parts.append(f"  [{pr.phase_index + 1}] {pr.title}: {pr.status.value}")
    if result.error_message:
        parts.append(f"error: {result.error_message}")
    return "\n".join(parts)


_TOOL_DISPATCH: dict[str, Any] = {
    "repo_audit": _tool_repo_audit,
    "repo_map": _tool_repo_map,
    "detect_agents": _tool_detect_agents,
    "run_build": _tool_run_build,
    "run_tests": _tool_run_tests,
    "run_task_pipeline": _tool_run_task_pipeline,
    "run_yolo": _tool_run_yolo,
}

# ---------------------------------------------------------------------------
# Fallback parser for tool calls serialized in message content
# ---------------------------------------------------------------------------


def _try_parse_content_tool_calls(
    content: str,
    valid_names: frozenset[str],
) -> list[SimpleNamespace] | None:
    """Parse tool-call JSON that a local model emitted as plain text.

    Some Ollama / LiteLLM models return tool invocations as a JSON blob
    inside ``message.content`` rather than the structured ``tool_calls``
    field.  This function recognises the most common serialised formats
    and converts them into lightweight objects whose shape matches
    ``response.choices[0].message.tool_calls`` entries, so the rest of
    the agentic loop can handle them identically.

    Returns a list of tool-call-like ``SimpleNamespace`` objects, or
    ``None`` when *content* does not look like a tool-call envelope.
    """
    if not content:
        return None

    text = content.strip()

    # Strip optional markdown code fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()

    if not text or text[0] not in ("{", "["):
        return None

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None

    # Unwrap {"Tool Calls": [...]}, {"tool_calls": [...]}, or bare list
    calls_list: list[dict] | None = None
    if isinstance(data, dict):
        for key in ("Tool Calls", "tool_calls", "toolCalls"):
            val = data.get(key)
            if isinstance(val, list):
                calls_list = val
                break
        if calls_list is None:
            return None
    elif isinstance(data, list):
        calls_list = data
    else:
        return None

    if not calls_list:
        return None

    parsed: list[SimpleNamespace] = []
    for entry in calls_list:
        if not isinstance(entry, dict):
            return None

        func = entry.get("function")
        if not isinstance(func, dict):
            return None

        name = func.get("name")
        if not isinstance(name, str) or name not in valid_names:
            return None

        args = func.get("arguments", {})
        if isinstance(args, dict):
            args_str = json.dumps(args)
        elif isinstance(args, str):
            args_str = args
        else:
            return None

        call_id = entry.get("id") or f"content_tc_{len(parsed)}"
        parsed.append(
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(name=name, arguments=args_str),
            )
        )

    return parsed if parsed else None


# ---------------------------------------------------------------------------
# ChatBrain
# ---------------------------------------------------------------------------


class ChatBrain:
    """Stateful LLM conversation with tool calling."""

    def __init__(
        self,
        repo_path: Path,
        model: str | None = None,
    ) -> None:
        self.repo_path = repo_path
        self.model = model or self._default_model()
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._build_system_prompt()},
        ]
        self._inject_api_keys()

    # -- public API -------------------------------------------------------

    async def respond(self, user_message: str) -> str:
        """Send *user_message* to the LLM and return its final text reply.

        Conversational messages (greetings, chitchat) are sent WITHOUT
        tool schemas so the model cannot hallucinate tool calls.  Task-like
        messages get the full tool set.  If the model hallucinates an
        invalid tool name anyway, we strip tools and retry.

        Some local models (Ollama / LiteLLM) return tool invocations as
        serialised JSON in ``message.content`` instead of the structured
        ``tool_calls`` field.  When that happens we parse the payload and
        feed it into the same execution path so the tool actually runs.
        """
        self._messages.append({"role": "user", "content": user_message})

        use_tools = not _is_conversational(user_message)
        _valid = frozenset(_TOOL_DISPATCH)

        for _ in range(_MAX_TOOL_ROUNDS):
            kwargs: dict[str, Any] = dict(
                model=self.model,
                messages=self._messages,
                max_tokens=2048,
                temperature=0.3,
            )
            if use_tools:
                kwargs["tools"] = TOOLS

            response = await litellm.acompletion(**kwargs)

            choice = response.choices[0]
            msg = choice.message

            tool_calls = getattr(msg, "tool_calls", None)

            # Fallback: detect tool calls serialised in plain-text content
            content_tool_calls: list[SimpleNamespace] | None = None
            if not tool_calls and use_tools and msg.content:
                content_tool_calls = _try_parse_content_tool_calls(
                    msg.content, _valid,
                )

            active_calls = tool_calls or content_tool_calls

            if not active_calls:
                text = msg.content or ""
                self._messages.append({"role": "assistant", "content": text})
                self._trim_history()
                return text

            if any(tc.function.name not in _TOOL_DISPATCH for tc in active_calls):
                use_tools = False
                continue

            # Record the assistant turn in conversation history
            if tool_calls:
                self._messages.append(msg.model_dump())
            else:
                self._messages.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in active_calls
                    ],
                })

            for tc in active_calls:
                result = await self._execute_tool(tc)
                self._messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

        final = self._messages[-1].get("content", "")
        if not final:
            final = "(tool loop ended without a final response)"
        return final

    # -- internals --------------------------------------------------------

    def _build_system_prompt(self) -> str:
        return _SYSTEM_TEMPLATE.format(repo_path=self.repo_path)

    @staticmethod
    def _default_model() -> str:
        try:
            from config.config_loader import get_config

            cfg = get_config()
            return cfg.models.local_router.model_name
        except Exception:
            return "ollama/mistral"

    @staticmethod
    def _inject_api_keys() -> None:
        if key := os.environ.get("OPENAI_API_KEY"):
            litellm.openai_key = key
        if key := os.environ.get("ANTHROPIC_API_KEY"):
            litellm.anthropic_key = key
        if key := os.environ.get("OPENROUTER_API_KEY"):
            litellm.openrouter_key = key

    async def _execute_tool(self, tool_call: Any) -> str:
        name = tool_call.function.name
        try:
            args = json.loads(tool_call.function.arguments)
        except (json.JSONDecodeError, TypeError):
            args = {}

        fn = _TOOL_DISPATCH.get(name)
        if fn is None:
            valid = ", ".join(_TOOL_DISPATCH.keys())
            return (
                f"ERROR: '{name}' is not a real tool. "
                f"Valid tools are: {valid}. "
                "You MUST respond to the user in plain text now. "
                "Do NOT call any more tools."
            )

        try:
            return await fn(**args)
        except Exception as exc:
            return json.dumps({"error": f"{name} failed: {exc}"})

    def _trim_history(self) -> None:
        """Keep history within a reasonable token budget.

        Preserves the system prompt (index 0) and the most recent messages.
        """
        max_messages = 40
        if len(self._messages) <= max_messages:
            return
        system = self._messages[:1]
        recent = self._messages[-(max_messages - 1):]
        self._messages = system + recent
