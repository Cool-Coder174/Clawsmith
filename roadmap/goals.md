# ClawSmith Roadmap

Organized by phase. Each goal includes a priority, brief rationale, and acceptance criteria.

---

## Phase 1 — Foundation (v0.2)

### 1.1 CI/CD Pipeline
**Priority:** Critical

Set up GitHub Actions to run lint, type checking, and tests on every push and PR.

- [ ] `.github/workflows/ci.yml` with ruff, mypy, pytest
- [ ] Matrix: Python 3.11, 3.12, 3.13 on ubuntu-latest + windows-latest
- [ ] Status badge in README.md
- [ ] Branch protection requiring CI pass before merge

### 1.2 Cross-Platform Job Execution
**Priority:** Critical

Replace Windows-only `.bat` job execution with a platform-aware runner.

- [ ] Abstract `ShellRunner` with Windows (`cmd.exe`) and Unix (`/bin/sh`) backends
- [ ] `.sh.template` equivalents for every `.bat.template`
- [ ] `bat_generator.py` → `script_generator.py` with platform dispatch
- [ ] Tests passing on both Windows and Ubuntu

### 1.3 Pre-Commit Hooks
**Priority:** High

Add `.pre-commit-config.yaml` so contributors catch issues before commit.

- [ ] Hooks: ruff lint, ruff format, mypy, check-yaml, trailing-whitespace
- [ ] Documented in CONTRIBUTING.md

---

## Phase 2 — Quality & Reliability (v0.3)

### 2.1 Enable mypy Strict Mode
**Priority:** High

Upgrade from `strict = false` to `strict = true` and fix all revealed issues.

- [ ] All public APIs fully typed
- [ ] mypy strict passes in CI
- [ ] No `type: ignore` without a justification comment

### 2.2 Code Coverage Tracking
**Priority:** High

Measure test coverage and identify untested modules.

- [ ] Add `pytest-cov` to dev dependencies
- [ ] Coverage report in CI output
- [ ] Target: 70% overall, 90% for routing and validation
- [ ] Coverage badge in README.md

### 2.3 Docstring Completeness
**Priority:** Medium

Ensure all public classes and functions have Google-style docstrings.

- [ ] Enable ruff `D` rules (pydocstyle)
- [ ] All `orchestrator/`, `routing/`, `tools/`, `agents/` modules documented
- [ ] Update style guide with docstring requirement

### 2.4 Narrow Exception Handling
**Priority:** Medium

Audit broad `except Exception` blocks and replace with specific catches.

- [ ] Pipeline steps catch domain-specific exceptions
- [ ] MCP tools return structured error JSON for expected failures
- [ ] Only CLI top-level handlers use broad catches

---

## Phase 3 — Scalability & UX (v0.4)

### 3.1 Split CLI into Command Groups
**Priority:** Medium

Break `orchestrator/cli.py` (~860 lines) into focused modules.

- [ ] `orchestrator/cli/` package with `core.py`, `chat.py`, `memory.py`, `mutation.py`
- [ ] Shared options and context in `__init__.py`
- [ ] No behavior change — pure refactor

### 3.2 Integration Test Harness
**Priority:** Medium

Add end-to-end tests that exercise the full pipeline and MCP server.

- [ ] Test: MCP server starts, accepts tool call, returns valid response
- [ ] Test: Pipeline runs with mocked provider and produces ExecutionResult
- [ ] Separate from unit tests (slow marker or separate directory)

### 3.3 TUI Improvements
**Priority:** Medium

Polish the interactive chat TUI for daily use.

- [ ] Command history and tab completion
- [ ] Streaming response display
- [ ] `/help` with usage examples for all commands
- [ ] Configurable theme via settings.yaml

### 3.4 OpenClaw Naming Cleanup
**Priority:** Low

Resolve the duplicate `openclaw_adapter.py` naming between agents and providers.

- [ ] Rename `providers/openclaw_adapter.py` → `providers/openclaw_gateway.py`
- [ ] Update all internal imports and references
- [ ] Document the distinction in architecture.md

---

## Phase 4 — Ecosystem & Growth (v0.5+)

### 4.1 Plugin Architecture
**Priority:** Medium

Allow third-party agent adapters and providers without modifying core code.

- [ ] Plugin discovery via entry points or a `plugins/` directory
- [ ] Plugin interface documented with examples
- [ ] At least one adapter extracted as a reference plugin

### 4.2 Web Dashboard
**Priority:** Low

Optional browser UI for monitoring tasks, viewing routing decisions, and browsing logs.

- [ ] FastAPI backend exposing pipeline status
- [ ] Lightweight frontend (or Rich-based live dashboard)
- [ ] Task history with filtering and search

### 4.3 Changelog & Release Automation
**Priority:** Low

Automate release notes from commit history.

- [ ] CHANGELOG.md following Keep a Changelog format
- [ ] GitHub Actions release workflow (tag → build → publish to PyPI)
- [ ] Conventional commit enforcement via commitlint or similar

### 4.4 Security Hardening
**Priority:** Low

Add dependency and source security scanning.

- [ ] `pip-audit` in CI
- [ ] GitHub Dependabot enabled
- [ ] Bandit for static security analysis
- [ ] Document security policy in SECURITY.md

### 4.5 API Reference Documentation
**Priority:** Low

Auto-generated docs from docstrings.

- [ ] mkdocs or Sphinx with autodoc
- [ ] Hosted on GitHub Pages
- [ ] Linked from README.md

---

## Long-Term Vision

- **Multi-machine orchestration** — distribute tasks across local and remote agents.
- **Learning loop** — track which model tiers succeed for which task types and adjust routing thresholds over time.
- **Cost dashboard** — real-time tracking of API spend vs. local inference savings.
- **Agent marketplace** — community-contributed agent profiles and routing strategies.
