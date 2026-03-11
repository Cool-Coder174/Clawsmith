# Troubleshooting

Common issues and fixes for ClawSmith installation and runtime.

---

## Python not found

**Symptom:** `python --version` fails or returns a version below 3.11.

**Fix:** Install Python 3.11+ from [python.org](https://www.python.org/downloads/). Ensure it is on `PATH`. On Windows you can also use the `py` launcher: `py -3.11 --version`.

---

## venv not found / activate fails

**Symptom:** `install.bat` fails to find or activate the virtual environment, or running `clawsmith` says "No module named orchestrator".

**Fix:** Run `scripts\windows\install.bat` from the repository root. If the venv is corrupted, delete the `venv\` directory and re-run the script. Make sure you are running from the same drive as the repository (Windows `cd /d` is required to switch drives).

---

## `pip install -e .[dev]` fails

**Symptom:** The install script fails during dependency installation.

**Fix:**
- Check internet connectivity. On corporate networks, configure a pip proxy (`pip config set global.proxy http://...`).
- If `setuptools` is missing: `pip install setuptools`.
- If you see build errors, ensure you have Python development headers installed.

---

## Missing API keys

**Symptom:** `clawsmith doctor` warns "No API key set (OPENAI_API_KEY / ANTHROPIC_API_KEY / OPENROUTER_API_KEY)".

**Fix:** Edit `.env` and set at least one of `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, or `OPENROUTER_API_KEY`. For local-only use with Ollama, API keys are not required but the doctor will warn. This warning is non-blocking — ClawSmith will still run local-tier tasks.

---

## Cursor CLI not detected

**Symptom:** `clawsmith doctor` warns "Set CURSOR_CLI_PATH or add cursor to PATH".

**Fix:** Set `CURSOR_CLI_PATH=C:\path\to\cursor.exe` in `.env`, or add the Cursor install directory to your system `PATH`. If Cursor CLI is not installed, jobs that invoke Cursor will return a `CursorNotAvailable` result with instructions. This warning is non-blocking.

---

## Config parse errors

**Symptom:** `clawsmith doctor` fails on "Config parses" with a YAML or validation error.

**Fix:** Validate `config/settings.yaml` with a YAML linter. Common causes:
- Tabs instead of spaces (YAML requires spaces).
- Missing required fields (e.g. `model_name` is empty for a tier).
- Incorrect indentation (nested keys must be indented consistently).

---

## `.bat` execution fails with "Access is denied"

**Symptom:** A generated `.bat` file cannot be executed.

**Fix:**
- Right-click the `.bat` file → Properties → Unblock (Windows may block downloaded scripts).
- Run from an elevated command prompt if needed.
- Check that your antivirus is not quarantining the file.

---

## `.bat` execution fails with "command not found"

**Symptom:** A build or test command inside a `.bat` job fails because the command is not recognised.

**Fix:**
- Ensure the command is installed and on `PATH`.
- If the command is not in the default allowlist, add it to `execution.allowed_commands` in `config/settings.yaml`.
- Run `clawsmith doctor` to verify the configuration loads.

---

## MCP server fails to start

**Symptom:** `scripts\windows\run_mcp.bat` or `clawsmith start-server` fails with an "address already in use" error.

**Fix:** Another process is using port 8765. Either:
- Stop the other process, or
- Change the port in `config/settings.yaml` under `mcp_server.port`, or
- Set `CLAWSMITH_MCP_SERVER__PORT=9000` in `.env`.

---

## Ollama models not responding

**Symptom:** Local model tiers (`local_router`, `local_code`) fail with connection errors.

**Fix:**
- Ensure Ollama is running: `ollama serve`.
- Ensure the required models are pulled: `ollama pull mistral`, `ollama pull codellama`.
- Verify Ollama is accessible at `http://localhost:11434`.

---

## `clawsmith` command not found

**Symptom:** Running `clawsmith` in the terminal returns "command not found" or "not recognized".

**Fix:** The project is not installed in editable mode, or the venv is not activated.
1. Activate the venv: `venv\Scripts\activate`.
2. Install: `pip install -e .`.

If you are using a different venv directory name (`.venv`), adjust accordingly.

---

## Doctor shows warnings but no failures

**Symptom:** `clawsmith doctor` reports warnings but exits successfully.

ClawSmith will still run. Warnings are **non-blocking** — they flag missing optional components:
- Missing Cursor CLI (jobs that invoke Cursor will skip gracefully).
- No API keys set (only local-tier tasks will work).
- Missing optional directories like `logs/` or `artifacts/` (they will be created on first use by the relevant components).

**Failures** are blocking and must be fixed before ClawSmith can operate (e.g. Python version too low, `config/settings.yaml` missing or unparseable, `jobs/templates/` directory missing).
