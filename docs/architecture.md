# ClawSmith Architecture

## Component Reference

| Component | File | Responsibility | Key Classes / Functions |
|---|---|---|---|
| CLI | `orchestrator/cli.py` | Click entry-points for all user commands | `cli`, `run_task`, `audit`, `run_job`, `start_server`, `register_skill` |
| Pipeline | `orchestrator/pipeline.py` | End-to-end orchestration of audit → classify → route → generate → execute | `OrchestrationPipeline.run()` |
| Schemas | `orchestrator/schemas.py` | Pydantic data models shared across all modules | `JobSpec`, `TaskClassification`, `RoutingDecision`, `ExecutionResult`, `ContextPacket`, `PipelineResult`, `AgentProfile` |
| RepoAuditor | `tools/repo_auditor.py` | Walks repository to detect languages, frameworks, CI, linters, markers | `RepoAuditor.audit()`, `AuditReport` |
| RepoMapper | `tools/repo_mapper.py` | Builds directory tree text, detects entrypoints and important files | `RepoMapper.map()`, `RepoMap` |
| BuildDetector | `tools/build_detector.py` | Infers build/test/lint/format commands from config files | `BuildDetector.detect()`, `BuildCommand` |
| ContextPacker | `tools/context_packer.py` | Assembles a token-budgeted context packet from audit + map + files | `ContextPacker.pack()` |
| TaskClassifier | `routing/classifier.py` | Keyword-based scoring of complexity, ambiguity, severity, architectural impact | `TaskClassifier.classify()` |
| ModelRouter | `routing/router.py` | Maps `TaskClassification` to a model tier based on thresholds | `ModelRouter.route_task()` |
| ProviderRegistry | `providers/registry.py` | Resolves a `ModelTier` to a `LiteLLMProvider` instance | `ProviderRegistry.get_provider()` |
| LiteLLMProvider | `providers/litellm_provider.py` | Wraps LiteLLM completion calls | `LiteLLMProvider.complete()` |
| OpenClawAdapter | `providers/openclaw_adapter.py` | Integration seam for OpenClaw: forwards tasks, formats responses, registers skills | `OpenClawAdapter.forward_task()`, `register_as_skill()` |
| BatGenerator | `jobs/bat_generator.py` | Produces `.bat` scripts with timeout logic, log redirection, metadata | `BatGenerator.generate()` |
| JobSpecValidator | `jobs/schema_validator.py` | Validates `JobSpec` against safety rules (allowlist, paths, timeouts) | `JobSpecValidator.validate()`, `ValidationError` |
| Command Allowlist | `jobs/allowlist.py` | Maintains and checks the command allowlist; rejects shell metacharacters | `validate_command()`, `get_effective_allowlist()` |
| JobExecutor | `jobs/executor.py` | Runs generated `.bat` scripts, captures output, returns `ExecutionResult` | `JobExecutor.execute()` |
| Config Loader | `config/config_loader.py` | Loads `settings.yaml`, applies env overrides, provides singleton access | `load_config()`, `get_config()`, `reset_config()` |
| MCP Server | `mcp_server/server.py` | Exposes all tools over SSE transport for editor integration | `mcp` (FastMCP app) |

---

## Data Flow: `clawsmith run-task`

1. **CLI** parses `--task` and `--repo-path` arguments, calls `OrchestrationPipeline.run()`.
2. **RepoAuditor** walks the repo to produce an `AuditReport` (languages, frameworks, CI, linters, marker files).
3. **RepoMapper** builds a `RepoMap` (tree text, entrypoints, important files).
4. **ContextPacker** combines the audit, map, and task description into a `ContextPacket` (architecture summary, relevant file contents within token budget, build commands, constraints).
5. **TaskClassifier** scores the task on complexity, ambiguity, architectural impact, and failure severity → `TaskClassification`.
6. **ModelRouter** maps the classification to a `RoutingDecision` (selected tier, model name, provider, confidence, reasoning).
7. **PromptGenerator** assembles a structured prompt from the context packet and routing info.
8. **ProviderRegistry** resolves the tier to a `LiteLLMProvider` and calls `complete()` (skipped in dry-run mode).
9. **BatGenerator** creates a `.bat` script from the `JobSpec`.
10. **JobSpecValidator** validates the spec (commands, paths, timeouts).
11. **JobExecutor** runs the `.bat`, captures stdout/stderr to `artifacts/<job_id>/`, returns `ExecutionResult`.
12. **Pipeline** assembles a `PipelineResult` and returns it to the CLI for display.

