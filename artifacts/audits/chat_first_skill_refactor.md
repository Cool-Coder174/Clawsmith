# Chat-First Skill Refactor — Code Audit

**Date:** 2026-03-16
**Author:** ClawSmith refactor agent
**Repo:** ClawSmith (local-first AI orchestration)

---

## 1. Current Runtime Entrypoints

| Entrypoint | Location | Mechanism |
|---|---|---|
| `clawsmith` CLI | `pyproject.toml` → `orchestrator.cli:cli` | Click group |
| `python -m orchestrator` | `orchestrator/__main__.py` | calls `cli()` |
| `python -m mcp_server` | `mcp_server/__main__.py` | FastMCP server |

### CLI Commands (all in `orchestrator/cli.py`)

- `run-task` — full orchestration pipeline
- `yolo` — autonomous multi-phase execution
- `resume` — resume interrupted YOLO run
- `audit` — repo audit
- `run-job` — execute JobSpec JSON
- `chat` — interactive TUI session (imports `tui.session.ChatSession`)
- `start-server` / `start` — MCP server
- `onboard` — first-run setup
- `doctor` — environment health check
- `smoke-test` — integration check
- `register-skill` — generate OpenClaw SKILL.md
- `detect-agents` — scan agent CLIs
- `quickstart` — guided setup
- `detect` — hardware detection
- `recommend` — model recommendations
- `install-model` — local LLM install
- `link-repo` — workspace graph
- `scope` — scope contracts
- `rollback` — mutation rollback
- `update` — self-update
- `openclaw` group — webhook, register, ping, status, manifest
- `memory` group — sync, show
- `mutate` group — propose, list, apply, approve

## 2. Existing Modules to Reuse

| Module | Contents | Reuse Strategy |
|---|---|---|
| `orchestrator/` | Pipeline, YOLO, planner, schemas, preflight, CLI | Extend; refactor shared session logic out |
| `tui/` | ChatSession, ChatBrain, commands, renderer, theme | Extend; ChatSession becomes runtime consumer |
| `memory_skill/` | Reader, writer, models, sync | Extend with retriever, ranker, always-remember |
| `repo_graph/` | Scanner, linker, models (WorkspaceGraph) | Reuse for skill scoring and memory retrieval |
| `scope_engine/` | Engine, models (ScopeContract) | Reuse as-is; wire into skill execution |
| `mutation_engine/` | Engine, differ, models | Reuse as-is; enforce in skill execution |
| `agents/` | Registry, router, detector, adapters | Reuse as-is |
| `providers/` | LiteLLM, OpenClaw client/webhook/adapter | Reuse; extend OpenClaw for skill import |
| `routing/` | Classifier, router, cost estimator | Reuse as-is |
| `tools/` | Auditor, mapper, context packer, build detector | Reuse; feed into skill generation |
| `execution/` | Backends, phase executor, prompt builder | Reuse for skill-driven execution |
| `discovery/` | Hardware, toolchain, profile | Reuse as-is |
| `recommendation/` | Catalog, engine, models | Reuse as-is |
| `config/` | Config loader, settings.yaml, agent profiles | Extend with skills config |
| `mcp_server/` | FastMCP server with tools | Reuse; expose skill tools |

## 3. Packaging / Installability Issues

- `pyproject.toml` package discovery includes all runtime modules via `tool.setuptools.packages.find` — this is correct
- Missing `__init__.py` in: `memory_skill/`, `repo_graph/`, `scope_engine/`, `mutation_engine/`, `discovery/`, `recommendation/`
- These missing `__init__.py` files mean these are NOT proper Python packages — imports work as namespace packages but `pip install -e .` may not include them reliably
- New `skills/` package needs to be added to `include` list
- `install/` package also lacks `__init__.py`

**Fix needed:** Add `__init__.py` to all runtime packages that lack them, and add `skills*` to the setuptools include list.

## 4. Gaps Preventing a True Chat-First Runtime

1. **No shared session/runtime layer** — `ChatSession` couples UI, state, and orchestration
2. **No skill system** — no `skills/` directory exists at all
3. **No skill-aware planning** — planner has no concept of skills
4. **No memory ranking/retrieval** — MemoryReader just reads files, no scoring or selection
5. **No cross-repo memory** — memory is single-workspace only
6. **No explicit session state** — state is scattered across ChatSession attributes
7. **No dry-run for chat** — only pipeline has dry_run
8. **No explainability metadata** — decisions are not tracked or surfaceable
9. **Slash commands are limited** — no `/skills`, `/plan`, `/apply`, `/revert`, etc.
10. **CLI commands don't share runtime** — each runs its own isolated logic

