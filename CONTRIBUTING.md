# Contributing to ClawSmith

Welcome! Contributions are appreciated — whether it's a bug report, a new agent profile, a provider adapter, documentation improvement, or test coverage.

---

## Dev Setup

```bash
git clone https://github.com/<your-org>/Clawsmith.git
cd Clawsmith
scripts\windows\install.bat
scripts\windows\run_tests.bat   # verify everything passes
```

---

## Code Style

ClawSmith uses **ruff** for linting and formatting and **mypy** for type checking.

| Tool | Command | Purpose |
|---|---|---|
| ruff check | `ruff check . --fix` | Lint with auto-fix |
| ruff format | `ruff format .` | Format all files |
| mypy | `mypy orchestrator/ mcp_server/ --ignore-missing-imports` | Static type checking |

Or run all three at once:

```bash
scripts\windows\lint.bat
```

Configuration lives in `pyproject.toml`:

- Line length: **100**
- Target: **Python 3.11**
- Ruff rules: `E`, `F`, `I`, `UP`, `B`
- mypy: non-strict, `ignore_missing_imports = true`

---

## Running Tests

```bash
scripts\windows\run_tests.bat
# or, with the venv activated:
pytest tests/ -v
```

Tests use `pytest-asyncio` with `asyncio_mode = "auto"` (configured in `pyproject.toml`), so async test functions are detected automatically.

---

## Adding a New Provider

No code changes required — the provider registry resolves dynamically from config.

1. Edit `config/settings.yaml` and set the desired model tier to a [LiteLLM-compatible](https://docs.litellm.ai/) model string (e.g. `anthropic/claude-3-opus`, `openrouter/meta-llama/llama-3`).
2. Set the required API key in `.env` (LiteLLM resolves keys from environment automatically).
3. Run `clawsmith doctor` to verify the tier is configured.

---

## Adding a New Agent Profile

1. Copy any YAML file from `config/agent_profiles/`.
2. Change the `name` field (must be unique across all profiles).
3. Set `task_type` to one of: `audit`, `bugfix`, `implementation`, `refactor`, `planning`, `summarization`, `debugging`, `testing`, `prompt_polish`.
4. Choose a `prompt_template` from `jobs/templates/` (or create a new one).
5. Populate `variables.OBJECTIVE` and `variables.CURSOR_PROMPT` with the instruction text.
6. Set `provider_preference` to the desired model tier.
7. Run `clawsmith doctor` to verify it loads and its template exists.

See [docs/agent_profiles.md](docs/agent_profiles.md) for the full schema reference.

---

## Adding a New Agent CLI Adapter

1. Create a new file in `agents/adapters/`, e.g. `codex_adapter.py`.
2. Extend `AgentAdapter` from `agents/base.py`.
3. Implement all abstract properties: `agent_id`, `display_name`, `executable_names`, `version_commands`, `capabilities`.
4. Implement `build_invocation()` to construct the CLI command for headless prompt execution.
5. Implement `parse_result()` to convert raw subprocess output into `AgentRunResult`.
6. Register the adapter in `AgentRegistry.register_builtins()` in `agents/registry.py`.
7. Run `clawsmith detect-agents` to verify detection works.

See [docs/architecture.md](docs/architecture.md) for the full adapter interface reference.

---

## Adding a New `.bat` Template

1. Copy an existing template from `jobs/templates/`.
2. Use `$VARIABLE` or `${VARIABLE}` placeholders (Python `string.Template` syntax).
3. Keep the standard header variables (`JOB_ID`, `ARTIFACT_DIR`, `STDOUT_LOG`, `STDERR_LOG`, `TIMEOUT_SECONDS`, `EXIT_CODE`, `_START_S`).
4. Keep the `:CHECK_TIMEOUT` subroutine and `:FINISH` label.
5. Add your custom commands between the build and test phases.
6. Reference the new template by filename in a profile's `prompt_template` field.

See [docs/bat_templates.md](docs/bat_templates.md) for details on variables, security, and the allowlist.

---

## PR Guidelines

- **One feature or fix per PR.** Keep changes focused and reviewable.
- **Tests required** for routing logic, validation, and any new schemas.
- **Docs updated** if behaviour changes — especially CLI flags, config keys, or profile schema.
- **`scripts\windows\lint.bat` must pass** before submitting. CI will reject PRs that fail linting.

---

## Issue Templates

When filing an issue, please include:

### Bug Report

- Steps to reproduce
- Output of `clawsmith doctor`
- OS version and Python version (`python --version`)
- Relevant `.env` keys (redact secrets)
- Full error traceback if available

### Feature Request

- Use case and motivation
- Proposed interface (CLI flags, config keys, YAML schema)
- Any related issues or prior art
