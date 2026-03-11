# `.bat` Templates

## Template Format

`.bat.template` files in `jobs/templates/` are standard Windows batch scripts with `$VARIABLE` placeholders using Python `string.Template` syntax. They are rendered by `TemplateRenderer.render()` or `TemplateRenderer.render_for_profile()` into executable `.bat` files written to `jobs/generated/`.

---

## Available Variables

When rendering a profile via `TemplateRenderer.render_for_profile()`, the following variables are injected:

| Variable | Source | Description |
|---|---|---|
| `$JOB_ID` | Generated UUID hex (first 12 chars) | Unique job identifier |
| `$OBJECTIVE` | `profile.variables["OBJECTIVE"]` (falls back to `profile.description`) | Human-readable task objective |
| `$WORKING_DIR` | `profile.working_directory` | `cd /d` target directory |
| `$TIMEOUT_SECONDS` | `profile.timeout_seconds` | Timeout in seconds |
| `$ARTIFACT_DIR` | `artifacts/<job_id>/` | Log and artifact output directory |
| `$BUILD_COMMANDS` | Rendered from `profile.build_commands` | Multi-line build phase block (echo + run + error check) |
| `$TEST_COMMANDS` | Rendered from `profile.test_commands` | Multi-line test phase block (echo + run + error check) |
| `$CURSOR_CLI_PATH` | `CURSOR_CLI_PATH` env var or `"cursor"` | Path to the Cursor executable |
| `$CURSOR_PROMPT` | `profile.variables["CURSOR_PROMPT"]` | Prompt passed to the Cursor CLI |

Build and test command blocks are formatted as repeating triplets:
```
echo [BUILD] Running: <cmd> >> "%STDOUT_LOG%"
<cmd> >> "%STDOUT_LOG%" 2>> "%STDERR_LOG%"
if errorlevel 1 set EXIT_CODE=1
```

---

## Bundled Templates

### `build_and_test.bat.template`

Build phase followed by test phase, no Cursor invocation. Used for tasks that only need build and test commands.

### `cursor_task.bat.template`

Build phase, then invokes the Cursor CLI with `$CURSOR_PROMPT`, then test phase. The general-purpose template for Cursor-driven tasks.

### `agent_audit.bat.template`

Audit commands only — runs test commands as audit checks (e.g. `ruff check`, `mypy`). No build step and no Cursor invocation. Used by the `code_audit` profile.

### `agent_bugfix.bat.template`

Pre-flight tests (build commands) to reproduce the bug, then invokes Cursor to apply the fix, then verification tests (test commands). Used by the `bugfix_worker` profile.

### `agent_implement.bat.template`

Build setup commands, then invokes Cursor to implement the feature, then post-implementation tests. Used by the `implementation_worker` and `heavy_remote_escalation` profiles.

---

## How to Create a Custom Template

1. Copy an existing template from `jobs/templates/`.
2. Keep the standard header variables that all templates use:
   - `JOB_ID`, `ARTIFACT_DIR` — for artifact path setup
   - `STDOUT_LOG`, `STDERR_LOG` — for log file paths
   - `TIMEOUT_SECONDS`, `EXIT_CODE`, `_START_S` — for timeout tracking
3. Keep the `:CHECK_TIMEOUT` subroutine and `:FINISH` label — these handle timeout enforcement and final exit code reporting.
4. Add your custom commands between the build and test phases.
5. Reference the new template by filename in a profile's `prompt_template` field.
6. Run `clawsmith doctor` to verify the template is detected.

---

## Security Model

### Variable value validation

`TemplateRenderer._validate_variable_values()` rejects any variable value containing shell metacharacters (`&`, `|`, `<`, `>`, `;`) to prevent injection. Two keys are exempt:

- `BUILD_COMMANDS` — generated internally by `_format_commands()` and intentionally uses redirection syntax (`>>`, `2>>`).
- `TEST_COMMANDS` — same as above.

### Command allowlist

Before a template is rendered, `JobSpecValidator` validates all commands in `build_commands` and `test_commands` against the allowlist maintained in `jobs/allowlist.py`. Commands containing shell metacharacters (`&`, `|`, `<`, `>`, `^`, `;`) are rejected outright, regardless of the allowlist.

### Allowlisted commands

The default allowlist (from `jobs/allowlist.py`):

| Command |
|---|
| `cursor` |
| `python` |
| `pip` |
| `npm` |
| `npx` |
| `node` |
| `cargo` |
| `dotnet` |
| `git` |
| `pytest` |
| `ruff` |
| `mypy` |
| `eslint` |
| `tsc` |

The allowlist is configurable via `execution.allowed_commands` in `config/settings.yaml`. Config entries are merged with the defaults (they extend, not replace).

Additionally, the multi-token pattern `cmd /c` is allowed as a special case.
