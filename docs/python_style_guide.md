# Python Style Guide — ClawSmith

This guide defines the Python conventions for all ClawSmith code. It supplements ruff and mypy enforcement with intent and rationale.

---

## Tooling

All style rules are enforced or auto-fixed by the configured toolchain:

| Tool | Config | Purpose |
|---|---|---|
| **ruff** (lint) | `pyproject.toml` — rules `E`, `F`, `I`, `UP`, `B` | Pyflakes, pycodestyle, isort, pyupgrade, bugbear |
| **ruff** (format) | `pyproject.toml` — line length 100 | Black-compatible formatting |
| **mypy** | `pyproject.toml` — Python 3.11, `ignore_missing_imports = true` | Static type checking |

Run before every commit:

```bash
ruff check . --fix
ruff format .
mypy orchestrator/ mcp_server/ agents/ routing/ tools/ --ignore-missing-imports
```

---

## General Principles

1. **Readability over cleverness.** Write code that a new contributor can understand in one read.
2. **Explicit over implicit.** Prefer named arguments, type hints, and clear variable names over compact idioms.
3. **Fail fast.** Validate inputs early. Raise specific exceptions. Don't silently return `None` on error.
4. **Small functions.** If a function exceeds ~40 lines, consider splitting.

---

## Formatting

- **Line length:** 100 characters (enforced by ruff).
- **Indentation:** 4 spaces (no tabs).
- **Quotes:** Double quotes for strings (ruff format default).
- **Trailing commas:** Use in multi-line collections and function signatures.
- **Blank lines:** Two between top-level definitions. One between methods.

```python
from __future__ import annotations

from pathlib import Path
from typing import Any


def process_result(
    data: dict[str, Any],
    output_dir: Path,
    *,
    verbose: bool = False,
) -> Path:
    ...
```

---

## Imports

Ruff handles import sorting (isort rules). Follow this order:

1. `from __future__ import annotations` (always first)
2. Standard library
3. Third-party packages
4. ClawSmith internal modules

```python
from __future__ import annotations

import json
import os
from pathlib import Path

import click
import httpx
from pydantic import BaseModel

from orchestrator.schemas import JobSpec
from tools.repo_auditor import RepoAuditor
```

- Use `from __future__ import annotations` in every module. This enables PEP 604 union syntax (`X | None`) and deferred evaluation.
- Avoid wildcard imports (`from module import *`).
- Prefer `from x import Y` over `import x` when only one or two names are needed.

---

## Type Hints

All public functions and methods must have type annotations on parameters and return values.

```python
# Good
def classify_task(description: str, repo_path: Path) -> TaskClassification:
    ...

# Good — use | None instead of Optional
def get_config(path: Path | None = None) -> Settings:
    ...

# Avoid
def classify_task(description, repo_path):  # missing hints
    ...
```

**Collections:** Use lowercase generics (`list[str]`, `dict[str, Any]`) — the `from __future__ import annotations` import enables this on Python 3.11+.

**Complex types:** Define type aliases at module level.

```python
ModelTier = str  # "local_router" | "local_code" | "premium"
ToolResult = dict[str, Any]
```

---

## Naming Conventions

| Entity | Convention | Example |
|---|---|---|
| Modules | `snake_case` | `repo_auditor.py` |
| Classes | `PascalCase` | `TaskClassifier`, `RepoAuditor` |
| Functions / methods | `snake_case` | `classify_task()`, `detect_hardware()` |
| Constants | `UPPER_SNAKE_CASE` | `DEFAULT_MAX_TOKENS`, `SUPPORTED_AGENTS` |
| Private | `_leading_underscore` | `_resolve_model()`, `_cache` |
| Type aliases | `PascalCase` | `RoutingResult`, `ModelTier` |

- Avoid single-letter names except in comprehensions and lambdas (`x`, `i`, `k`, `v`).
- Boolean variables and parameters: use `is_`, `has_`, `should_`, `can_` prefixes.

---

## Pydantic Models

ClawSmith uses Pydantic v2 for all structured data flowing through the pipeline.

```python
from __future__ import annotations

from pydantic import BaseModel, Field


class TaskClassification(BaseModel):
    complexity: float = Field(ge=0.0, le=1.0)
    ambiguity: float = Field(ge=0.0, le=1.0)
    task_type: str
    severity: str = "normal"
```

**Conventions:**
- Inherit from `BaseModel` (not dataclasses) for pipeline data.
- Use `Field(...)` for constraints and descriptions.
- Keep models in a `models.py` or `schemas.py` within their package.
- Avoid business logic in model classes — models are data carriers.

