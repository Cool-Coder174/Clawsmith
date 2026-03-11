# Contributing to ClawSmith

Contributions are welcome — bug reports, agent profiles, provider adapters, docs, or tests.

---

## Table of Contents

- [Dev Setup](#dev-setup)
- [Project Structure](#project-structure)
- [Code Style](#code-style)
- [Running Tests](#running-tests)
- [Branch & Commit Conventions](#branch--commit-conventions)
- [Adding a New Provider](#adding-a-new-provider)
- [Adding a New Agent Profile](#adding-a-new-agent-profile)
- [Adding a New Agent CLI Adapter](#adding-a-new-agent-cli-adapter)
- [PR Guidelines](#pr-guidelines)
- [Code Review Process](#code-review-process)
- [Issue Templates](#issue-templates)
- [Getting Help](#getting-help)

---

## Dev Setup

```bash
git clone https://github.com/Cool-Coder174/ClawSmith.git
cd ClawSmith
pip install -e ".[dev]"
pytest tests/ -v     # verify everything passes
```

**Prerequisites:**
- Python 3.11+ (3.12 and 3.13 also supported)
- git
- Ollama *(optional)* — for local model inference

**Recommended:** Create a virtual environment before installing.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -e ".[dev]"
```

After installing, verify the CLI works:

```bash
clawsmith doctor
```

---

## Project Structure

```
ClawSmith/
├── orchestrator/     # CLI entry point, pipeline, planner, task queue
├── agents/           # Agent CLI adapters (Cursor, Claude, Gemini, OpenClaw)
├── routing/          # Task classification, model routing, cost estimation
├── providers/        # LLM provider abstraction (LiteLLM, OpenClaw gateway)
├── tools/            # Repo auditor, mapper, context packer, build detector
├── jobs/             # Job execution, BAT generation, templates, validation
├── config/           # Settings, agent profiles, config loader
├── mcp_server/       # FastMCP server exposing tools over SSE
├── discovery/        # Hardware + toolchain detection
├── recommendation/   # Local model recommendation engine
├── install/          # Model provisioning and downloads
├── memory_skill/     # Persistent architecture/preferences memory
├── scope_engine/     # Cross-repo scope contracts
├── mutation_engine/  # Guarded config mutation pipeline
├── repo_graph/       # Workspace repo graph and linker
├── prompts/          # Prompt generation
├── tui/              # Interactive chat TUI (Rich-based)
├── tests/            # Pytest suite with fixtures
├── scripts/windows/  # Windows batch helper scripts
└── docs/             # Architecture, integration, troubleshooting docs
```

**Key entry points:**
- `orchestrator/cli.py` — all Click commands
- `orchestrator/pipeline.py` — the 10-step orchestration pipeline
- `mcp_server/server.py` — MCP tool definitions
- `orchestrator/yolo.py` — autonomous task engine

---

## Code Style

ClawSmith follows the [Python Style Guide](docs/python_style_guide.md) for all Python code.

**Tooling summary:**

| Tool | Command | Purpose |
|---|---|---|
| ruff check | `ruff check . --fix` | Lint with auto-fix |
| ruff format | `ruff format .` | Format all files |
| mypy | `mypy orchestrator/ mcp_server/ --ignore-missing-imports` | Static type checking |

Config lives in `pyproject.toml`: line length 100, target Python 3.11, rules `E`, `F`, `I`, `UP`, `B`.

**Before submitting a PR, always run:**

```bash
ruff check . --fix
ruff format .
mypy orchestrator/ mcp_server/ agents/ routing/ tools/ --ignore-missing-imports
```

---

## Running Tests

```bash
# Full suite
pytest tests/ -v

# Single test file
pytest tests/test_planner.py -v

# With coverage (requires pytest-cov)
pytest tests/ --cov=orchestrator --cov=tools --cov=routing --cov-report=term-missing
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (configured in `pyproject.toml`).

**Writing tests:**
- Place tests in `tests/test_<module>.py`.
- Use fixtures from `tests/conftest.py` (`tmp_repo`, `sample_job_spec`, `sample_config_yaml`, `sample_context_packet`).
- Mock external calls (Ollama, cloud APIs) — tests must pass offline.
- New routing, validation, or schema code must have tests.

---

## Branch & Commit Conventions

### Branches

Use descriptive branch names with a prefix:

| Prefix | Use |
|---|---|
| `feat/` | New features |
| `fix/` | Bug fixes |
| `docs/` | Documentation only |
| `refactor/` | Code refactoring (no behavior change) |
| `test/` | Adding or improving tests |
| `chore/` | Maintenance, CI, deps |

Example: `feat/unix-job-executor`, `fix/mcp-log-path`, `docs/tui-usage`

### Commits

Write clear, imperative commit messages:

```
feat: add Unix shell runner for cross-platform jobs
fix: resolve MCP logs_dir path mismatch
docs: add TUI module documentation
test: add integration tests for mutation engine
```

Keep commits atomic — one logical change per commit.

---

## Adding a New Provider

No code changes needed — the provider registry resolves dynamically from config.

1. Edit `config/settings.yaml` with a [LiteLLM-compatible](https://docs.litellm.ai/) model string.
2. Set the required API key in `.env`.
3. Run `clawsmith doctor` to verify.

---

## Adding a New Agent Profile

1. Copy any YAML from `config/agent_profiles/`.
2. Change `name` (must be unique).
3. Set `task_type`, `prompt_template`, `variables`, `provider_preference`.
4. Run `clawsmith doctor` to verify.

See [docs/agent_profiles.md](docs/agent_profiles.md) for the full schema.

---

## Adding a New Agent CLI Adapter

1. Create a file in `agents/adapters/`.
2. Extend `AgentAdapter` from `agents/base.py`.
3. Implement all abstract methods: `detect()`, `build_invocation()`, `parse_output()`.
4. Register in `agents/registry.py`.
5. Add a test in `tests/test_agent_detection.py`.
6. Run `clawsmith detect-agents` to verify.

See [docs/architecture.md](docs/architecture.md) for the adapter interface.

---

## PR Guidelines

- **One feature or fix per PR** — keep diffs focused and reviewable.
- **Tests required** for routing, validation, schemas, and new adapters.
- **Docs updated** if behavior or CLI flags change.
- **Linting must pass:** `ruff check . && ruff format --check .`
- **Fill in the PR template** with a summary, test plan, and any breaking changes.

### PR Checklist

```
- [ ] ruff check . passes
- [ ] ruff format --check . passes
- [ ] pytest tests/ -v passes
- [ ] New/changed behavior has tests
- [ ] Docs updated (if applicable)
- [ ] No secrets or .env values committed
```

---

## Code Review Process

1. Open a PR against `main`.
2. At least one maintainer review is required before merge.
3. Reviewers check for: correctness, test coverage, style compliance, and architecture fit.
4. Address review comments with new commits (don't force-push during review).
5. Once approved, the PR author squash-merges.

---

## Issue Templates

### Bug Report

- Steps to reproduce
- Output of `clawsmith doctor`
- OS and Python version
- Relevant `.env` keys (redact secrets)
- Full error traceback

### Feature Request

- Use case and motivation
- Proposed interface (CLI flags, config keys)
- Related issues or prior art

---

## Getting Help

- Open a [GitHub Issue](https://github.com/Cool-Coder174/ClawSmith/issues) for bugs or feature requests.
- Check [docs/troubleshooting.md](docs/troubleshooting.md) for common solutions.
- Run `clawsmith doctor` to diagnose environment problems.
