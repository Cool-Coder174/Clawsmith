# Agent Profiles

## What Profiles Are

Agent profiles are YAML files in `config/agent_profiles/` that define how an agent worker is deployed without touching core source code. Each profile specifies a task type, model tier, template, variables, timeout, and retry policy. Profiles are loaded by `ProfileLoader` and converted to `JobSpec` instances for execution.

---

## YAML Schema Reference

Every field maps to the `AgentProfile` Pydantic model in `orchestrator/schemas.py`:

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `str` | `"default"` | Unique profile identifier |
| `description` | `str` | `""` | Human-readable description |
| `task_type` | `TaskType` | `implementation` | One of: `audit`, `bugfix`, `implementation`, `refactor`, `planning`, `summarization`, `debugging`, `testing`, `prompt_polish` |
| `working_directory` | `str` | `"."` | Working directory for the job |
| `build_commands` | `list[str]` | `[]` | Commands run in the build phase |
| `test_commands` | `list[str]` | `[]` | Commands run in the test/verify phase |
| `prompt_template` | `str` | `cursor_task.bat.template` | `.bat.template` filename from `jobs/templates/` |
| `variables` | `dict[str, str]` | `{}` | Template variables; `OBJECTIVE` and `CURSOR_PROMPT` are special |
| `provider_preference` | `ModelTier` | `local_code` | One of: `local_router`, `local_code`, `premium`, `prompt_polisher` |
| `timeout_seconds` | `int` | `300` | Execution timeout (10–3600) |
| `dry_run` | `bool` | `false` | Skip actual execution |
| `retries` | `int` | `1` | Retry count (0–5) |
| `tags` | `list[str]` | `[]` | Metadata tags |

---

## Variable Substitution

Templates use Python's `string.Template.safe_substitute` syntax. In `.bat.template` files, `$VARIABLE` or `${VARIABLE}` references are replaced with values from the profile's `variables` dict (plus injected system variables like `JOB_ID`).

Two variable keys have special meaning:

- **`OBJECTIVE`** — used as the `objective` field of the generated `JobSpec`. Falls back to the profile's `description` if not set.
- **`CURSOR_PROMPT`** — the prompt text passed to the Cursor CLI. `ProfileLoader.to_job_spec()` applies `safe_substitute` to this value, so it can reference other variables from the same dict.

Unknown `$VARIABLE` references are left as-is (`safe_substitute` does not raise on missing keys).

---

## Bundled Profiles

### `code_audit`

Runs a local code model for static analysis, linting, and type-checking. Uses `agent_audit.bat.template` with `ruff check .` and `mypy orchestrator/ --ignore-missing-imports` as test commands. No build step. Tier: `local_code`, timeout: 300s.

### `bugfix_worker`

Dispatches a local code model to diagnose and fix a reported bug. Runs pre-flight tests (`pytest tests/ -x -q`) to reproduce the bug, invokes Cursor to apply the fix, then runs verification tests (`pytest tests/ -v`). Uses `agent_bugfix.bat.template`. Tier: `local_code`, timeout: 600s, retries: 2.

### `implementation_worker`

Dispatches a local code model to implement a requested feature. No build step — goes straight to Cursor, then runs post-implementation tests (`pytest tests/ -v`). Uses `agent_implement.bat.template`. Tier: `local_code`, timeout: 900s.

### `prompt_polisher`

Uses the `prompt_polisher` model tier to refine draft prompts for clarity, completeness, and optimal LLM consumption. No build or test commands — purely a text transformation step. Uses `cursor_task.bat.template`. Tier: `prompt_polisher`, timeout: 120s.

### `heavy_remote_escalation`

Escalates to the premium remote model for complex architectural refactors and high-impact changes that exceed the capability of local models. Uses `agent_implement.bat.template` with post-implementation tests. Tier: `premium`, timeout: 1800s, retries: 2.

---

## How to Create a Custom Profile

1. Copy an existing YAML from `config/agent_profiles/`.
2. Change the `name` field — it must be unique across all profiles.
3. Set `task_type` to the appropriate category.
4. Choose a `prompt_template` from `jobs/templates/`, or create a new one (see [bat_templates.md](bat_templates.md)).
5. Populate `variables.CURSOR_PROMPT` with the instruction for Cursor.
6. Populate `variables.OBJECTIVE` with a human-readable objective.
7. Set `provider_preference` to the desired model tier.
8. Run `clawsmith doctor` to verify the profile loads and its template exists.

---

## How Profiles Map to `.bat` Execution

```
Profile YAML
    │
    ▼
ProfileLoader.load_by_name()
    │
    ▼
AgentProfile (Pydantic model)
    │
    ├──► ProfileLoader.to_job_spec() ──► JobSpec
    │
    └──► TemplateRenderer.render_for_profile() ──► jobs/generated/<job_id>.bat
                                                        │
                                                        ▼
                                                   JobExecutor.execute()
                                                        │
                                                        ▼
                                                   ExecutionResult
```

**Step by step:**

1. `ProfileLoader.load_by_name(name)` reads the YAML file and validates it into an `AgentProfile`.
2. `ProfileLoader.to_job_spec(profile)` converts the profile into a `JobSpec`, merging variables, rendering the prompt via `safe_substitute`, and generating a unique job ID.
3. `TemplateRenderer.render_for_profile(profile, job_id)` injects all variables into the `.bat.template` and writes the rendered script to `jobs/generated/<job_id>.bat`.
4. `JobExecutor.execute(job_spec)` runs the generated `.bat` file, captures stdout/stderr to `artifacts/<job_id>/`, and returns an `ExecutionResult`.

---

## Example Workflow

Running the `bugfix_worker` profile via the pipeline:

```bash
clawsmith run-task --task "Fix the login bug in auth.py" --repo-path .
```

The pipeline:

1. Audits the repository.
2. Classifies the task — detects "fix" and "bug" keywords → `bugfix` type.
3. Routes to `local_code` tier based on complexity score.
4. Generates a structured prompt from the context packet.
5. The executor uses the `bugfix_worker` profile's template (`agent_bugfix.bat.template`), running pre-flight tests, invoking Cursor, then running verification tests.