---

## Schema Reference

### `JobSpec`

| Field | Type | Default | Description |
|---|---|---|---|
| `id` | `str` | auto (uuid hex[:12]) | Unique job identifier |
| `task_type` | `TaskType` | — | One of: audit, bugfix, implementation, refactor, planning, summarization, debugging, testing, prompt_polish |
| `objective` | `str` | — | Human-readable objective |
| `working_directory` | `str` | — | Path to the working directory |
| `files_in_scope` | `list[str]` | `[]` | Files the task should focus on |
| `build_commands` | `list[str]` | `[]` | Commands to run during build phase |
| `test_commands` | `list[str]` | `[]` | Commands to run during test phase |
| `prompt` | `str` | — | The prompt to send to the model |
| `provider_preference` | `ModelTier` | `local_code` | Preferred model tier |
| `timeout_seconds` | `int` | `300` | Execution timeout (10–3600) |
| `dry_run` | `bool` | `False` | Skip actual execution |
| `retries` | `int` | `1` | Number of retries (0–5) |

### `TaskClassification`

| Field | Type | Description |
|---|---|---|
| `task_type` | `TaskType` | Detected task type |
| `complexity_score` | `float` | 0.0–1.0, weighted composite score |
| `files_likely_touched` | `int` | Estimated number of files |
| `ambiguity_score` | `float` | 0.0–1.0, presence of ambiguity markers |
| `architectural_impact` | `float` | 0.0–1.0, presence of high-impact keywords |
| `failure_severity` | `float` | 0.0–1.0, presence of severity markers |
| `estimated_tokens` | `int` | Token estimate from context or word count |

### `RoutingDecision`

| Field | Type | Description |
|---|---|---|
| `selected_tier` | `ModelTier` | Chosen tier: local_router, local_code, premium |
| `model_name` | `str` | LiteLLM model string |
| `provider` | `str` | Provider name (ollama, openai, etc.) |
| `reasoning` | `str` | Human-readable explanation of routing logic |
| `confidence_score` | `float` | 1.0 - ambiguity_score |
| `estimated_tokens` | `int` | Passthrough from classification |
| `estimated_cost_usd` | `float` | Estimated API cost |

### `ContextPacket`

| Field | Type | Description |
|---|---|---|
| `task_summary` | `str` | The original task description |
| `relevant_files` | `dict[str, str]` | Relative path → file contents |
| `architecture_summary` | `str` | Multi-line summary of repo architecture |
| `build_test_commands` | `list[str]` | Detected build/test commands |
| `recent_errors` | `list[str]` | Recent error messages (if supplied) |
| `constraints` | `list[str]` | Budget, project-size warnings, missing configs |
| `recommended_steps` | `list[str]` | Suggested commands to run |
| `token_estimate` | `int` | Estimated total tokens in the packet |

### `ExecutionResult`

| Field | Type | Description |
|---|---|---|
| `job_id` | `str` | The job identifier |
| `exit_code` | `int` | Process exit code |
| `stdout` | `str` | Captured standard output |
| `stderr` | `str` | Captured standard error |
| `artifacts` | `list[str]` | Paths to generated artifact files |
| `duration_seconds` | `float` | Wall-clock execution time |
| `success` | `bool` | Whether the job succeeded |
| `error_message` | `str \| None` | Error description on failure |

### `PipelineResult`

| Field | Type | Description |
|---|---|---|
| `task_description` | `str` | Original task |
| `repo_path` | `str` | Repository path |
| `audit_report` | `dict` | Serialised AuditReport |
| `repo_map` | `dict` | Serialised RepoMap |
| `context_packet` | `ContextPacket \| None` | Assembled context |
| `classification` | `TaskClassification \| None` | Task classification |
| `routing_decision` | `RoutingDecision \| None` | Model routing result |
| `generated_prompt` | `str` | Final prompt sent to provider |
| `completion` | `dict \| None` | Provider response |
| `execution_result` | `ExecutionResult \| None` | Job execution result |
| `dry_run` | `bool` | Whether this was a dry run |
| `success` | `bool` | Overall success |
| `error_message` | `str \| None` | Pipeline error message |
| `duration_seconds` | `float` | Total pipeline duration |

---

## `.bat` Job Lifecycle

