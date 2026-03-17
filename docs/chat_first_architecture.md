# Chat-First Architecture — ClawSmith

**Version:** 1.0.0
**Date:** 2026-03-16

---

## Overview

ClawSmith has been refactored from a CLI/prompt-dispatch workflow into a **chat-native orchestrator** with:

- `clawsmith chat` as the primary runtime surface
- first-class skill support (manual, generated, imported)
- auto-generated skills from repo dependencies and structure
- durable "always remember" cross-session memory
- OpenClaw interoperability for external skill surfaces
- scope and mutation guardrails on all execution
- backward compatibility for all existing CLI commands

---

## Runtime Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    User Interface                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │ clawsmith    │  │ clawsmith    │  │ clawsmith    │  │
│  │ chat (TUI)   │  │ run-task     │  │ skills ...   │  │
│  │              │  │ yolo, audit  │  │              │  │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  │
│         │                  │                  │          │
│  ┌──────┴──────────────────┴──────────────────┴───────┐  │
│  │              ChatRuntime (shared layer)             │  │
│  │   - SessionState (explicit, testable)              │  │
│  │   - Skill loading + selection + execution          │  │
│  │   - Memory retrieval + ranking                     │  │
│  │   - Scope enforcement                              │  │
│  │   - Explainability metadata                        │  │
│  └──────┬─────────────┬──────────────┬───────────────┘  │
│         │             │              │                   │
│  ┌──────┴──────┐ ┌────┴────┐ ┌──────┴──────┐           │
│  │ Skills      │ │ Memory  │ │ Execution   │           │
│  │ Registry    │ │ Retriever│ │ Backends    │           │
│  └─────────────┘ └─────────┘ └─────────────┘           │
└─────────────────────────────────────────────────────────┘
```

### Key Components

| Component | Module | Purpose |
|-----------|--------|---------|
| **ChatRuntime** | `orchestrator/chat_runtime.py` | Shared orchestration layer for all entry points |
| **SessionState** | `orchestrator/session_state.py` | Explicit, testable session state |
| **SkillRegistry** | `skills/registry.py` | Load, store, enable/disable skills |
| **SkillResolver** | `skills/resolver.py` | Score and select skills for tasks |
| **SkillGenerator** | `skills/generator.py` | Auto-generate skills from repo structure |
| **SkillExecutor** | `skills/executor.py` | Execute skills with scope guardrails |
| **MemoryRetriever** | `memory_skill/retriever.py` | Ranked memory retrieval |
| **AlwaysRemember** | `memory_skill/always_remember.py` | Durable cross-session annotations |
| **OpenClawBridge** | `skills/openclaw_adapter.py` | Skill import/export with OpenClaw |

---

## Skill System Design

### Skill Schema

Every skill is a `SkillDefinition` (Pydantic model) with:

| Field | Type | Purpose |
|-------|------|---------|
| `id` | str | Unique deterministic identifier |
| `name` | str | Human-readable name |
| `description` | str | What the skill does |
| `version` | str | Semantic version |
| `source_type` | enum | `manual`, `generated`, `dependency_derived`, `repo_derived`, `openclaw_imported` |
| `triggers` | list[str] | Keywords/phrases that activate this skill |
| `applicable_stacks` | list[str] | Technology stacks this skill applies to |
| `required_context` | list[str] | Files/data needed for execution |
| `preferred_tools` | list[str] | MCP tools this skill uses |
| `allowed_scope` | list[str] | File paths this skill may modify |
| `execution_strategy` | str | How the skill executes (`llm_guided`, `command`, `remote`) |
| `constraints` | list[str] | Guardrails and limitations |
| `acceptance_criteria` | list[str] | What constitutes success |
| `confidence` | float | 0.0–1.0 confidence in applicability |
| `enabled` | bool | Whether the skill is active |
| `explainability` | str | Why this skill exists |
| `inferred_commands` | list[str] | Commands the skill would run |
| `inferred_file_targets` | list[str] | Files the skill would modify |
| `generation_evidence` | list[str] | Evidence for auto-generated skills |

### Skill Sources

| Source Type | Storage Path | How Created |
|-------------|-------------|-------------|
| Manual | `.clawsmith/skills/manual/` | User-defined |
| Generated | `.clawsmith/skills/generated/` | `SkillGenerator` from repo scan |
| Imported | `.clawsmith/skills/imported/` | OpenClaw gateway import |

### Skill Selection

The `SkillResolver` scores skills against a task using:

1. **Trigger matching** — keywords in the task match skill triggers (+0.3 per match)
2. **Stack matching** — repo stacks match skill applicable_stacks (+0.2 per match)
3. **Keyword overlap** — word overlap between task and skill name/description (+0.05 per word, max 0.3)
4. **Tag matching** — skill tags found in task (+0.1 per match)
5. **Confidence weighting** — score multiplied by skill confidence

Selected skills include explainability about why they were chosen.

### Auto Skill Generation

The `SkillGenerator` scans the repository for:

| File/Pattern | Detections |
|---|---|
| `pyproject.toml` | pytest, ruff, mypy, FastAPI, Django, Flask, Pydantic, CLI frameworks |
| `package.json` | React, Next.js, Vue, Vite, TypeScript, Jest, Vitest, ESLint |
| `Cargo.toml` | Rust build and test |
| `go.mod` | Go build and test |
| `Dockerfile` / `docker-compose.yml` | Docker debug |
| `.github/workflows/*.yml` | CI pipeline debug |
| `Makefile` | Makefile target execution |
| `mcp_server/` / `openclaw_skill.yaml` | MCP/OpenClaw validation |

Each generated skill includes:
- Generation evidence (which files/deps triggered it)
- Confidence score
- Inferred commands
- Inferred file targets
- Acceptance criteria
- Constraints and safe scope

---

## Memory System Design

**"Always remember" does not mean "dump everything into context."**  It means
durable storage + ranked retrieval + explainable selection.

### Memory Types

| Type | Source | Use Case |
|------|--------|----------|
| **Repo Memory** | `clawsmith/` directory | Architecture, preferences, tooling, conventions |
| **Always Remember** | `.clawsmith/always_remember/` | User annotations, accepted outcomes, cross-session facts |
| **Cross-Repo Memory** | Linked repos via `repo_graph` | Shared conventions, related patterns |

### Typed Dimensions on Every Entry

Every `MemoryEntry` (Pydantic model) carries typed dimensions so the ranker
can score it against the current task without context flooding:

| Dimension | Field(s) | Purpose |
|-----------|----------|---------|
| **Repo** | `repo` | Which repository this memory belongs to |
| **Workspace** | `workspace` | Multi-repo workspace root |
| **Dependency stack** | `dependency_stack: list[str]` | Language/framework tags (python, fastapi, …) |
| **Workflow type** | `workflow_type` | build, test, lint, deploy, … |
| **Task category** | `task_category` | bugfix, refactor, debug, … |
| **Recency** | `created_at`, `last_accessed_at` | Timestamps for exponential decay |
| **Acceptance** | `hit_count`, `accept_count`, `usefulness_score` | How useful the memory has been |
| **Suppressed** | `suppressed` | Explicitly silenced entries are filtered out |

### Retrieval and Ranking

The ranker (`rank_entries`) applies these scoring dimensions in order:

| # | Dimension | Weight | Mechanism |
|---|-----------|--------|-----------|
| 1 | Token overlap | 0.05/word, cap 0.25 | Task words ∩ entry content words |
| 2 | Tag overlap | 0.08/tag | Task words ∩ entry tags |
| 3 | Dependency-stack match | 0.12/stack | Repo stacks ∩ entry stacks |
| 4 | Workflow-type match | 0.10 | Exact match on workflow type |
| 5 | Task-category match | 0.10 | Exact match on task category |
| 6 | Source boost | 0.10/0.06/0.03 | always_remember > repo > cross_repo |
| 7 | Repo proximity | 0.12 | Entry repo == current repo |
| 8 | Recency | 0.10 × decay | Exponential decay, half-life 30 days |
| 9 | Acceptance | 0.15 × factor | usefulness_score or accept/hit ratio |

Suppressed entries are **excluded** before scoring — they never appear in
results.  Retrieval is bounded by `max_entries` (default 10).

Each retrieved entry gets an `explanation` string describing which scoring
dimensions contributed.

### Promotion — Accepted Outcomes → Durable Memory

`AlwaysRemember.promote_outcome()` promotes an accepted task result into
persistent memory:

- If the entry already exists (same content+category), its `accept_count`
  and `usefulness_score` are incremented — no duplicate is created.
- Re-promoting an entry automatically un-suppresses it.
- Promoted entries start with `usefulness_score=0.8` so they rank high
  on their next retrieval.

### Suppression — Noise Control

`AlwaysRemember.suppress(entry_id)` marks an entry as suppressed.  It is
**not** deleted — it can be un-suppressed later with `unsuppress()`.

### Decay — Auto-Suppression of Low-Value Entries

`AlwaysRemember.decay()` scans all non-suppressed entries and auto-suppresses
those that have been retrieved often but rarely accepted:

- `hit_count >= min_hits` (default 5) — the entry has been seen enough
- `accept_count / hit_count < (1 - max_reject_ratio)` (default < 0.2) —
  almost never accepted

This ensures that noisy, low-value entries are silenced over time without
manual intervention.

### Acceptance Tracking

- `record_hit(entry_id)` — called when an entry is retrieved; increments
  `hit_count` and updates `last_accessed_at`
- `record_accept(entry_id)` — called when the user accepts a task outcome
  that used this memory; increments `accept_count` and boosts
  `usefulness_score`

### Always Remember API

| Method | Purpose |
|--------|---------|
| `remember(content, category, tags, …)` | Store a new typed entry |
| `promote_outcome(content, …)` | Promote accepted outcome; increment on duplicate |
| `suppress(entry_id)` | Mark entry as suppressed |
| `unsuppress(entry_id)` | Remove suppression |
| `decay(min_hits, max_reject_ratio)` | Auto-suppress noisy entries |
| `record_hit(entry_id)` | Track retrieval |
| `record_accept(entry_id)` | Track acceptance |
| `forget(entry_id)` | Permanently delete |
| `list_entries(include_suppressed=)` | List entries |
| `search(query)` | Search by content/tag |

### Slash Commands

| Command | Purpose |
|---------|---------|
| `/remember` | List entries with ID, score, hits, accepts |
| `/remember <text>` | Store a new entry |
| `/remember promote <text>` | Promote an outcome |
| `/remember suppress <id>` | Suppress an entry |
| `/remember unsuppress <id>` | Un-suppress an entry |
| `/remember decay` | Auto-suppress noisy entries |
| `/remember why <task>` | Explain retrieval for a task |

---

## OpenClaw Integration Design

OpenClaw is treated as an **optional external** skill/tool ecosystem. ClawSmith
is fully functional without it — every interaction is governed by explicit config
toggles that default to *disabled / restrictive*.

### Architecture

```
ClawSmith ←→ OpenClawSkillBridge ←→ OpenClaw Gateway
```

| Direction | Function | Module |
|-----------|----------|--------|
| **Export** | `export_skill_for_openclaw()` | `skills/openclaw_adapter.py` |
| **Import** | `import_skill_from_openclaw()` | `skills/openclaw_adapter.py` |
| **Sync** | `sync_from_gateway()` | `OpenClawSkillBridge` |
| **Register** | `register_skills_with_gateway()` | `OpenClawSkillBridge` |

### Config Toggles

All toggles live under the `openclaw` section of `settings.yaml`:

| Toggle | Default | Purpose |
|--------|---------|---------|
| `enabled` | `false` | Master switch — the entire bridge is inert when false |
| `allow_skill_import` | `false` | Permit importing skills from the OpenClaw gateway |
| `allow_external_execution` | `false` | Permit executing imported (external) skills locally |
| `require_approval_for_external_writes` | `true` | Require explicit approval before an external skill writes files |

The bridge also requires `gateway_url` to be non-empty; `enabled: true` with an
empty `gateway_url` is treated as unavailable.

Toggles can also be set via environment variables:

```
CLAWSMITH_OPENCLAW__ENABLED=true
CLAWSMITH_OPENCLAW__ALLOW_SKILL_IMPORT=true
CLAWSMITH_OPENCLAW__ALLOW_EXTERNAL_EXECUTION=true
CLAWSMITH_OPENCLAW__REQUIRE_APPROVAL_FOR_EXTERNAL_WRITES=false
```

### Typed Adapter Layer

Imported skills carry extra metadata that distinguishes them from local skills:

| Field on `SkillDefinition` | Purpose |
|---|---|
| `source_type = openclaw_imported` | Marks the skill as external |
| `is_external` (property) | Convenience check — `True` when `source_type` is `openclaw_imported` |
| `origin_url` | URL on the OpenClaw gateway this skill was imported from |
| `requires_approval` | Stamped from `require_approval_for_external_writes` at import time |
| `tags` includes `"external"` | For filtering and display |

### Safety

- OpenClaw is **optional** — absent config results in graceful no-op behaviour
  throughout the bridge, executor, and runtime
- Every bridge method checks `enabled` and the relevant toggle before doing work
- Imported skills are typed as `openclaw_imported` with `is_external = True`
- When `allow_external_execution` is off, imported skills are persisted but
  automatically disabled so the resolver never selects them
- The executor blocks external skills unless `allow_external_execution` is on
- When `require_approval_for_external_writes` is on, the executor refuses to
  execute an external skill that has file targets or commands unless an
  `approval_callback` is provided and returns `True`
- Dry-run mode bypasses the approval check (no actual writes occur)
- Existing sharing toggles (`share_api_keys`, `share_local_models`) are preserved

### Execution Guard Order

When the executor receives an external skill, it applies guards in this order:

1. **External-skill toggle** — is `openclaw.allow_external_execution` true?
2. **External-write approval** — does the skill require approval, and has it been granted?
3. **Scope contract** — are the file targets within scope?
4. **Dry-run short-circuit** — return preview output without side effects
5. **Safe-mode command allowlist** — are the commands in `allowed_commands`?

A local skill (any `source_type` other than `openclaw_imported`) skips steps 1–2.

---

## Scope and Mutation Safety

### Enforcement

All skill execution passes through:

1. **External-skill toggle check** — blocks external skills when `openclaw.allow_external_execution` is off
2. **External-write approval check** — blocks external skills that want to write without approval
3. **Scope check** — `check_skill_scope()` verifies file targets against scope contracts
4. **Command allowlist** — `check_command_allowed()` verifies commands against `execution.allowed_commands`
5. **Safe mode** — when enabled, blocks non-allowlisted commands
6. **Dry run** — executes skill logic but produces no side effects

### Existing Systems Preserved

- `scope_engine/` — scope contracts unchanged
- `mutation_engine/` — staged mutation pipeline unchanged
- No weaker parallel execution paths created

---

## Chat UX

### Slash Commands

| Command | Purpose |
|---------|---------|
| `/help` | Show available commands |
| `/skills` | List loaded skills |
| `/skills regen` | Regenerate skills from repo |
| `/skills why <task>` | Explain skill selection for a task |
| `/memory` | Show persisted architecture memory |
| `/remember <text>` | Store an always-remember entry |
| `/context` | Show current session context |
| `/plan` | Show current execution plan |
| `/scope` | View scope contracts |
| `/agents` | List detected agent CLIs |
| `/openclaw` | OpenClaw integration status |
| `/detect` | Hardware detection |
| `/recommend` | Model recommendations |
| `/doctor` | Environment health check |
| `/yolo <goal>` | Autonomous multi-phase execution |
| `/status` | Session info |
| `/clear` | Clear screen |
| `/quit` | Exit |

### CLI Commands

New skill-related commands:

| Command | Purpose |
|---------|---------|
| `clawsmith skills list` | List all loaded skills |
| `clawsmith skills generate` | Auto-generate skills from repo |
| `clawsmith skills score --task "..."` | Score skills against a task |

All existing commands (`run-task`, `yolo`, `audit`, `chat`, etc.) continue to work.

---

## Migration Notes

### What Changed

1. **New package:** `skills/` — first-class skill subsystem
2. **New modules:** `orchestrator/chat_runtime.py`, `orchestrator/session_state.py`
3. **Extended:** `memory_skill/` with `retriever.py` and `always_remember.py`
4. **Extended:** `tui/commands.py` with new slash commands
5. **Extended:** `orchestrator/cli.py` with `skills` command group
6. **Fixed:** Missing `__init__.py` in 7 packages

### What Was Preserved

- All existing CLI commands work identically
- `orchestrator/pipeline.py` unchanged
- `orchestrator/yolo.py` unchanged
- `scope_engine/` unchanged
- `mutation_engine/` unchanged
- `agents/` unchanged
- `providers/` unchanged
- `routing/` unchanged
- `config/settings.yaml` unchanged
- All existing tests pass

### Upgrade Path

1. `pip install -e .` to get new packages
2. `clawsmith skills generate` to create initial skills
3. Use `clawsmith chat` as the primary interface
4. Existing workflows continue to work unchanged

---

## File Layout

```
skills/
├── __init__.py
├── models.py           # SkillDefinition, SkillScore, SkillSelectionResult
├── registry.py         # SkillRegistry — load, persist, enable/disable
├── resolver.py         # Score and select skills for tasks
├── generator.py        # Auto-generate skills from repo structure
├── executor.py         # Execute skills with scope guardrails
└── openclaw_adapter.py # OpenClaw skill import/export bridge

orchestrator/
├── chat_runtime.py     # ChatRuntime — shared orchestration layer
├── session_state.py    # SessionState — explicit session data
└── (existing files unchanged)

memory_skill/
├── retriever.py        # MemoryRetriever — ranked memory retrieval
├── always_remember.py  # AlwaysRemember — durable cross-session memory
└── (existing files unchanged)

.clawsmith/
├── skills/
│   ├── manual/         # User-defined skills
│   ├── generated/      # Auto-generated skills
│   └── imported/       # OpenClaw-imported skills
└── always_remember/    # Durable memory entries
```