---

## Error Handling

```python
# Good — specific exception, informative message
if not config_path.exists():
    raise ConfigurationError(f"Config not found: {config_path}")

# Good — catch specific, re-raise or log
try:
    result = await provider.complete(prompt)
except httpx.TimeoutException:
    logger.warning("Provider timed out, retrying...")
    raise

# Avoid — bare except or overly broad catch in library code
try:
    result = do_work()
except Exception:
    pass  # swallows real bugs
```

**Guidelines:**
- Use project-specific exceptions (`ConfigurationError`, `ProviderError`, `ValidationError`, `AgentNotAvailableError`).
- Broad `except Exception` is acceptable **only** in CLI top-level handlers where you need to show a friendly error and exit.
- Always log the exception before re-raising or returning an error response.
- In MCP tools, return structured error JSON rather than raising.

---

## Docstrings

Use Google-style docstrings on all public classes and functions.

```python
def pack_context(
    audit: AuditResult,
    repo_map: RepoMap,
    *,
    max_tokens: int = 4096,
) -> ContextPacket:
    """Assemble token-budgeted context from audit and repo map.

    Prioritizes entrypoints and config files, truncating large files
    to fit within the token budget.

    Args:
        audit: Output of RepoAuditor.
        repo_map: Output of RepoMapper.
        max_tokens: Maximum token budget for the packed context.

    Returns:
        A ContextPacket ready for prompt injection.

    Raises:
        ValueError: If max_tokens is less than 256.
    """
```

- **Module docstrings:** One-liner describing the module's purpose.
- **Class docstrings:** Describe what the class represents and its key responsibilities.
- **Private functions:** Docstrings optional but encouraged for complex logic.
- Don't restate what's obvious from the signature. Focus on *why* and *how*, not *what*.

---

## Async Code

ClawSmith uses `asyncio` for providers, MCP server, and parts of the pipeline.

```python
# Good — async function with typed return
async def complete(self, prompt: str, *, max_tokens: int = 4096) -> CompletionResult:
    ...

# Good — use asyncio.gather for concurrent independent calls
results = await asyncio.gather(
    provider_a.complete(prompt),
    provider_b.complete(prompt),
)
```

- Prefer `async def` over sync wrappers when the underlying call is I/O-bound.
- Use `asyncio.gather()` for concurrent independent operations.
- Tests use `pytest-asyncio` with `asyncio_mode = "auto"` — async test functions just work.

---

## Logging

Use `logging` via the project's `logging_setup` module.

```python
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def detect_agents() -> list[str]:
    logger.info("Scanning PATH for agent CLIs...")
    ...
    logger.debug("Found %d agents", len(agents))
```

- Use `__name__` as the logger name.
- Levels: `DEBUG` for internal tracing, `INFO` for user-relevant events, `WARNING` for recoverable issues, `ERROR` for failures.
- Use `%s` formatting in log calls (not f-strings) — avoids string interpolation when the log level is disabled.

---

## Testing Conventions

- Test files: `tests/test_<module>.py`.
- Use fixtures from `conftest.py` for shared setup.
- Mock external calls (Ollama, cloud APIs, filesystem) — tests must pass offline and without API keys.
- Test names: `test_<function>_<scenario>` (e.g., `test_classify_task_high_complexity`).
- Assert one concept per test. Use descriptive assertion messages for non-obvious checks.

```python
def test_classify_task_high_complexity(sample_context_packet):
    result = classify_task("Refactor the entire database layer", sample_context_packet)
    assert result.complexity >= 0.7, "Complex refactor should score high complexity"
    assert result.task_type == "refactor"
```

---

## File & Module Organization

- One class per file when the class is substantial (>100 lines).
- Group related small classes in a shared `models.py` or `schemas.py`.
- Each package has an `__init__.py` that exports its public API.
- Keep `__init__.py` files minimal — re-exports only, no logic.

---

## Summary Checklist

Before submitting code:

- [ ] `from __future__ import annotations` at top of every module
- [ ] Type hints on all public function parameters and returns
- [ ] Google-style docstrings on public classes and functions
- [ ] `ruff check . --fix` passes
- [ ] `ruff format --check .` passes
- [ ] `mypy` passes on touched modules
- [ ] Tests added for new logic
- [ ] No broad `except Exception` in library code
- [ ] Logging uses `logger = logging.getLogger(__name__)`
