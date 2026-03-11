# Contributing to ClawSmith

Contributions are welcome — bug reports, agent profiles, provider adapters, docs, or tests.

---

## Dev Setup

```bash
git clone https://github.com/Cool-Coder174/ClawSmith.git
cd ClawSmith
pip install -e ".[dev]"
pytest tests/ -v     # verify everything passes
```

---

## Code Style

ClawSmith uses **ruff** for linting/formatting and **mypy** for type checking.

| Tool | Command | Purpose |
|---|---|---|
| ruff check | `ruff check . --fix` | Lint with auto-fix |
| ruff format | `ruff format .` | Format all files |
| mypy | `mypy orchestrator/ mcp_server/ --ignore-missing-imports` | Static type checking |

Config lives in `pyproject.toml`: line length 100, target Python 3.11, rules `E`, `F`, `I`, `UP`, `B`.

---

## Running Tests

```bash
pytest tests/ -v
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (configured in `pyproject.toml`).

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
3. Implement all abstract methods.
4. Register in `agents/registry.py`.
5. Run `clawsmith detect-agents` to verify.

See [docs/architecture.md](docs/architecture.md) for the adapter interface.

---

## PR Guidelines

- One feature or fix per PR
- Tests required for routing, validation, and new schemas
- Docs updated if behavior changes
- `ruff check . && ruff format --check .` must pass

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