## 5. Existing Memory Functionality and Limitations

### What exists:
- `memory_skill/reader.py` — reads architecture, preferences, tooling, repo graph, scope rules from `clawsmith/` dir
- `memory_skill/writer.py` — writes structured data + Markdown summaries
- `memory_skill/models.py` — typed models for architecture, preferences, tooling
- `memory_skill/sync.py` — syncs from discovery profile, repo graph, preferences

### Limitations:
- **No retrieval/ranking** — reads all or nothing, no relevance scoring
- **No cross-repo awareness** — single workspace only
- **No always-remember** — no persistent cross-session annotations
- **No memory types** — no distinction between repo/workspace/cross-repo/user
- **No dependency-stack memory** — doesn't know "FastAPI projects usually need X"
- **No recency weighting** — no timestamps on retrieval
- **No context flooding control** — loads all data or none
- **No `__init__.py`** — not a proper package

## 6. Existing Repo Graph Functionality and Reuse

### What exists:
- `repo_graph/scanner.py` — discovers repos, reads manifests, builds dependency edges
- `repo_graph/linker.py` — manages link/unlink of repos in persisted graph
- `repo_graph/models.py` — RepoNode, DependencyEdge, WorkspaceGraph with build_order

### Reuse opportunities:
- Dependency edges → skill applicability scoring
- Language/framework detection → auto-skill generation
- Related repos → cross-repo memory selection
- Build order → execution order for multi-repo skills
- Manifest reading → stack detection for skill inference

## 7. Existing Scope / Mutation Protections

### Scope engine:
- `ScopeEngine.create_contract()` — workspace graph → scope contract
- `check_file_in_scope()` / `check_repo_in_scope()` — enforcement
- Scope levels: in_scope, conditional (read-only), out_of_scope
- Persisted to `.clawsmith/scopes/`

### Mutation engine:
- Staged pipeline: propose → stage → validate → approve → apply (with rollback)
- Policy-controlled: allowed/restricted types and paths
- Audit logging to `.clawsmith/mutation-audit.json`
- Backup/restore support

**Preservation strategy:** All skill execution must go through scope checks. Command execution must respect allowed_commands. Mutation operations must use the existing staged pipeline. Do NOT create weaker parallel paths.

## 8. Existing OpenClaw Integration Points

| Component | Function |
|---|---|
| `providers/openclaw_client.py` | HTTP client for gateway (ping, register, submit_task, status callbacks) |
| `providers/openclaw_webhook.py` | Starlette webhook receiver (POST /webhook/task, /webhook/ping, etc.) |
| `providers/openclaw_adapter.py` | Task forwarding, skill manifest building, tool definitions |
| `agents/adapters/openclaw_adapter.py` | Agent adapter for OpenClaw as execution target |
| `config/openclaw_skill.yaml` | Skill metadata for OpenClaw discovery |
| `mcp_server/server.py` | MCP tools: openclaw_forward_task, openclaw_skill_manifest |

**Reuse:** Extend OpenClaw adapter to support skill import/export. Add capability checks. Use existing registration/manifest infrastructure.

## 9. Migration Risks

1. **ChatSession refactor** — must preserve existing REPL behavior while adding runtime
2. **Slash command additions** — must not break existing `/help`, `/detect`, etc.
3. **Memory reader changes** — must not break existing sync/show commands
4. **Package structure changes** — adding `__init__.py` could change import behavior
5. **Config changes** — adding skills config must not invalidate existing settings.yaml
6. **Test breakage** — existing tests depend on current module layout

## 10. Recommended Implementation Order

1. **Packaging fixes** — add missing `__init__.py`, verify editable install
2. **Skills schema + registry** — independent, no existing code to break
3. **Session state + chat runtime** — shared layer between ChatSession and CLI
4. **Auto skill generator** — uses tools/repo_auditor and repo_graph
5. **Memory retrieval/ranking** — extends memory_skill
6. **Repo graph integration** — wires graph into memory + skill scoring
7. **Scope enforcement in skills** — wires scope_engine into skill execution
8. **OpenClaw skill interop** — extends providers/openclaw_adapter
9. **Chat UX upgrade** — new slash commands, skill/memory display
10. **CLI backward compatibility** — rewire existing commands through runtime
11. **Tests** — for each new subsystem
12. **Architecture docs** — document the new system
