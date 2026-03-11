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
| `$AGENT_ID` | Selected agent adapter's `agent_id` | Machine-readable agent identifier |
| `$AGENT_DISPLAY_NAME` | Selected agent adapter's `display_name` | Human-readable agent name |
| `$AGENT_INVOCATION` | Built by the adapter's `build_invocation()` | Full command line for the agent CLI |
| `$CURSOR_CLI_PATH` | `CURSOR_CLI_PATH` env var or `"cursor"` | Legacy: path to the Cursor executable |
| `$CURSOR_PROMPT` | `profile.variables["CURSOR_PROMPT"]` | Legacy: prompt passed to the Cursor CLI |

Build and test command blocks are formatted as repeating triplets:
```
echo [BUILD] Running: <cmd> >> "%STDOUT_LOG%"
<cmd> >> "%STDOUT_LOG%" 2>> "%STDERR_LOG%"
if errorlevel 1 set EXIT_CODE=1
```

---

## Bundled Templates

### `agent_task.bat.template` (recommended)

The agent-agnostic template. Build phase, then invokes `$AGENT_INVOCATION` (the selected agent CLI), then test phase. This is the default template for all new profiles and supports any agent adapter.

### `build_and_test.bat.template`

Build phase followed by test phase, no agent invocation. Used for tasks that only need build and test commands.

### `cursor_task.bat.template` (legacy)

Build phase, then invokes the Cursor CLI with `$CURSOR_PROMPT`, then test phase. Preserved for backwards compatibility. New profiles should use `agent_task.bat.template`.

### `agent_audit.bat.template` (legacy)

Audit commands only — runs test commands as audit checks. No build step and no agent invocation. Preserved for backwards compatibility.

### `agent_bugfix.bat.template` (legacy)

Pre-flight tests, then invokes Cursor to apply the fix, then verification tests. Preserved for backwards compatibility.

### `agent_implement.bat.template` (legacy)

Build setup, then invokes Cursor, then post-implementation tests. Preserved for backwards compatibility.

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

`TemplateRenderer._validate_variable_values()` rejects any variable value containing shell metacharacters (`&`, `|`, `<`, `>`, `;`) to prevent injection. Three keys are exempt:

- `BUILD_COMMANDS` — generated internally by `_format_commands()` and intentionally uses redirection syntax (`>>`, `2>>`).
- `TEST_COMMANDS` — same as above.
- `AGENT_INVOCATION` — generated internally by the agent adapter's `build_invocation()` method.

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
