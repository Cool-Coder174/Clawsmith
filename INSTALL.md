# Installation Guide

## Quick Install

### macOS / Linux

```bash
curl -fsSL https://raw.githubusercontent.com/Cool-Coder174/ClawSmith/main/install.sh | bash
```

### Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/Cool-Coder174/ClawSmith/main/install.ps1 | iex
```

### From source

```bash
git clone https://github.com/Cool-Coder174/ClawSmith.git
cd ClawSmith
pip install -e .
```

---

## Prerequisites

| Requirement | Notes |
|---|---|
| **Python 3.11+** | Verify with `python --version`. |
| **git** | Verify with `git --version`. |
| **Ollama** *(optional)* | For local model tiers. Install from [ollama.com](https://ollama.com). |
| **At least one agent CLI** *(optional)* | Cursor (`cursor`), Claude Code (`claude`), Gemini (`gemini`), or OpenClaw (`openclaw`). |

---

## First Run

After installation, run the guided onboarding:

```bash
clawsmith onboard       # checks prereqs, creates .env, sets up directories
clawsmith doctor        # verifies full environment (HEALTHY / DEGRADED / BLOCKED)
clawsmith smoke-test    # quick integration check across all subsystems
```

---

## Environment Setup

All environment variables are defined in `.env.example`. The onboarding command copies it to `.env` automatically.

| Variable | Purpose | Default |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key for premium tier | *(empty)* |
| `ANTHROPIC_API_KEY` | Anthropic API key | *(empty)* |
| `OPENROUTER_API_KEY` | OpenRouter API key | *(empty)* |
| `CURSOR_CLI_PATH` | Path to Cursor CLI executable | auto-detect |
| `CLAWSMITH_CONFIG_PATH` | Path to custom `settings.yaml` | `config/settings.yaml` |
| `LOG_LEVEL` | Logging verbosity | `INFO` |

At least one API key is needed for cloud model tiers. For local-only use with Ollama, API keys are not required.

### Config overrides via environment

Any `config/settings.yaml` value can be overridden with `CLAWSMITH_<SECTION>__<KEY>`:

```bash
CLAWSMITH_ROUTING__LOW_COMPLEXITY_THRESHOLD=0.5
CLAWSMITH_MODELS__PREMIUM__MAX_TOKENS=16384
CLAWSMITH_MCP_SERVER__PORT=9000
```

---

## Configuration

`config/settings.yaml` is the central config file. Key sections:

| Section | Purpose |
|---|---|
| `models` | Four model tiers: `local_router`, `local_code`, `premium`, `prompt_polisher` |
| `routing` | Complexity thresholds for model routing |
| `execution` | Timeout, retries, artifact/log directories, command allowlist |
| `mcp_server` | Host, port, transport for the MCP server |
| `openclaw` | OpenClaw skill registration settings |
| `agents` | Agent CLI detection, fallback order, overrides |

---

## Agent CLI Detection

ClawSmith auto-detects installed coding agent CLIs:

```bash
clawsmith detect-agents
```

| Agent | Executable | How to install |
|---|---|---|
| Cursor | `cursor` | [cursor.sh](https://cursor.sh) |
| Claude Code | `claude` | `npm install -g @anthropic-ai/claude-code` |
| Gemini CLI | `gemini` | Google's official distribution |
| OpenClaw | `openclaw` | Configure in `config/settings.yaml` |

---

## Verification

Run the doctor to verify everything:

```bash
clawsmith doctor
```

The doctor reports PASS / WARN / FAIL for each check and gives an overall health status (HEALTHY, DEGRADED, or BLOCKED). The output is designed for copy-paste into GitHub issues.

---

## Troubleshooting

See [docs/troubleshooting.md](docs/troubleshooting.md) for solutions to common issues.