```
JobSpec (Pydantic model)
    │
    ▼
JobSpecValidator.validate()
    ├── check task_type
    ├── check timeout bounds (10–3600s)
    ├── check working_directory (no "..", path traversal, existence)
    └── check commands (allowlist + shell metacharacter rejection)
    │
    ▼
BatGenerator.generate()
    ├── Create jobs/generated/<job_id>.bat
    ├── Embed: @echo off, setlocal, cd /d, build commands, test commands
    ├── Timeout checking via :CHECK_TIMEOUT subroutine
    ├── Log redirection to artifacts/<job_id>/stdout.log, stderr.log
    └── Write artifacts/<job_id>/metadata.json
    │
    ▼
JobExecutor.execute()
    ├── Run the .bat via subprocess
    ├── Capture stdout/stderr
    ├── Enforce timeout
    └── Return ExecutionResult
```

---

## Model Routing Flow

The `ModelRouter` maps a `TaskClassification` to a model tier using three thresholds from `config/settings.yaml`:

```
                          ┌─────────────────────────────┐
                          │   TaskClassifier.classify()  │
                          │   → complexity_score (0–1)   │
                          └──────────────┬──────────────┘
                                         │
              ┌──────────────────────────┼──────────────────────────┐
              ▼                          ▼                          ▼
     complexity < 0.35        0.35 ≤ complexity < 0.70    complexity ≥ 0.70
              │                          │                          │
              ▼                          ▼                          ▼
        local_router               local_code                   premium
      (ollama/mistral)          (ollama/codellama)          (openai/gpt-4o)
```

### Bump and override rules

- **Ambiguity bump:** If `ambiguity_score > 0.60` (`ambiguity_bump_threshold`), the selected tier is bumped up one level (`local_router` → `local_code`, `local_code` → `premium`). A task that is already at `premium` stays at `premium`.
- **Severity override:** If `failure_severity > 0.8`, the tier is overridden to `premium` regardless of complexity. This ensures critical/production issues always get the strongest model.
- **Confidence score:** Computed as `1.0 - ambiguity_score`. Passed through in the `RoutingDecision` for downstream consumers.

### Scoring Inputs

`TaskClassifier` in `routing/classifier.py` computes the following scores:

| Score | How it's computed | Weight in `complexity_score` |
|---|---|---|
| `files_likely_touched` | Count of file-path-like mentions in the task description + files in the context packet (capped at 50). Normalized to `min(count / 10, 1.0)`. | 30% |
| `ambiguity_score` | Count of ambiguity keywords ("maybe", "possibly", "not sure", "unclear", "might", "could", "somehow", "figure out", "investigate") divided by 10, capped at 1.0. | 25% |
| `architectural_impact` | Count of high-impact keywords ("refactor", "migrate", "redesign", "overhaul", "rewrite", "restructure", "replace", "extract", "split", "merge") divided by 5, capped at 1.0. | 25% |
| `failure_severity` | Count of severity keywords ("broken", "crash", "critical", "production", "outage", "data loss", "security", "urgent", "blocker") divided by 5, capped at 1.0. | 20% |

The final `complexity_score` is: `0.30 × files + 0.25 × ambiguity + 0.25 × architectural + 0.20 × severity`, clamped to [0.0, 1.0].

`estimated_tokens` comes from the context packet's `token_estimate` if available, otherwise it is estimated as `word_count / 0.75`.

---

## Provider Abstraction

`ProviderRegistry` maps `ModelTier` names to `LiteLLMProvider` instances using the model configuration from `settings.yaml`.

### How it works

1. `get_config().models` provides a `ModelsConfig` with four entries: `local_router`, `local_code`, `premium`, `prompt_polisher`.
2. `ProviderRegistry.get_provider(tier)` reads `getattr(models_config, tier.value)` to get the `ModelConfig`.
3. A `LiteLLMProvider` is instantiated with the model name, max tokens, and temperature.
4. `LiteLLMProvider.complete(prompt)` calls `litellm.acompletion(model=model_name, ...)`.

### Adding a new provider

1. Add a new model entry in `settings.yaml` under the appropriate tier.
2. Use a LiteLLM-compatible model string (e.g., `anthropic/claude-3-opus`, `openrouter/meta-llama/llama-3`).
3. Set the required API key in `.env` (LiteLLM resolves keys from environment automatically).
4. No code changes required — the registry resolves dynamically from config.
