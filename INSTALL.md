# Installation Guide

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.11+** | Verify with `python --version`. On Windows you can also use `py -3.11`. |
| **git** | Required for cloning. Verify with `git --version`. |
| **Cursor CLI** *(optional)* | Set `CURSOR_CLI_PATH` in `.env` or add `cursor` to `PATH`. Required only for jobs that invoke Cursor. |
| **Ollama** *(optional)* | Required for local model tiers (`local_router`, `local_code`). Pull models with `ollama pull mistral` and `ollama pull codellama`. |

---

## Quick Install (Windows)

```
git clone https://github.com/<your-org>/Clawsmith.git
cd Clawsmith
scripts\windows\install.bat
```

**What `install.bat` does:**

1. Checks that Python 3.11+ is available.
2. Creates a virtual environment in `venv\`.
3. Upgrades pip and runs `pip install -e .[dev]` (installs ClawSmith and all dev dependencies).
4. Copies `.env.example` to `.env` if `.env` does not already exist.
5. Creates runtime directories: `logs\`, `artifacts\`, `jobs\generated\`.

---

## Manual Install

If you prefer explicit control over each step:

```
python -m venv venv
venv\Scripts\activate
pip install --upgrade pip
pip install -e .[dev]
```

Then manually create the runtime directories:

```
mkdir logs
mkdir artifacts
mkdir jobs\generated
```

And copy the environment file:

```
copy .env.example .env
```

---

## Environment Setup

All environment variables are defined in `.env.example`. Copy it to `.env` and fill in the relevant keys.

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key for premium / prompt polisher tiers | *(empty)* |
| `ANTHROPIC_API_KEY` | Anthropic API key (alternative provider) | *(empty)* |
| `OPENROUTER_API_KEY` | OpenRouter API key (alternative provider) | *(empty)* |
| `CURSOR_CLI_PATH` | Absolute path to the Cursor CLI executable | auto-detect from `PATH` |
| `CLAWSMITH_CONFIG_PATH` | Path to a custom `settings.yaml` | `config/settings.yaml` |
| `LOG_LEVEL` | Logging verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`) | `INFO` |

At least one API key is required for cloud model tiers. For local-only use with Ollama, API keys are not required (but the doctor will warn).

### Config overrides via environment

Any `config/settings.yaml` value can be overridden with an environment variable using `CLAWSMITH_<SECTION>__<KEY>` (double-underscore nesting):

```
CLAWSMITH_ROUTING__LOW_COMPLEXITY_THRESHOLD=0.5
CLAWSMITH_MODELS__PREMIUM__MAX_TOKENS=16384
CLAWSMITH_EXECUTION__DEFAULT_TIMEOUT=600
CLAWSMITH_MCP_SERVER__PORT=9000
CLAWSMITH_EXECUTION__ALLOWED_COMMANDS=["cursor","python","git"]
```

---

## Config Walkthrough

`config/settings.yaml` is the central configuration file. It has five sections:

### `models`

Defines four model tiers. Each tier has `provider`, `model_name`, `max_tokens`, `temperature`, and optional cost fields (`input_cost_per_token`, `output_cost_per_token`).

| Tier | Default Provider | Default Model | Purpose |
|---|---|---|---|
| `local_router` | `ollama` | `ollama/mistral` | Fast, cheap routing for simple tasks |
| `local_code` | `ollama` | `ollama/codellama` | Code-focused local model for mid-complexity tasks |
| `premium` | `openai` | `openai/gpt-4o` | Highest capability for complex tasks |
| `prompt_polisher` | `openai` | `openai/gpt-4o-mini` | Lightweight model for prompt refinement |

### `routing`

Three thresholds that control how `TaskClassifier` scores map to model tiers:

| Key | Default | Purpose |
|---|---|---|
| `low_complexity_threshold` | `0.35` | Below this → `local_router` |
| `high_complexity_threshold` | `0.70` | At or above this → `premium` |
| `ambiguity_bump_threshold` | `0.60` | If ambiguity exceeds this, tier is bumped up one level |

### `execution`

| Key | Default | Purpose |
|---|---|---|
| `default_timeout` | `300` | Seconds before a job is killed |
| `max_retries` | `2` | Number of retry attempts |
| `artifacts_dir` | `artifacts` | Where job output is written |
| `logs_dir` | `logs` | Application log directory |
| `allowed_commands` | *(14 commands)* | Allowlist for job build/test commands |

### `mcp_server`

| Key | Default | Purpose |
|---|---|---|
| `host` | `127.0.0.1` | Bind address for the MCP server |
| `port` | `8765` | Listen port |
| `transport` | `sse` | Transport protocol (SSE for Cursor) |

### `openclaw`

| Key | Default | Purpose |
|---|---|---|
| `skill_name` | `ClawSmith` | Skill name when registering with OpenClaw |
| `mcp_endpoint` | `http://127.0.0.1:8765/sse` | Endpoint exposed to OpenClaw |
| `webhook_secret` | *(empty)* | HMAC secret for webhook auth (set via `OPENCLAW_WEBHOOK_SECRET` env) |

---

## Agent Profile Setup

ClawSmith ships with five bundled agent profiles in `config/agent_profiles/`:

| Profile | Task Type | Model Tier |
|---|---|---|
| `code_audit` | audit | `local_code` |
| `bugfix_worker` | bugfix | `local_code` |
| `implementation_worker` | implementation | `local_code` |
| `prompt_polisher` | prompt_polish | `prompt_polisher` |
| `heavy_remote_escalation` | refactor | `premium` |

To customise a profile, copy any YAML file from `config/agent_profiles/`, change the `name` field (must be unique), adjust `task_type`, `prompt_template`, `variables`, and `provider_preference` as needed. See [docs/agent_profiles.md](docs/agent_profiles.md) for the full schema reference.

---

## First-Run Verification

After installation, run the doctor to verify everything is configured correctly:

```
scripts\windows\doctor.bat
```

Or, if the venv is already activated:

```
clawsmith doctor
```

The doctor runs 16 checks across:

- Python version (3.11+ required)
- `pip` and `git` on PATH
- Cursor CLI availability
- `.env` file existence and API key presence
- `config/settings.yaml` existence and parse validity
- All four model tiers defined with non-empty `model_name`
- OpenClaw config section present
- Runtime directories (`logs/`, `artifacts/`, `jobs/generated/`, `jobs/templates/`)
- All five `.bat.template` files present
- All agent profiles valid and referencing existing templates

**Pass** = ready to go. **Warnings** are non-blocking (missing Cursor CLI, no API keys, missing optional dirs). **Failures** are blocking and must be fixed.

---

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md) for solutions to common installation and runtime issues.
